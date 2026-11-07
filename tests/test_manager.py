from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from idm.manager import DownloadManager

PAYLOAD = bytes(range(256)) * 8192
CHANGED = bytes(reversed(range(256))) * 8192


class RangeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/folder/":
            body = b'<h1>Index of folder</h1><a href="setup.exe">Setup</a><a href="archive.zip">Archive</a>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/missing" or (self.path == "/flaky" and self.server.fail):
            self.send_error(404)
            return
        if self.path == "/unsafe-redirect":
            self.send_response(302)
            self.send_header("Location", "ftp://127.0.0.1:9/file")
            self.end_headers()
            return
        body = CHANGED if self.server.changed else PAYLOAD
        etag = '"v2"' if self.server.changed else '"v1"'
        offset = 0
        range_header = self.headers.get("Range")
        self.server.ranges.append((self.path, range_header))
        if range_header and self.path != "/no-range" and self.headers.get("If-Range") == etag:
            offset = int(range_header.removeprefix("bytes=").removesuffix("-"))
        self.send_response(206 if offset else 200)
        if self.path != "/no-validator":
            self.send_header("ETag", etag)
        if offset:
            start = 0 if self.path == "/wrong-range" else offset
            self.send_header("Content-Range", f"bytes {start}-{len(body)-1}/{len(body)}")
        self.send_header("Content-Length", str(len(body) - offset))
        self.end_headers()
        with self.server.activity_lock:
            self.server.active += 1
            self.server.max_active = max(self.server.max_active, self.server.active)
        try:
            for index in range(offset, len(body), 16384):
                self.wfile.write(body[index:index + 16384])
                self.wfile.flush()
                time.sleep(getattr(self.server, "delay", 0.003))
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with self.server.activity_lock:
                self.server.active -= 1


