"""Local-only dashboard and JSON API; no third-party web framework required."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import re
import shutil
import signal
import threading
import webbrowser
from urllib.parse import quote, urlsplit

from . import __version__
from .manager import DownloadManager


class DashboardServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address, manager):
        self.manager = manager
        super().__init__(address, DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "IDM"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        # URLs can include signed query parameters. Do not log requests.
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        super().end_headers()

    def _trusted(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            self._json({"error": "Invalid local host"}, 403)
            return False
        origin = self.headers.get("Origin")
        if (origin is not None and origin not in {f"http://{host}" for host in hosts}) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._json({"error": "Cross-origin requests are not allowed"}, 403)
            return False
        return True

    def _bytes(self, data, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, data, status=200):
        self._bytes(json.dumps(data).encode(), "application/json; charset=utf-8", status)

    def do_GET(self):
        if not self._trusted():
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._json({"status": "ok", "app": "flow", "version": __version__})
        elif path == "/api/jobs":
            self._json(self.server.manager.snapshot())
        elif match := re.fullmatch(r"/api/jobs/([0-9a-f]{32})/file", path):
            try:
                target, name = self.server.manager.file(match[1])
                with target.open("rb") as source:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name, safe='')}")
                    self.send_header("Content-Length", str(target.stat().st_size))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    shutil.copyfileobj(source, self.wfile)
            except (KeyError, FileNotFoundError):
                self._json({"error": "Downloaded file not found"}, 404)
            except ValueError as error:
                self._json({"error": str(error)}, 409)
        else:
            assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"), "/flow.svg": ("flow.svg", "image/svg+xml")}
            if path not in assets:
                self._json({"error": "Not found"}, 404)
                return
            name, content_type = assets[path]
            self._bytes(files("idm").joinpath("static", name).read_bytes(), content_type)

    def do_POST(self):
        if not self._trusted():
            return
        if self.headers.get_content_type() != "application/json":
            self._json({"error": "Use application/json"}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Request body must be between 1 and 65536 bytes")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object")
        except (ValueError, UnicodeError) as error:
            self._json({"error": str(error)}, 400)
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/shutdown":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif path == "/api/jobs":
                jobs = self.server.manager.add(body.get("urls"))
                self._json({"jobs": jobs}, 201)
            elif match := re.fullmatch(r"/api/jobs/([0-9a-f]{32})/(pause|resume|cancel|retry|remove)", path):
                self.server.manager.action(match[1], match[2])
                self._json({"ok": True})
            elif path in {"/api/actions/pause-all", "/api/actions/resume-all"}:
                pause = path.endswith("pause-all")
                for job in self.server.manager.snapshot()["jobs"]:
                    if job["state"] in ({"downloading", "queued"} if pause else {"paused"}):
                        try:
                            self.server.manager.action(job["id"], "pause" if pause else "resume")
                        except (ValueError, KeyError):
                            pass  # A transfer may finish while the snapshot is processed.
                self._json({"ok": True})
            else:
                self._json({"error": "Not found"}, 404)
        except KeyError:
            self._json({"error": "Download not found"}, 404)
        except ValueError as error:
            self._json({"error": str(error)}, 409)


def serve(argv=None, *, open_browser=False):
    parser = argparse.ArgumentParser(description="Start the local download dashboard")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--directory", type=Path, default=Path("downloads"), help="directory for files and persistent history")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=3, help="concurrent downloads (1–8)")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        manager = DownloadManager(args.directory, args.workers)
    except (OSError, ValueError) as error:
        print(f"Cannot open download directory: {error}")
        return 1
    try:
        with DashboardServer(("127.0.0.1", args.port), manager) as server:
            print(f"Download dashboard listening on 127.0.0.1:{server.server_port}", flush=True)
            print(f"Files and history: {manager.directory}", flush=True)
            print("Press Ctrl+C to stop; unfinished downloads will be paused.", flush=True)
            if open_browser:
                try:
                    webbrowser.open(f"http://127.0.0.1:{server.server_port}")
                except (webbrowser.Error, OSError):
                    pass  # The printed address still permits manual access.
            old_signal = None
            if threading.current_thread() is threading.main_thread():
                def stop(*_):
                    raise KeyboardInterrupt

                old_signal = signal.signal(signal.SIGTERM, stop)
            try:
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
            finally:
                if old_signal is not None:
                    signal.signal(signal.SIGTERM, old_signal)
                # Keep the port bound until transfers and SQLite handles close.
                # Installers can then wait for port release before relaunching.
                manager.close()
    except OSError as error:
        print(f"Cannot start dashboard: {error}")
        return 1
    finally:
        manager.close()
    return 0
