"""Remove the per-user application while retaining downloaded files and history."""

import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import os

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install import APP_ID, MARKER, ps_quote, stop_flow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    prefix = args.prefix.resolve()
    marker = json.loads((prefix / MARKER).read_text())
    if marker.get("application") != APP_ID or Path(marker["location"]).resolve() != prefix or not Path(__file__).resolve().is_relative_to(prefix):
        raise ValueError("Cannot verify this Flow installation")
    if sys.platform != "win32":
        raise ValueError("This uninstaller targets Windows")
    if not args.yes and ctypes.windll.user32.MessageBoxW(None, "Uninstall Flow Download Manager?\n\nYour downloaded files and history in Downloads\\Flow will be kept.", "Uninstall Flow", 0x24) != 6:
        return 0
    stop_flow()
    # The bundled Python executable must exit before Windows can remove it.
    script = f"""
Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue
$ErrorActionPreference = 'Stop'
$flowPrefix = {ps_quote(prefix)}
$flowShell = New-Object -ComObject WScript.Shell
foreach ($flowFolder in @([Environment]::GetFolderPath('DesktopDirectory'), [Environment]::GetFolderPath('Programs'))) {{
  if (-not $flowFolder) {{ continue }}
  $flowLinkPath = Join-Path $flowFolder 'Flow Download Manager.lnk'
  if (Test-Path -LiteralPath $flowLinkPath) {{
    $flowLink = $flowShell.CreateShortcut($flowLinkPath)
    if ($flowLink.TargetPath.StartsWith($flowPrefix + '\\', [StringComparison]::OrdinalIgnoreCase)) {{ Remove-Item -LiteralPath $flowLinkPath }}
  }}
}}
$flowRegistry = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\FlowDownloadManager'
if ((Test-Path $flowRegistry) -and (Get-ItemProperty $flowRegistry).InstallLocation -eq $flowPrefix) {{ Remove-Item $flowRegistry -Recurse }}
$flowMarker = Get-Content -LiteralPath (Join-Path $flowPrefix '{MARKER}') -Raw | ConvertFrom-Json
if ($flowMarker.application -ne '{APP_ID}' -or $flowMarker.location -ne $flowPrefix) {{ throw 'Flow installation identity changed; removal cancelled.' }}
Remove-Item -LiteralPath $flowPrefix -Recurse -Force
"""
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], cwd=prefix.parent, creationflags=subprocess.CREATE_NO_WINDOW)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
