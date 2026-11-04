import hashlib
from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("flow_windows_install", ROOT / "packaging/windows/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "release"
        self.source.mkdir()
        (self.source / "launch.py").write_text("print('Flow')")
        self.manifest = {"application": installer.APP_ID, "version": "1.1.0", "files": {"launch.py": hashlib.sha256((self.source / "launch.py").read_bytes()).hexdigest()}}
        (self.source / "payload.json").write_text(json.dumps(self.manifest))

    def test_install_and_repeat_leave_downloads_outside_prefix_untouched(self):
        downloads = self.root / "Downloads" / "Flow"
        downloads.mkdir(parents=True)
        (downloads / "personal.txt").write_text("keep me")
        prefix = self.root / "Programs" / "Flow"
        target = installer.install_files(self.source, prefix)
        self.assertEqual((target / "launch.py").read_text(), "print('Flow')")
        self.assertEqual(installer.install_files(self.source, prefix), target)
        self.assertEqual((downloads / "personal.txt").read_text(), "keep me")
        self.assertEqual(json.loads((prefix / installer.MARKER).read_text())["application"], installer.APP_ID)

    def test_tampered_payload_is_rejected_before_installation(self):
        (self.source / "launch.py").write_text("different contents")
        prefix = self.root / "Programs" / "Flow"
        with self.assertRaisesRegex(ValueError, "verification failed"):
            installer.install_files(self.source, prefix)
        self.assertFalse(prefix.exists())

    def test_unrelated_existing_directory_is_preserved(self):
        prefix = self.root / "Documents"
        prefix.mkdir()
        (prefix / "notes.txt").write_text("user work")
        with self.assertRaisesRegex(ValueError, "other files"):
            installer.install_files(self.source, prefix)
        self.assertEqual((prefix / "notes.txt").read_text(), "user work")

    def test_unsafe_manifest_paths_and_versions_are_rejected(self):
        for name in ["../outside", "/absolute", "C:/outside", "..\\outside"]:
            manifest = dict(self.manifest, files={name: "0" * 64})
            (self.source / "payload.json").write_text(json.dumps(manifest))
            with self.subTest(name=name), self.assertRaises(ValueError):
                installer.verify_payload(self.source)
        (self.source / "payload.json").write_text(json.dumps(dict(self.manifest, version="../../outside")))
        with self.assertRaisesRegex(ValueError, "version"):
            installer.verify_payload(self.source)

    def test_modified_existing_installation_is_not_overwritten(self):
        prefix = self.root / "Programs" / "Flow"
        target = installer.install_files(self.source, prefix)
        (target / "launch.py").write_text("local customization")
        with self.assertRaisesRegex(ValueError, "verification failed"):
            installer.install_files(self.source, prefix)
        self.assertEqual((target / "launch.py").read_text(), "local customization")


class DesktopTests(unittest.TestCase):
    def test_launch_reuse_and_quit_through_local_api(self):
        with tempfile.TemporaryDirectory() as folder:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            command = [sys.executable, "-m", "idm.desktop", "--no-browser", "--port", str(port), "--directory", folder]
            process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                deadline = time.monotonic() + 10
                healthy = False
                while time.monotonic() < deadline:
                    connection = HTTPConnection("127.0.0.1", port, timeout=.3)
                    try:
                        connection.request("GET", "/api/health")
                        response = connection.getresponse()
                        data = json.loads(response.read())
                        healthy = response.status == 200 and data.get("app") == "flow"
                        if healthy:
                            break
                    except OSError:
                        time.sleep(.02)
                    finally:
                        connection.close()
                self.assertTrue(healthy, "Desktop launcher did not start")
                reused = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=5)
                self.assertEqual(reused.returncode, 0, reused.stdout + reused.stderr)
                self.assertIsNone(process.poll(), "The original desktop server should keep running")
                connection = HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("POST", "/api/shutdown", body="{}", headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                connection.close()
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
