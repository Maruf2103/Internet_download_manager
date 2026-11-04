from http.client import HTTPConnection
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from idm.manager import DownloadManager
from idm.web import DashboardServer
from test_manager import PAYLOAD, fixture_server, wait_for


class WebTests(unittest.TestCase):
    def setUp(self):
        self.proxy = patch.dict(os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"})
        self.proxy.start()
        self.directory = tempfile.TemporaryDirectory()
        self.fixture, self.fixture_thread = fixture_server()
        self.manager = DownloadManager(self.directory.name)
        self.server = DashboardServer(("127.0.0.1", 0), self.manager)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.manager.close()
        self.fixture.shutdown()
        self.fixture.server_close()
        self.fixture_thread.join()
        self.directory.cleanup()
        self.proxy.stop()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = dict(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body)
            headers.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_dashboard_assets_and_health_are_served(self):
        for path, expected in [("/", b"Flow"), ("/app.js", b"refresh"), ("/style.css", b"sidebar"), ("/api/health", b'"status": "ok"')]:
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(expected, body)
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_add_list_complete_save_and_remove(self):
        url = f"http://127.0.0.1:{self.fixture.server_port}/range"
        status, _, body = self.request("POST", "/api/jobs", {"urls": [url]})
        self.assertEqual(status, 201)
        key = json.loads(body)["jobs"][0]["id"]
        wait_for(self.manager, key, lambda job: job["state"] == "completed")
        status, headers, data = self.request("GET", f"/api/jobs/{key}/file")
        self.assertEqual(status, 200)
        self.assertEqual(data, PAYLOAD)
        self.assertIn("attachment", headers["Content-Disposition"])
        status, _, body = self.request("GET", "/api/jobs")
        self.assertEqual(json.loads(body)["jobs"][0]["state"], "completed")
        status, _, _ = self.request("POST", f"/api/jobs/{key}/remove", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.manager.snapshot()["jobs"], [])

    def test_foreign_origin_host_and_cross_site_requests_are_rejected(self):
        for headers in [{"Origin": "https://evil.example"}, {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}]:
            with self.subTest(headers=headers):
                status, _, _ = self.request("POST", "/api/jobs", {"urls": ["https://example.com/file"]}, headers)
                self.assertEqual(status, 403)
        self.assertEqual(self.manager.snapshot()["jobs"], [])

    def test_malformed_requests_and_traversal_are_rejected(self):
        self.assertEqual(self.request("POST", "/api/jobs", "{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/jobs", "not-json", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/jobs", {"urls": ["file:///etc/passwd"]})[0], 409)
        self.assertEqual(self.request("GET", "/../../pyproject.toml")[0], 404)
        self.assertEqual(self.request("GET", "/history.sqlite3")[0], 404)
        self.assertEqual(self.request("POST", "/api/jobs/" + "0" * 32 + "/pause", {})[0], 404)

    def test_bulk_pause_and_resume(self):
        jobs = self.manager.add([f"http://127.0.0.1:{self.fixture.server_port}/range"] * 4)
        self.assertEqual(self.request("POST", "/api/actions/pause-all", {})[0], 200)
        for job in jobs:
            wait_for(self.manager, job["id"], lambda j: j["state"] == "paused")
        self.assertEqual(self.request("POST", "/api/actions/resume-all", {})[0], 200)
        for job in jobs:
            wait_for(self.manager, job["id"], lambda j: j["state"] == "completed")
