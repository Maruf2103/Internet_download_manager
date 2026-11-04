"""Persistent download queue with a bounded pool of cooperative workers."""

from copy import deepcopy
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
import json
import shutil
import sqlite3
import threading
import time
import uuid

from .download import DownloadError, filename_from_url, validate_url
from .transfer import TransferStopped, transfer


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
        self._jobs = {key: json.loads(data) for key, data in self._db.execute("SELECT id, data FROM jobs")}
        for job in self._jobs.values():
            if job["state"] == "cancelling":
                job.update(state="cancelled", received=0, total=None, speed=0)
                shutil.rmtree(self.path(job).parent, ignore_errors=True)
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
        return self.directory / job["id"] / job["name"]

    def _save(self, job):
        job["updated_at"] = now()
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (job["id"], json.dumps(job)))

    def snapshot(self):
        with self._condition:
            return {"jobs": deepcopy(sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)), "workers": self.workers}

    def add(self, urls):
        if not isinstance(urls, list) or not 1 <= len(urls) <= 50:
            raise ValueError("Add between 1 and 50 URLs at a time")
        for url in urls:
            validate_url(url)
        with self._condition:
            if self._closed:
                raise ValueError("The download manager is shutting down")
            if len(self._jobs) + len(urls) > 1000:
                raise ValueError("History is full; remove old downloads before adding more")
            jobs = []
            for url in urls:
                name = filename_from_url(url).encode("utf-8")[:180].decode("utf-8", errors="ignore")
                job = dict(id=uuid.uuid4().hex, url=url, name=name, state="queued", received=0, total=None, speed=0, sha256=None, error=None, created_at=now(), updated_at=now())
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
                    shutil.rmtree(self.path(job).parent, ignore_errors=True)
                    job.update(received=0, total=None)
            elif action == "remove" and state in {"completed", "cancelled", "failed", "paused"}:
                shutil.rmtree(self.path(job).parent, ignore_errors=True)
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
            try:
                result = transfer(job["url"], self.path(job), stopped=event.is_set, progress=progress)
            except TransferStopped:
                pass
            except (OSError, ValueError, DownloadError, HTTPException) as failure:
                error = str(failure)
            with self._condition:
                self._events.pop(job["id"], None)
                if job["state"] == "cancelling":
                    shutil.rmtree(self.path(job).parent, ignore_errors=True)
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
