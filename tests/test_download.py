from contextlib import redirect_stderr, redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from idm.cli import main
from idm.download import DownloadError, download, filename_from_url

PAYLOAD = bytes(range(256)) * 600


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/missing":
            self.send_error(404)
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/file.bin")
            self.end_headers()
            return
        if self.path == "/empty":
            body = b""
        elif self.path == "/truncated":
            body = b"partial"
        else:
            body = PAYLOAD
        self.send_response(206 if self.path == "/partial" else 200)
        if self.path != "/unknown-length":
            self.send_header("Content-Length", str(len(body) + (100 if self.path == "/truncated" else 0)))
        self.end_headers()
        self.wfile.write(body)


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proxy_settings = patch.dict(os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"})
        cls.proxy_settings.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.proxy_settings.stop()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / "file.bin"

    def test_streamed_download_matches_bytes_checksum_and_progress(self):
        events = []
        result = download(self.base_url + "/file.bin", self.output, progress=lambda n, t: events.append((n, t)))
        self.assertEqual(self.output.read_bytes(), PAYLOAD)
        self.assertEqual(result.size, len(PAYLOAD))
        self.assertEqual(result.sha256, hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(events[-1], (len(PAYLOAD), len(PAYLOAD)))
        self.assertGreater(len(events), 1)
        self.assertEqual(list(self.root.iterdir()), [self.output])

    def test_follows_redirect(self):
        download(self.base_url + "/redirect", self.output)
        self.assertEqual(self.output.read_bytes(), PAYLOAD)

    def test_unknown_length(self):
        result = download(self.base_url + "/unknown-length", self.output)
        self.assertEqual(result.size, len(PAYLOAD))
        self.assertEqual(self.output.read_bytes(), PAYLOAD)

    def test_empty_file(self):
        result = download(self.base_url + "/empty", self.output)
        self.assertEqual(result.size, 0)
        self.assertEqual(self.output.read_bytes(), b"")

    def test_existing_destination_is_protected(self):
        self.output.write_bytes(b"keep me")
        with self.assertRaises(FileExistsError):
            download(self.base_url + "/file.bin", self.output)
        self.assertEqual(self.output.read_bytes(), b"keep me")

    def test_successful_overwrite(self):
        self.output.write_bytes(b"old")
        download(self.base_url + "/file.bin", self.output, overwrite=True)
        self.assertEqual(self.output.read_bytes(), PAYLOAD)

    def test_failed_overwrite_retains_original_and_cleans_temporary_file(self):
        self.output.write_bytes(b"keep me")
        with self.assertRaises(DownloadError):
            download(self.base_url + "/truncated", self.output, overwrite=True)
        self.assertEqual(self.output.read_bytes(), b"keep me")
        self.assertEqual(list(self.root.iterdir()), [self.output])

    def test_unrequested_partial_response_is_rejected(self):
        with self.assertRaises(DownloadError):
            download(self.base_url + "/partial", self.output)
        self.assertFalse(self.output.exists())

    def test_cancellation_removes_incomplete_download(self):
        def cancel(size, total):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            download(self.base_url + "/file.bin", self.output, progress=cancel)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_destination_created_during_download_is_not_overwritten(self):
        def create_destination(size, total):
            self.output.write_bytes(b"another process")

        with self.assertRaises(FileExistsError):
            download(self.base_url + "/file.bin", self.output, progress=create_destination)
        self.assertEqual(self.output.read_bytes(), b"another process")
        self.assertEqual(list(self.root.iterdir()), [self.output])

    def test_unsupported_url_and_embedded_credentials_rejected(self):
        for url in ["file:///etc/passwd", "ftp://example.com/a", "not-a-url", "https://user:password@example.com/a"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                download(url, self.output)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_continues_after_failure_and_reports_nonzero_exit(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main([self.base_url + "/missing", self.base_url + "/file.bin", "-d", str(self.root)])
        self.assertEqual(status, 1)
        self.assertIn("404", errors.getvalue())
        self.assertIn("SHA-256", output.getvalue())
        self.assertEqual(self.output.read_bytes(), PAYLOAD)
        self.assertFalse((self.root / "missing").exists())

    def test_cli_success_creates_nested_output_directory(self):
        target = self.root / "nested" / "saved.bin"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = main([self.base_url + "/file.bin", "-o", str(target)])
        self.assertEqual(status, 0)
        self.assertEqual(target.read_bytes(), PAYLOAD)

    def test_cli_rejects_ambiguous_output_and_invalid_timeouts(self):
        invalid_arguments = [
            [self.base_url, self.base_url, "-o", str(self.output)],
            *[[self.base_url, "--timeout", timeout] for timeout in ["0", "-1", "nan", "inf", "oops"]],
        ]
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                main(arguments)
            self.assertEqual(result.exception.code, 2)

    def test_url_filename_cannot_escape_output_directory(self):
        for url, expected in [
            ("https://example.com/a.zip?token=x", "a.zip"),
            ("https://example.com/%2Ftmp%2Fsafe.zip", "safe.zip"),
            ("https://example.com/..%5Cfile.zip", "file.zip"),
            ("https://example.com/%00bad%0A.zip", "bad.zip"),
            ("https://example.com/..", "download"),
            ("https://example.com/", "download"),
        ]:
            with self.subTest(url=url):
                self.assertEqual(filename_from_url(url), expected)


if __name__ == "__main__":
    unittest.main()
