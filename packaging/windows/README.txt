FLOW DOWNLOAD MANAGER — WINDOWS x64

INSTALL
1. Right-click the downloaded ZIP and choose Extract All.
2. Open the extracted folder and double-click "Install Flow.cmd".
3. Flow opens in your default browser. Next time, use the Flow Download Manager
   shortcut on your Desktop or in the Start menu.

Python is included. No Python installation, pip command, administrator account,
or Internet connection is needed to install. Downloading files requires Internet
access as usual. Target: Windows 10/11, 64-bit.

PORTABLE MODE
Double-click "Flow.cmd" instead of installing. Keep the extracted folder intact.
The included runtime and application stay in that folder.

FILES
Downloads and history are stored in your user Downloads\Flow folder. Browser
"Save" makes a separate copy. Deleting a job in Flow deletes its local data.
Closing the browser tab leaves Flow running; use "Quit Flow" in the sidebar to
stop it and pause active downloads. Opening Flow again resumes the dashboard;
press Resume on any paused transfers.

UPGRADES / UNINSTALL
The installer uses your Local AppData\Programs\Flow folder. It can be rerun and
keeps downloads separate. Uninstall "Flow Download Manager" in Windows Settings
under Installed apps. Downloads and history are retained after uninstall.

VERIFICATION
payload.json lists the SHA-256 of every included file. The accompanying
.zip.sha256 file gives the checksum of the complete release archive. The builder verifies the bundled
official Python Software Foundation runtime against its pinned NuGet SHA-512.
The installer verifies every payload file before copying it. You can run
runtime\python.exe -I install.py --check to check the extracted bundle.

The application, installer, real Chrome native bridge, launch/reuse/shutdown,
and uninstall were validated by native Windows CI for application commit 45a2e4f:
https://github.com/Maruf2103/Internet_download_manager/actions/runs/42370345154
The Linux build also verifies the ZIP, payload hashes, runtime architecture,
installer file operations, and repeat installation.

CHROME EXTENSION AND NEW FEATURES
See CHROME-SETUP.txt for Chrome integration and WINDOWS-SETUP.md for full computer
setup, folder manifests, performance options, and limitations. Flow supports any
direct HTTP(S) file type, not just videos. Multiple Wi-Fi bonding is not included.
