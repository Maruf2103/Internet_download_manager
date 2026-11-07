"""Persistent download queue with a bounded pool of cooperative workers."""

from copy import deepcopy
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid

from .download import DownloadError, filename_from_url, validate_url, safe_name, response_filename
from .folders import relative_path
from .transfer import TransferStopped
from .accelerator import accelerated_transfer


def now():
    return datetime.now(timezone.utc).isoformat()


class DownloadManager:
    def __init__(self, directory, workers=3):
        if not 1 <= workers <= 8:
            raise ValueError("Workers must be between 1 and 8")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._instance = sqlite3.connect(self.directory / ".instance.sqlite3", timeout=0, check_same_thread=False)
        try:
            self._instance.execute("CREATE TABLE IF NOT EXISTS instance_lock (id INTEGER)")
            self._instance.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as error:
            self._instance.close()
            raise ValueError("Another download manager is using this directory") from error
        self.workers = workers
        self._condition = threading.Condition(threading.RLock())
        self._closed = False
        self._events = {}
        self._db = sqlite3.connect(self.directory / "history.sqlite3", check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        setting = self._db.execute("SELECT value FROM settings WHERE key = 'connections'").fetchone()
        self.connections = int(setting[0]) if setting else 8
        self._jobs = {key: json.loads(data) for key, data in self._db.execute("SELECT id, data FROM jobs")}
        for job in self._jobs.values():
            if job["state"] == "cancelling":
                job.update(state="cancelled", received=0, total=None, speed=0)
                self._cleanup(job)
                self._save(job)
            if job["state"] in {"queued", "downloading", "pausing"}:
                job.update(state="paused", speed=0, error="Paused after application restart")
                self._save(job)
            if job["state"] == "completed" and not self.path(job).is_file():
                job.update(state="failed", error="The downloaded file is missing; retry to download it again")
                self._save(job)
        self._threads = [threading.Thread(target=self._worker, name=f"idm-worker-{n}", daemon=True) for n in range(workers)]
        for thread in self._threads:
            thread.start()

    def path(self, job):
        if job.get("folder_group"):
            return self.directory / "folders" / job["folder_group"] / job["relative_path"]
        return self.directory / job["id"] / job["name"]

    def _cleanup(self, job):
        path = self.path(job)
        if job.get("folder_group"):
            path.unlink(missing_ok=True)
            shutil.rmtree(self.directory / ".transfers" / job["id"], ignore_errors=True)
        else:
            shutil.rmtree(self.directory / job["id"], ignore_errors=True)
        parent = path.parent
        while parent != self.directory and parent.is_relative_to(self.directory):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def _staging(self, job):
        if job.get("folder_group"):
            return self.directory / ".transfers" / job["id"] / job["name"]
        return self.path(job)

    def _save(self, job):
        job["updated_at"] = now()
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (job["id"], json.dumps(job)))

    def snapshot(self):
        with self._condition:
            return {"jobs": deepcopy(sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)), "workers": self.workers, "connections": self.connections}

    def configure(self, connections):
        if type(connections) is not int or connections not in {1, 2, 4, 8, 16}:
            raise ValueError("Connections must be 1, 2, 4, 8, or 16")
        with self._condition:
            self.connections = connections
            with self._db:
                self._db.execute("INSERT OR REPLACE INTO settings VALUES ('connections', ?)", (str(connections),))

    def add(self, urls=None, *, items=None, folder_name=None):
        if items is None:
            if not isinstance(urls, list):
                raise ValueError("Provide a list of file URLs")
            items = [{"url": url} for url in urls]
        if not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise ValueError("Add between 1 and 500 files at a time")
        prepared = []
        paths = set()
        is_folder = folder_name is not None or any(isinstance(item, dict) and item.get("path") for item in items)
        group = safe_name(folder_name or "Folder").encode("utf-8")[:60].decode("utf-8", errors="ignore") + "-" + uuid.uuid4().hex if is_folder else None
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Each manifest file must contain a URL and optional relative path")
            url = validate_url(item.get("url"))
            path = relative_path(item.get("path") or item.get("name") or filename_from_url(url)) if is_folder else None
            if path and path.casefold() in paths:
                raise ValueError("Folder paths must be unique, including on case-insensitive Windows filesystems")
            if path and any(path.casefold().startswith(p + "/") or p.startswith(path.casefold() + "/") for p in paths):
                raise ValueError("A folder path cannot also be a file")
            paths.add(path.casefold() if path else uuid.uuid4().hex)
            name = path.rsplit("/", 1)[-1] if path else safe_name(item.get("name") or filename_from_url(url))
            prepared.append((url, name, path, bool(item.get("name") or path)))
        with self._condition:
            if self._closed:
                raise ValueError("The download manager is shutting down")
            if len(self._jobs) + len(prepared) > 5000:
                raise ValueError("History is full; remove old downloads before adding more")
            jobs = []
            for url, name, path, named in prepared:
                job = dict(id=uuid.uuid4().hex, url=url, name=name, state="queued", received=0, total=None, speed=0, sha256=None, error=None, created_at=now(), updated_at=now())
                job.update(folder_group=group, folder_name=safe_name(folder_name or "Folder") if group else None, relative_path=path, named=named, transfer_mode="auto", connections=0)
                self._jobs[job["id"]] = job
                self._save(job)
                jobs.append(deepcopy(job))
            self._condition.notify_all()
            return jobs

    def action(self, key, action):
        with self._condition:
            job = self._jobs[key]
            state = job["state"]
            if action == "pause" and state in {"queued", "downloading"}:
                job["state"] = "pausing" if key in self._events else "paused"
                if key in self._events:
                    self._events[key].set()
            elif action in {"resume", "retry"} and state in {"paused", "failed", "cancelled"}:
                job.update(state="queued", error=None, speed=0)
            elif action == "cancel" and state in {"queued", "downloading", "pausing", "paused", "failed"}:
                job["state"] = "cancelling" if key in self._events else "cancelled"
                if key in self._events:
                    self._events[key].set()
                else:
                    self._cleanup(job)
                    job.update(received=0, total=None)
            elif action == "remove" and state in {"completed", "cancelled", "failed", "paused"}:
                self._cleanup(job)
                with self._db:
                    self._db.execute("DELETE FROM jobs WHERE id = ?", (key,))
                del self._jobs[key]
                return
            else:
                raise ValueError(f"Cannot {action} a {state} download")
            job["speed"] = 0
            self._save(job)
            self._condition.notify_all()

    def file(self, key):
        with self._condition:
            job = self._jobs[key]
            if job["state"] != "completed":
                raise ValueError("This download is not complete")
            return self.path(job), job["name"]

    def _worker(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or any(j["state"] == "queued" for j in self._jobs.values()))
                if self._closed:
                    return
                job = next(j for j in self._jobs.values() if j["state"] == "queued")
                event = threading.Event()
                self._events[job["id"]] = event
                job.update(state="downloading", error=None, speed=0)
                self._save(job)
            previous_time, previous_size = time.monotonic(), None
            last_saved = 0

            def progress(received, total):
                nonlocal previous_time, previous_size, last_saved
                stamp = time.monotonic()
                with self._condition:
                    job.update(received=received, total=total)
                    if previous_size is None:
                        previous_size, previous_time = received, stamp
                    elif stamp - previous_time >= 0.2:
                        job["speed"] = max(0, (received - previous_size) / (stamp - previous_time))
                        previous_size, previous_time = received, stamp
                    if stamp - last_saved > 0.5:
                        self._save(job)
                        last_saved = stamp

            result, error = None, None

            def metadata(headers):
                with self._condition:
                    path = self._staging(job)
                    artifacts = [path.with_name(path.name + suffix) for suffix in [".part", ".resume.json", ".segments"]]
                    if not job.get("named") and not any(p.exists() for p in artifacts):
                        name = response_filename(headers)
                        if name:
                            job["name"] = name
                            job["named"] = True
                            self._save(job)
                    return self._staging(job)

            def mode(kind, count):
                with self._condition:
                    job.update(transfer_mode=kind, connections=count)

            try:
                result = accelerated_transfer(job["url"], self._staging(job), stopped=event.is_set, progress=progress, connections=self.connections, on_metadata=metadata, on_mode=mode)
            except TransferStopped:
                pass
            except (OSError, ValueError, DownloadError, HTTPException) as failure:
                error = str(failure)
            with self._condition:
                self._events.pop(job["id"], None)
                if result is not None and job["state"] != "cancelling" and result.path != self.path(job):
                    try:
                        self.path(job).parent.mkdir(parents=True, exist_ok=True)
                        os.replace(result.path, self.path(job))
                        shutil.rmtree(result.path.parent, ignore_errors=True)
                    except OSError as failure:
                        result, error = None, str(failure)
                if job["state"] == "cancelling":
                    self._cleanup(job)
                    job.update(state="cancelled", received=0, total=None, sha256=None)
                elif result is not None:
                    job.update(state="completed", received=result.size, total=result.size, sha256=result.sha256, error=None)
                elif event.is_set():
                    job["state"] = "paused"
                else:
                    job.update(state="failed", error=error or "Transfer stopped unexpectedly")
                job["speed"] = 0
                self._save(job)
                self._condition.notify_all()

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            for job in self._jobs.values():
                if job["state"] == "queued":
                    job["state"] = "paused"
                    self._save(job)
            for event in self._events.values():
                event.set()
            self._condition.notify_all()
        for thread in self._threads:
            thread.join()
        self._db.close()
        self._instance.close()
