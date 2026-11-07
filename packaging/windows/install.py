"""Offline, per-user Windows installer; --check works on any build machine."""

import argparse
import base64
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import uuid

APP_ID = "io.github.maruf2103.flow"
MARKER = ".flow-install.json"


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def verify_payload(source):
    source = Path(source).resolve()
    manifest = json.loads((source / "payload.json").read_text(encoding="utf-8"))
    if manifest.get("application") != APP_ID or not manifest.get("files"):
        raise ValueError("This is not a valid Flow installation package")
    if not re.fullmatch(r"\d+\.\d+\.\d+", manifest.get("version", "")):
        raise ValueError("Invalid package version")
    for name, expected in manifest["files"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe payload path")
        target = source.joinpath(*relative.parts)
        if target.is_symlink() or not target.resolve().is_relative_to(source):
            raise ValueError("Payload contains an unexpected symbolic link")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"Package verification failed: {name}. Extract a fresh copy of the release.")
    return manifest


def install_files(source, prefix):
    source, prefix = Path(source).resolve(), Path(prefix).resolve()
    manifest = verify_payload(source)
    if prefix == source or prefix.is_relative_to(source) or source.is_relative_to(prefix):
        raise ValueError("Choose an installation directory separate from the extracted package")
    marker = prefix / MARKER
    if prefix.exists() and any(prefix.iterdir()):
        if not marker.exists() or json.loads(marker.read_text()).get("application") != APP_ID:
            raise ValueError("The installation directory contains other files; choose an empty directory")
    prefix.mkdir(parents=True, exist_ok=True)
    target = prefix / manifest["version"]
    if target.exists():
        existing = verify_payload(target)
        if existing != manifest:
            raise ValueError("A different build already uses this version directory; choose another installation directory")
    else:
        staging = prefix / (".install-" + uuid.uuid4().hex)
        try:
            staging.mkdir()
            for name in manifest["files"]:
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / name, destination)
            shutil.copy2(source / "payload.json", staging / "payload.json")
            verify_payload(staging)
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    temp_marker = prefix / (MARKER + ".tmp")
    temp_marker.write_text(json.dumps({"application": APP_ID, "version": manifest["version"], "location": str(prefix)}), encoding="utf-8")
    os.replace(temp_marker, marker)
    return target


def stop_flow():
    connection = HTTPConnection("127.0.0.1", 8765, timeout=3)
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        data = json.loads(response.read(4096))
        if response.status != 200 or not isinstance(data, dict) or data.get("app") != "flow":
            return
        connection.close()
        connection = HTTPConnection("127.0.0.1", 8765, timeout=3)
        connection.request("POST", "/api/shutdown", body="{}", headers={"Content-Type": "application/json"})
        connection.getresponse().read()
        # Wait for the server to release its files before an upgrade.
        for _ in range(150):
            probe = HTTPConnection("127.0.0.1", 8765, timeout=.1)
            try:
                probe.connect()
            except TimeoutError:
                continue
            except OSError:
                return
            finally:
                probe.close()
            time.sleep(.1)
        raise RuntimeError("Flow is still stopping. Quit Flow and run the installer again.")
    except (OSError, ValueError, HTTPException):
        return
    finally:
        connection.close()