def fixture_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    server.daemon_threads = True
    server.fail = True
    server.changed = False
    server.ranges = []
    server.activity_lock = threading.Lock()
    server.active = 0
    server.max_active = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def wait_for(manager, key, predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = next(j for j in manager.snapshot()["jobs"] if j["id"] == key)
        if predicate(job):
            return job
        time.sleep(0.01)
    raise AssertionError(f"Download did not reach expected state: {job}")


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.proxy = patch.dict(os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"})
        self.proxy.start()
        self.directory = tempfile.TemporaryDirectory()
        self.server, self.thread = fixture_server()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.manager = DownloadManager(self.directory.name, workers=2)

    def tearDown(self):
        self.manager.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()
        self.proxy.stop()

    def add(self, route="/range"):
        return self.manager.add([self.base + route])[0]["id"]

    def pause(self, key):
        wait_for(self.manager, key, lambda j: j["received"] >= 32768 and j["state"] == "downloading")
        self.manager.action(key, "pause")
        return wait_for(self.manager, key, lambda j: j["state"] == "paused")

    def complete(self, key):
        return wait_for(self.manager, key, lambda j: j["state"] == "completed")

    def test_pause_resume_uses_range_and_preserves_exact_bytes(self):
        key = self.add()
        paused = self.pause(key)
        self.assertGreater(paused["received"], 0)
        self.manager.action(key, "resume")
        completed = self.complete(key)
        self.assertEqual(self.manager.path(completed).read_bytes(), PAYLOAD)
        self.assertTrue(any(header and not header.startswith("bytes=0-") for _, header in self.server.ranges))
        self.assertEqual(len(completed["sha256"]), 64)
        self.assertFalse(list(self.manager.path(completed).parent.glob("*.part")))

    def test_source_change_restarts_instead_of_mixing_versions(self):
        key = self.add()
        self.pause(key)
        self.server.changed = True
        self.manager.action(key, "resume")
        completed = self.complete(key)
        self.assertEqual(self.manager.path(completed).read_bytes(), CHANGED)

    def test_server_ignoring_ranges_restarts_safely(self):
        key = self.add("/no-range")
        self.pause(key)
        self.manager.action(key, "resume")
        self.assertEqual(self.manager.path(self.complete(key)).read_bytes(), PAYLOAD)

    def test_no_validator_restarts_without_requesting_range(self):
        key = self.add("/no-validator")
        self.pause(key)
        self.manager.action(key, "resume")
        self.assertEqual(self.manager.path(self.complete(key)).read_bytes(), PAYLOAD)
        self.assertTrue(all(header is None for _, header in self.server.ranges))

    def test_bad_range_is_rejected_and_retry_starts_clean(self):
        key = self.add("/wrong-range")
        self.pause(key)
        self.manager.action(key, "resume")
        failed = wait_for(self.manager, key, lambda j: j["state"] == "failed")
        self.assertIn("range", failed["error"])
        self.manager.action(key, "retry")
        self.assertEqual(self.manager.path(self.complete(key)).read_bytes(), PAYLOAD)

    def test_shutdown_persists_paused_job_and_resume_after_restart(self):
        key = self.add()
        wait_for(self.manager, key, lambda j: j["received"] >= 32768)
        self.manager.close()
        self.manager = DownloadManager(self.directory.name, workers=2)
        job = self.manager.snapshot()["jobs"][0]
        self.assertEqual(job["state"], "paused")
        self.manager.action(key, "resume")
        self.assertEqual(self.manager.path(self.complete(key)).read_bytes(), PAYLOAD)

    def test_concurrency_is_bounded_and_duplicate_names_have_distinct_paths(self):
        keys = [j["id"] for j in self.manager.add([self.base + "/range"] * 4)]
        paths = [self.manager.path(self.complete(key)) for key in keys]
        self.assertEqual(len(set(paths)), 4)
        self.assertEqual(self.server.max_active, 2)
        for path in paths:
            self.assertEqual(path.read_bytes(), PAYLOAD)

    def test_cancel_deletes_partial_file_and_retry_is_possible(self):
        key = self.add()
        self.pause(key)
        self.manager.action(key, "cancel")
        cancelled = wait_for(self.manager, key, lambda j: j["state"] == "cancelled")
        self.assertFalse(self.manager.path(cancelled).parent.exists())
        self.manager.action(key, "retry")
        self.assertEqual(self.manager.path(self.complete(key)).read_bytes(), PAYLOAD)

    def test_failed_download_can_be_retried(self):
        key = self.add("/flaky")
        wait_for(self.manager, key, lambda j: j["state"] == "failed")
        self.server.fail = False
        self.manager.action(key, "retry")
        self.complete(key)

    def test_cancelling_active_transfer_removes_its_partial_data(self):
        key = self.add()
        wait_for(self.manager, key, lambda j: j["received"] >= 32768)
        self.manager.action(key, "cancel")
        job = wait_for(self.manager, key, lambda j: j["state"] == "cancelled")
        self.assertFalse(self.manager.path(job).parent.exists())

    def test_interrupted_cancel_is_recovered_on_restart(self):
        key = self.add()
        self.pause(key)
        job = self.manager.snapshot()["jobs"][0]
        self.manager.close()
        import json
        import sqlite3

        job["state"] = "cancelling"
        db = sqlite3.connect(Path(self.directory.name) / "history.sqlite3")
        try:
            with db:
                db.execute("UPDATE jobs SET data = ? WHERE id = ?", (json.dumps(job), key))
        finally:
            db.close()
        self.manager = DownloadManager(self.directory.name)
        recovered = self.manager.snapshot()["jobs"][0]
        self.assertEqual(recovered["state"], "cancelled")
        self.assertFalse(self.manager.path(recovered).parent.exists())

    def test_remove_completed_file_and_history(self):
        key = self.add()
        job = self.complete(key)
        target = self.manager.path(job)
        self.manager.action(key, "remove")
        self.assertFalse(target.exists())
        self.assertEqual(self.manager.snapshot()["jobs"], [])

    def test_invalid_batch_does_not_add_any_jobs(self):
        for urls in [None, [], [self.base + "/range", "file:///etc/passwd"], ["https://user:pass@example.com/a"]]:
            with self.subTest(urls=urls), self.assertRaises(ValueError):
                self.manager.add(urls)
        self.assertEqual(self.manager.snapshot()["jobs"], [])

    def test_only_one_manager_can_use_a_directory(self):
        with self.assertRaisesRegex(ValueError, "Another download manager"):
            DownloadManager(self.directory.name)

    def test_unsafe_redirect_is_a_failed_download(self):
        key = self.add("/unsafe-redirect")
        job = wait_for(self.manager, key, lambda j: j["state"] == "failed")
        self.assertIn("http", job["error"])

    def test_missing_completed_file_is_reported_after_restart(self):
        key = self.add()
        job = self.complete(key)
        self.manager.path(job).unlink()
        self.manager.close()
        self.manager = DownloadManager(self.directory.name)
        self.assertEqual(self.manager.snapshot()["jobs"][0]["state"], "failed")
