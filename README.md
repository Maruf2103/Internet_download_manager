# Flow — Internet Download Manager

A local download manager with a browser dashboard and a command-line interface.
Built with Python 3.12+ and the standard library; there are no runtime dependencies.

## Install on Windows

[Download Flow 2.0.0 for Windows x64](https://github.com/Maruf2103/Internet_download_manager/raw/refs/heads/windows-download/Flow-2.0.0-Windows-x64.zip)
and its [SHA-256 checksum](https://github.com/Maruf2103/Internet_download_manager/raw/refs/heads/windows-download/Flow-2.0.0-Windows-x64.zip.sha256).
The same files are available in the prepared `dist/` directory. After downloading,
right-click it and choose **Extract All**, then double-click **Install Flow.cmd**.
The offline package includes its Python runtime. You do not need Python, pip,
administrator access, or an Internet connection to install it.

The installer creates Desktop and Start menu shortcuts, registers Flow under
Windows **Installed apps**, and opens the dashboard in your default browser.
It installs per user in `%LOCALAPPDATA%\Programs\Flow`. Your downloads and history
stay separately in `%USERPROFILE%\Downloads\Flow` and are retained on uninstall.
Target: **Windows 10/11 x64**. To run without installing, open **Flow.cmd** in
the extracted folder. To stop the background application, use **Quit Flow** in
the dashboard; closing only the browser tab leaves downloads running.

The builder verifies the official Python Software Foundation runtime using its
pinned NuGet SHA-512, and the installer checks every bundled payload file. The
release ZIP has a separate `.zip.sha256` file. The Windows package was assembled
and integrity-checked on Linux, and its installer file operations were tested
there. **Native Windows installation and real Chrome bridge integration passed [Windows CI](https://github.com/Maruf2103/Internet_download_manager/actions/runs/42370345154) for application commit `45a2e4f`.** The run also verified desktop launch, instance reuse, clean shutdown, and uninstall with downloaded files retained.

## What works

- Add one or many HTTP(S) links to a persistent queue.
- Run up to eight concurrent downloads (three by default).
- Watch live progress, transfer speeds, and completed byte counts.
- Pause, resume, cancel, retry, or delete individual downloads.
- Pause/resume the whole queue, search history, and filter by status.
- Save completed files through your browser and copy SHA-256 checksums.
- Resume safely using HTTP Range requests and strong ETag or Last-Modified
  validators. Changed sources and servers without resume support restart safely.
- Keep history and partial files across application restarts. Unfinished jobs
  recover as paused, so they do not silently resume consuming bandwidth.
- Use the dashboard on desktop or mobile, with light and dark themes.
- Quickly paste a link, sort files, use keyboard shortcuts (`N` to add, `/` to
  search), and confirm deletion in an accessible dialog.

Flow 2 adds validated segmented transfers (up to 16 connections per file),
public folder-tree downloads, manifest import, and a Chrome Manifest V3 extension
with a local native-messaging bridge. All direct file types are supported,
including application installers; downloaded applications are never auto-executed.

**[Windows installation and Chrome setup guide](docs/WINDOWS-SETUP.md)**

Speed depends on your network, disk, and server. Multiple-Wi-Fi bonding, video-site
extraction, authenticated cookie transfer, and DRM bypass are not included.
Cloud folders can use their provider's archive export; public directory listings
and explicit URL manifests preserve nested folder paths.

This is a personal, local application. It does not include video-site extraction, scheduled downloads, or a public multi-user service. Chrome download handoff is opt-in and uses no cookies.

## Quick start

Requires Python 3.12 or newer. From the repository root, no installation is needed:

```sh
python3 -m idm serve
```

Open port **8765** on **127.0.0.1** in a browser on the same machine. Click
**New download**, paste one HTTP(S) URL per line, and start downloading. The
server prints its address and download directory at startup. It listens only on
loopback. Stop it with Ctrl+C; active downloads are paused and retained.

To choose another output directory, port, or concurrency limit:

```sh
python3 -m idm serve --directory ./downloads --port 8765 --workers 3
```

Only one manager may use a particular download directory at a time. If the port
is busy, stop the existing instance or choose another port. Use the same directory
on the next run to recover history and partial downloads.

## Install the command and develop

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/idm serve
```

After installation, `flow` opens the desktop dashboard automatically, keeps data
in your user Downloads/Flow folder, and reopens an existing instance on a second
launch. Use `flow --no-browser` for a headless launch, or `flow --help` for options.

On Windows, replace `.venv/bin/python` and `.venv/bin/idm` with
`.venv\Scripts\python.exe` and `.venv\Scripts\idm.exe`. You can also run
`python -m idm` directly from the source checkout.

The two pinned development dependencies are packaging tools, not application
requirements. Application data and build outputs are ignored by Git.

## Command-line downloads

```sh
.venv/bin/idm https://raw.githubusercontent.com/python/cpython/3.12/LICENSE
.venv/bin/idm https://example.com/file.zip -o downloads/file.zip
.venv/bin/idm https://example.com/a.zip https://example.com/b.zip -d downloads
.venv/bin/idm https://example.com/file.zip -o downloads/file.zip --overwrite --timeout 60
```

The simple CLI downloads URLs sequentially and displays progress in a terminal.
It streams to a temporary file and publishes only completed transfers. Existing
files are protected unless `--overwrite` is explicit. Failed or interrupted CLI
transfers remove their temporary files; use the dashboard for persistent,
resumable downloads. Duplicate CLI filenames require distinct output paths;
the dashboard automatically keeps duplicate names in separate job directories.

CLI exit statuses: **0** all succeeded, **1** one or more failed, **2** invalid
arguments, **130** interrupted. `idm --help` and `idm serve --help` list options.

## Files, persistence, and integrity

The dashboard stores `history.sqlite3`, its instance-lock database, and one
subdirectory per download in the selected directory (default `downloads/`).
A job directory contains the file, or a `.part` file and resume metadata while
incomplete. Keep the whole directory to retain resumability. **Remove/delete**
actions delete that job's local file and partial data, as the confirmation says.
Browser **Save** downloads a separate copy from the server to your browser.

Download URLs, including any signed query parameters, are stored in local
history to support resume. Treat that directory as personal application data.
The dashboard renders names as text and serves completed files as attachments.
It rejects foreign Origin/Host requests and does not expose arbitrary filesystem
paths. No account or API key is required; it is not intended for public hosting.

TLS verification and the configured HTTP(S) proxies remain enabled. A reported
SHA-256 identifies the downloaded bytes; compare it with the publisher's checksum
when available. Resume requires a strong ETag or Last-Modified validator. Without
one, the transfer restarts from zero. Servers returning an invalid resume range
fail safely; retry starts afresh. Pause/cancel can wait for the current socket
read or connection timeout before taking effect.

## Test and build

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip wheel --no-build-isolation --no-deps . --wheel-dir dist
```

The required suite uses temporary loopback HTTP servers, not external downloads.
It covers real bytes, checksums, redirects, failure cleanup, resume and changed
sources, concurrency, cancellation, restart recovery, instance locking, and the
web API. CI runs these checks on Python 3.12 and 3.13. The current cloud instance
has been validated on Python 3.12; hosted CI executes after the repository is
pushed to GitHub.

An optional real-browser smoke test uses Playwright and Chromium. It checks
validation, add/pause/resume, saved file bytes, search, theme persistence, mobile
layout, and deletion:

```sh
# Optional browser-test dependencies; not needed to run the application.
python3 -m pip install playwright
python3 -m playwright install chromium
python3 tools/browser_smoke.py
# Or use an already installed Chromium:
python3 tools/browser_smoke.py --browser /usr/bin/chromium
```

To capture a dashboard screenshot, add `--screenshot /tmp/flow.png`.
This also captures dark-theme and mobile screenshots with `-dark` and `-mobile`
suffixes. The browser smoke check additionally covers folder previews, manifest import, connection settings, quick add, sorting,
accessible progress, modal focus restoration, keyboard shortcuts, and quitting.

## Build the Windows package

```sh
.venv/bin/python -m pip wheel --no-build-isolation --no-deps . --wheel-dir dist
.venv/bin/python tools/build_windows.py
```

The builder downloads the pinned official runtime from `api.nuget.org` on the
first run and reuses only checksum-valid cache entries thereafter. No verification
is disabled. The resulting `dist/Flow-2.0.0-Windows-x64.zip` contains the complete
application, Windows runtime, installer, uninstaller, icon, and payload manifest.
The `packaging/windows/runtime.json` file records the authoritative hash metadata.
On a machine with Python, the extracted installer's `--check` mode verifies the
whole bundle without installing anything. The Windows workflow can be started
with **Run workflow** after the code is on GitHub, and uploads the tested ZIP.

## Cloud environment

Use the existing checkout at `/workspace/Internet_download_manager`; cloud tasks
are already isolated, so no additional Git worktree is needed. The prepared
virtual environment is `.venv`. Start the dashboard with `.venv/bin/idm serve`;
check `/api/health` and `/api/jobs` through local HTTP requests for readiness.
Live processes do not survive environment publication or restoration, so the
saved startup instructions restart the dashboard when needed.

No database service, secret, or environment variable is required. SQLite data is
local. Local tests and the dashboard require loopback socket access. Cloud egress
policy must allow each download hostname and redirect destination. The configured
package-manager preset supports installation and the documented public GitHub
smoke download. Add other destinations in environment settings while preserving
existing rules; never disable proxy settings or TLS verification to bypass policy.

## Chrome bridge tests

```sh
.venv/bin/python -m unittest discover -s tests -v
node tools/test_extension.js
# Requires an unmanaged Chromium with unpacked extensions allowed:
python3 tools/chrome_smoke.py --browser /path/to/chromium
```

The managed cloud Chromium blocks unpacked extensions by administrator policy.
This is a real end-to-end test blocker; native protocol and handoff logic tests
do not replace a native Chrome execution result. Windows CI runs the extension
test with Playwright Chromium and the bundled Python runtime.
