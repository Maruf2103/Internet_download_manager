"""Chrome native messaging bridge. Standard output contains framed JSON only."""

import base64
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import webbrowser

from . import __version__

HOST_NAME = "com.maruf2103.flow"
EXTENSION = Path(__file__).resolve().parent / "chrome_extension"


def extension_id():
    key = json.loads((EXTENSION / "manifest.json").read_text())["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def request(path, body=None):
    port = int(os.environ.get("FLOW_PORT", "8765"))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid local Flow port")
    connection = HTTPConnection("127.0.0.1", port, timeout=40 if path.endswith("preview") else 8)
    try:
        connection.request("GET" if body is None else "POST", path, body=None if body is None else json.dumps(body), headers={} if body is None else {"Content-Type": "application/json"})
        response = connection.getresponse()
        data = json.loads(response.read(1024 * 1024))
        if response.status >= 400:
            raise ValueError(data.get("error", "Flow rejected the request"))
        return data
    finally:
        connection.close()


def ensure_running():
    try:
        health = request("/api/health")
        if health.get("app") == "flow" and health.get("version", "").split(".")[0] == "2":
            return
        raise ValueError("Quit the older Flow instance and start Flow 2 before using this extension")
    except OSError:
        pass
    root = Path(__file__).resolve().parents[2]
    launch = root / "launch.py"
    interpreter = Path(sys.executable)
    if sys.platform == "win32" and interpreter.with_name("pythonw.exe").exists():
        interpreter = interpreter.with_name("pythonw.exe")
    command = [str(interpreter), "-I", str(launch), "--no-browser"] if launch.exists() else [str(interpreter), "-m", "idm.desktop", "--no-browser"]
    options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(command, **options)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            if request("/api/health").get("app") == "flow":
                return
        except OSError:
            pass
        time.sleep(.15)
    raise ValueError("Flow could not start. Open Flow from the Start menu, then try again")


def handle(message):
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object")
    action = message.get("action")
    if action == "ping":
        return {"ok": True, "version": __version__, "extension_id": extension_id()}
    if action not in {"open", "queue", "folder"}:
        raise ValueError("Unsupported browser command")
    ensure_running()
    if action == "open":
        webbrowser.open(f"http://127.0.0.1:{int(os.environ.get('FLOW_PORT', '8765'))}")
        return {"ok": True}
    if action == "folder":
        folder = request("/api/folders/preview", {"url": message.get("url")})
        data = request("/api/jobs", {"items": folder["files"], "folder_name": folder["name"]})
    else:
        data = request("/api/jobs", {"urls": message.get("urls"), "items": message.get("items"), "folder_name": message.get("folder_name")})
    return {"ok": True, "count": len(data["jobs"]), "ids": [job["id"] for job in data["jobs"]]}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    origin = f"chrome-extension://{extension_id()}/"
    if not argv or argv[0] != origin:
        return 1
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    while True:
        prefix = source.read(4)
        if not prefix:
            return 0
        if len(prefix) != 4:
            return 1
        length = struct.unpack("<I", prefix)[0]
        if not 0 < length <= 1024 * 1024:
            return 1
        payload = source.read(length)
        if len(payload) != length:
            return 1
        try:
            response = handle(json.loads(payload))
        except Exception as error:
            response = {"ok": False, "error": str(error)}
        encoded = json.dumps(response).encode("utf-8")
        sink.write(struct.pack("<I", len(encoded)) + encoded)
        sink.flush()


if __name__ == "__main__":
    raise SystemExit(main())