def write_native_manifest(target):
    target = Path(target)
    extension = target / "ChromeExtension"
    key = json.loads((extension / "manifest.json").read_text())["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    extension_id = "".join(chr(ord("a") + int(char, 16)) for char in digest)
    host = target / "native-host.json"
    host.write_text(json.dumps({"name": "com.maruf2103.flow", "description": "Flow Download Manager Chrome bridge", "path": str(target / "native-host.cmd"), "type": "stdio", "allowed_origins": [f"chrome-extension://{extension_id}/"]}), encoding="utf-8")
    return host


def register_windows(target, prefix, version):
    target, prefix = Path(target), Path(prefix)
    host = write_native_manifest(target)
    interpreter = target / "runtime" / "pythonw.exe"
    uninstall = f'"{target / "runtime" / "python.exe"}" -I "{target / "uninstall.py"}" --prefix "{prefix}"'
    script = f"""
$ErrorActionPreference = 'Stop'
$flowPrefix = {ps_quote(prefix)}
$flowTarget = {ps_quote(interpreter)}
$flowShell = New-Object -ComObject WScript.Shell
$flowRegistry = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\FlowDownloadManager'
if (Test-Path $flowRegistry) {{
  $flowExisting = Get-ItemProperty $flowRegistry
  if ($flowExisting.InstallLocation -and $flowExisting.InstallLocation -ne $flowPrefix) {{ throw 'Flow is installed in another location. Uninstall that copy first.' }}
}}
foreach ($flowFolder in @([Environment]::GetFolderPath('DesktopDirectory'), [Environment]::GetFolderPath('Programs'))) {{
  if (-not $flowFolder) {{ continue }}
  [IO.Directory]::CreateDirectory($flowFolder) | Out-Null
  $flowLinkPath = Join-Path $flowFolder 'Flow Download Manager.lnk'
  $flowLink = $flowShell.CreateShortcut($flowLinkPath)
  if ($flowLink.TargetPath -and -not $flowLink.TargetPath.StartsWith($flowPrefix + '\\', [StringComparison]::OrdinalIgnoreCase)) {{ throw 'An unrelated Flow shortcut already exists.' }}
  $flowLink.TargetPath = $flowTarget
  $flowLink.Arguments = {ps_quote('-I "' + str(target / 'launch.py') + '"')}
  $flowLink.WorkingDirectory = {ps_quote(target)}
  $flowLink.IconLocation = {ps_quote(str(target / 'flow.ico') + ',0')}
  $flowLink.Description = 'Flow Download Manager'
  $flowLink.Save()
}}
New-Item $flowRegistry -Force | Out-Null
$flowProperties = @{{ DisplayName='Flow Download Manager'; DisplayVersion={ps_quote(version)}; Publisher='Flow contributors'; InstallLocation=$flowPrefix; DisplayIcon={ps_quote(target / 'flow.ico')}; UninstallString={ps_quote(uninstall)} }}
foreach ($flowKey in $flowProperties.Keys) {{ New-ItemProperty $flowRegistry -Name $flowKey -Value $flowProperties[$flowKey] -PropertyType String -Force | Out-Null }}
New-ItemProperty $flowRegistry -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty $flowRegistry -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null
"""
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], check=True)
    # Registry provider Get-Item returns a read-only .NET handle. Use the
    # standard Windows API with a writable per-user handle instead.
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\NativeMessagingHosts\com.maruf2103.flow") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(host))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--no-shortcuts", action="store_true")
    parser.add_argument("--no-launch", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    try:
        manifest = verify_payload(source)
        if args.check:
            print(f"Verified all {len(manifest['files'])} files in Flow {manifest['version']} for Windows x64.")
            return 0
        if sys.platform != "win32":
            raise ValueError("This installer targets Windows x64; --check can verify it on another platform")
        prefix = args.prefix or Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Flow"
        stop_flow()
        target = install_files(source, prefix)
        if not args.no_shortcuts:
            register_windows(target, prefix.resolve(), manifest["version"])
        print(f"Flow {manifest['version']} installed in {target}")
        print("Your downloads and history remain in your Downloads\\Flow folder.")
        print(f"Chrome extension: enable Developer mode at chrome://extensions, choose Load unpacked, and select {target / 'ChromeExtension'}")
        if not args.no_launch:
            subprocess.Popen([str(target / "runtime" / "pythonw.exe"), "-I", str(target / "launch.py")], cwd=target)
        return 0
    except subprocess.CalledProcessError:
        print("Flow's Windows shortcut/registration step failed. See the Windows error above, then retry the installer.", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Flow could not be installed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
