"""Build an offline Windows x64 installer ZIP with a verified CPython runtime."""

import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import sys
import tempfile
from urllib.request import urlopen
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from idm import __version__


def runtime_package(cache):
    spec = json.loads((ROOT / "packaging/windows/runtime.json").read_text())
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"python.{spec['version']}.nupkg"
    expected = base64.b64decode(spec["sha512_base64"], validate=True)

    def valid(path):
        if not path.exists():
            return False
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha512").digest() == expected

    if not valid(target):
        temporary = target.with_suffix(".download")
        try:
            with urlopen(spec["url"], timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if not valid(temporary):
                raise ValueError("Official Windows runtime SHA-512 verification failed")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"Verified official Python {spec['version']} Windows x64 runtime (SHA-512).")
    return target, spec


def safe_relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError("Unsafe archive entry")
    return path


def write_icon(target):
    # Author the small application mark directly; no image/build dependency.
    width, scale = 64, 4
    segments = [(32, 14, 32, 40), (22, 30, 32, 40), (32, 40, 42, 30), (18, 45, 18, 51), (18, 51, 46, 51), (46, 51, 46, 45)]

    def on_line(x, y, line):
        a, b, c, d = line
        t = max(0, min(1, ((x-a)*(c-a) + (y-b)*(d-b)) / ((c-a)**2 + (d-b)**2)))
        return (x-a-t*(c-a))**2 + (y-b-t*(d-b))**2 <= 4

    rows = bytearray()
    for y in range(width):
        rows.append(0)
        for x in range(width):
            values = [0, 0, 0, 0]
            for sy in range(scale):
                for sx in range(scale):
                    px, py = x + (sx+.5)/scale, y + (sy+.5)/scale
                    cx, cy = max(17, min(47, px)), max(17, min(47, py))
                    if (px-cx)**2 + (py-cy)**2 > 17**2:
                        color = (0, 0, 0, 0)
                    elif any(on_line(px, py, line) for line in segments):
                        color = (255, 255, 255, 255)
                    else:
                        color = (8, 127, 91, 255)
                    values = [a+b for a, b in zip(values, color)]
            rows.extend(round(value / scale**2) for value in values)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    target.write_bytes(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", width, width, 0, 0, 1, 32, len(png), 22) + png)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / "build/runtime-cache")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    wheel = ROOT / "dist" / f"internet_download_manager-{__version__}-py3-none-any.whl"
    if not wheel.exists():
        raise SystemExit("Build the current wheel first: python -m pip wheel --no-build-isolation --no-deps . --wheel-dir dist")
    runtime, spec = runtime_package(args.cache)
    args.output.mkdir(parents=True, exist_ok=True)
    name = f"Flow-{__version__}-Windows-x64"
    output = args.output / f"{name}.zip"
    with tempfile.TemporaryDirectory(prefix="flow-windows-") as folder:
        stage = Path(folder)
        with zipfile.ZipFile(runtime) as package:
            for entry in package.infolist():
                path = safe_relative(entry.filename)
                if entry.is_dir() or not path.parts or path.parts[0] != "tools":
                    continue
                destination = stage / "runtime" / Path(*path.parts[1:])
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, destination.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
        with zipfile.ZipFile(wheel) as package:
            for entry in package.infolist():
                path = safe_relative(entry.filename)
                if entry.is_dir():
                    continue
                destination = stage / "app" / Path(*path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(package.read(entry))
        for asset in (ROOT / "packaging/windows").iterdir():
            if asset.is_file() and asset.name != "runtime.json":
                shutil.copy2(asset, stage / asset.name)
        write_icon(stage / "flow.ico")
        shutil.copytree(ROOT / "idm/chrome_extension", stage / "ChromeExtension")
        assert json.loads((stage / "ChromeExtension/manifest.json").read_text())["version"] == __version__
        shutil.copy2(ROOT / "docs/WINDOWS-SETUP.md", stage / "WINDOWS-SETUP.md")
        shutil.copy2(ROOT / "examples/folder-manifest.json", stage / "folder-manifest-example.json")
        from idm.chrome_host import extension_id
        (stage / "CHROME-SETUP.txt").write_text(f"Flow Chrome integration\n\n1. Run Install Flow.cmd, then restart Chrome.\n2. Open chrome://extensions and enable Developer mode.\n3. Click Load unpacked and select the installed ChromeExtension folder.\n   Default: %LOCALAPPDATA%\\Programs\\Flow\\{__version__}\\ChromeExtension\n4. Pin Flow to the toolbar. Open its popup; it should say the bridge is ready.\n5. Right-click a file link and choose Download link with Flow, or paste links in the popup.\n\nExtension ID: {extension_id()}\n\nAutomatic Chrome handoff is optional and off by default. Public HTTP(S) files\nwork directly. Login-protected and DRM downloads may need to stay in Chrome.\nNo cookies are collected. Multiple Wi-Fi bonding is not provided.\n", encoding="utf-8")
        (stage / "runtime-source.json").write_text(json.dumps(spec, indent=2))
        required = ["runtime/python.exe", "runtime/pythonw.exe", "runtime/python313.dll", "runtime/DLLs/_sqlite3.pyd", "runtime/DLLs/_ssl.pyd", "runtime/Lib/ssl.py", "app/idm/static/index.html", "app/idm/static/flow.svg", "app/idm/chrome_extension/manifest.json", "ChromeExtension/manifest.json", "native-host.cmd", "native_host.py"]
        for file in required:
            if not (stage / file).is_file():
                raise ValueError(f"Runtime/application component missing: {file}")
        payload = {path.relative_to(stage).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(stage.rglob("*")) if path.is_file()}
        manifest = {"application": "io.github.maruf2103.flow", "version": __version__, "platform": "windows-x64", "files": payload}
        (stage / "payload.json").write_text(json.dumps(manifest, indent=2))
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    entry = zipfile.ZipInfo(f"{name}/{path.relative_to(stage).as_posix()}", date_time=(2020, 1, 1, 0, 0, 0))
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = 0o644 << 16
                    archive.writestr(entry, path.read_bytes())
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n")
    print(f"Built {output} ({output.stat().st_size / 1024**2:.1f} MB), {len(payload)} verified payload files.")
    print("Native Windows execution must be checked on a Windows runner.")


if __name__ == "__main__":
    main()
