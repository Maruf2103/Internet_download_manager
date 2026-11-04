"""Verify release integrity and real installer file operations without Windows.

This does not substitute for the native execution check in Windows CI.
"""

import hashlib
import importlib.util
from pathlib import Path, PurePosixPath
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from idm import __version__


def main():
    release = ROOT / "dist" / f"Flow-{__version__}-Windows-x64.zip"
    expected = release.with_suffix(".zip.sha256").read_text().split()[0]
    assert hashlib.sha256(release.read_bytes()).hexdigest() == expected, "Release ZIP checksum mismatch"
    with tempfile.TemporaryDirectory(prefix="flow-install-check-") as folder:
        root = Path(folder)
        with zipfile.ZipFile(release) as archive:
            for entry in archive.infolist():
                path = PurePosixPath(entry.filename)
                assert not path.is_absolute() and ".." not in path.parts and "\\" not in entry.filename
                assert (entry.external_attr >> 16) & 0o170000 != 0o120000, "Unexpected archive symlink"
            archive.extractall(root / "extracted")
        source = next((root / "extracted").iterdir())
        subprocess.run([sys.executable, "-I", str(source / "install.py"), "--check"], check=True)
        for name in ["python.exe", "pythonw.exe", "python313.dll"]:
            with (source / "runtime" / name).open("rb") as binary:
                header = binary.read(64)
                assert header[:2] == b"MZ"
                binary.seek(struct.unpack_from("<I", header, 60)[0])
                signature, architecture = struct.unpack("<4sH", binary.read(6))
                assert signature == b"PE\0\0" and architecture == 0x8664, "Runtime is not Windows x64"
        assert (source / "runtime/vcruntime140.dll").exists(), "Private Visual C runtime missing"
        spec = importlib.util.spec_from_file_location("flow_release_installer", source / "install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        prefix = root / "Local AppData" / "Programs" / "Flow"
        target = installer.install_files(source, prefix)
        assert installer.install_files(source, prefix) == target
        installer.verify_payload(target)
        assert (target / "app/idm/static/index.html").is_file()
        assert (target / "flow.ico").is_file()
        print("Windows package verified: ZIP SHA-256, every payload file, x64 PE headers, private C runtime, full installation, and repeat installation.")
        print("Windows execution and shell integration still require the native Windows CI check.")


if __name__ == "__main__":
    main()
