"""Desktop entry point: open a browser, reuse the running app, and retain data."""

import argparse
from http.client import HTTPConnection, HTTPException
import json
import logging
import os
from pathlib import Path
import sys
import webbrowser

from .web import serve


def data_directory():
    return Path.home() / "Downloads" / "Flow"


def running(port):
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        if response.status != 200:
            return False
        data = json.loads(response.read(4096))
        return isinstance(data, dict) and data.get("app") == "flow" and data.get("status") == "ok"
    except (OSError, ValueError, HTTPException):
        return False
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open the Flow download manager")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--directory", type=Path, default=data_directory())
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=3)
    parser.add_argument("--no-browser", action="store_true", help="start without opening a browser")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if running(args.port):
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        return 0
    try:
        result = serve(["--directory", str(args.directory), "--port", str(args.port), "--workers", str(args.workers)], open_browser=not args.no_browser)
        if result:
            raise OSError("Flow could not start. Another application may be using its port, or the download directory may be unavailable.")
        return 0
    except Exception as error:
        # pythonw has no console. Keep a readable error and show a native dialog.
        if sys.platform == "win32":
            import ctypes

            root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Flow"
            root.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(filename=root / "flow.log", level=logging.ERROR)
            logging.exception("Flow could not start")
            ctypes.windll.user32.MessageBoxW(None, f"{error}\n\nDetails: {root / 'flow.log'}", "Flow", 0x10)
        elif sys.stderr:
            print(f"Cannot start Flow: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
