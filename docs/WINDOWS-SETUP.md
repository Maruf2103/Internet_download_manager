# Set up Flow 2 on your Windows computer

Flow downloads **any direct HTTP/HTTPS file**: EXE/MSI app installers, ZIP/RAR/7z
archives, documents, pictures, audio, videos, and other file types. It can also
preserve a folder tree from a public directory listing or a JSON URL manifest.
Downloaded applications are saved; Flow does not run or install them for you.

## 1. Download and install the update

1. In the old Flow window, choose **Quit Flow**. Closing only its browser tab
   leaves the old background application running.
2. Download [Flow 2.0.0 for Windows x64](https://github.com/Maruf2103/Internet_download_manager/raw/refs/heads/windows-download/Flow-2.0.0-Windows-x64.zip).
3. Right-click the ZIP in File Explorer and choose **Extract All**.
4. Open the extracted folder and double-click **Install Flow.cmd**.
5. Flow opens in your usual browser. You can reopen it later using the
   **Flow Download Manager** Desktop or Start menu shortcut.

Python is included. You do not need to install Python, type a command, or give
administrator permission. Target: Windows 10/11 x64. The installer keeps your
existing files and history in `%USERPROFILE%\Downloads\Flow` and installs the new
application in `%LOCALAPPDATA%\Programs\Flow\2.0.0`.

The separate [ZIP checksum](https://github.com/Maruf2103/Internet_download_manager/raw/refs/heads/windows-download/Flow-2.0.0-Windows-x64.zip.sha256)
can be checked with PowerShell's `Get-FileHash -Algorithm SHA256` if desired.

## 2. Connect Chrome

Do this **after running the installer**, because the installer registers Chrome's
local native-messaging bridge.

1. Restart Google Chrome.
2. Type `chrome://extensions` in Chrome's address bar.
3. Switch **Developer mode** on at the top right.
4. Click **Load unpacked**.
5. In the folder picker, paste this into the address bar, then choose **Select Folder**:

   ```text
   %LOCALAPPDATA%\Programs\Flow\2.0.0\ChromeExtension
   ```

6. Open Chrome's puzzle-piece **Extensions** menu and pin **Flow Download Manager**.
7. Click the Flow icon. Its popup should say **Flow 2.0.0 bridge is ready**.

This is a locally installed extension, not a Chrome Web Store listing. Its
permissions support native messaging, download handoff, context menus, local
settings, and finding links on the page you explicitly select. It does not read
browser cookies or browsing history and does not request access to every website.

## 3. Download files and applications

- **From a web page:** right-click the actual download link and choose
  **Download link with Flow**.
- **From the extension:** paste one or more direct file URLs and click
  **Download with Flow**. **Find links on this page** can collect common file links
  for you to review before sending them.
- **From Flow:** paste a URL into the quick download field, or use **New download**
  for several links.

A web page containing a download button is not always the file's URL. Use the
actual file link or the browser's download URL. Blob URLs, login/cookie-protected
downloads, and DRM or segmented streaming media may need to stay in Chrome.
A direct MP4/video file is supported just like an EXE or PDF; video-site extraction
and DRM bypass are not included.

The optional **Hand off Chrome downloads to Flow** checkbox is off by default.
Enable it after testing an ordinary public download. Flow confirms queue
acceptance before the extension cancels Chrome's transfer. If the native bridge
is unavailable, Chrome keeps downloading. A very small file may finish in Chrome
before handoff; existing Chrome files are never deleted. For sign-in-protected
files, turn handoff off and let Chrome download normally.

## 4. Download folders

### Public directory listing

1. In Flow, click **Download folder**.
2. Paste the folder's public directory URL, such as `https://your-server/files/`.
3. Click **Preview files**, review the paths, then **Download folder**.

Nested paths are preserved. You can also right-click a directory link in Chrome
and choose **Download linked folder with Flow**. This requires a server that lists
its files; an arbitrary web page or private cloud folder is not a directory listing.
For Google Drive/OneDrive and similar services, use their **Download as ZIP**
feature and let the browser handle any required sign-in.

### File manifest

Click **Import manifest** in Flow and select a JSON file of this form:

```json
{
  "name": "My files",
  "files": [
    {"url": "https://example.com/docs/manual.pdf", "path": "Documents/manual.pdf"},
    {"url": "https://example.com/setup.exe", "path": "Apps/setup.exe"}
  ]
}
```

Replace the example URLs with real direct file URLs. The checked-in
`examples/folder-manifest.json` contains two public Python documentation files
for a small real test. Manifest paths must be relative and unique on Windows.
Traversal, duplicate names, and file/directory collisions are rejected.

Folder files are stored below `Downloads\Flow\folders\<name>-<id>\`, with the
selected relative paths. Discovery is limited to 500 files, 60 directory pages,
and eight nested listing levels per preview; split larger trees into batches.
Empty folders are not represented by a file manifest.

## 5. Get good transfer speeds

- Start with **8 connections per file**; try **16 · Maximum** for a large file from
  a server that supports parallel ranges. Use **4 · Balanced** if a site throttles
  or rejects many connections.
- Large files (4 MB or more) with usable range/validator support can use several
  connections. Other servers fall back to a normal stream. Small files remain
  single-stream to avoid needless overhead.
- Changes apply to the next transfer or resume. Changing the part count can
  restart an existing segmented transfer. Keep it unchanged when resuming if you
  want to reuse the segments.
- The default queue runs three files at once. Simultaneous transfers share your
  connection. Pausing other large downloads can help one important file finish.
- Segments are merged and SHA-256 checked before publication. Allow roughly twice
  the file size in temporary free disk space during a segmented transfer.

Flow cannot exceed your ISP, Wi-Fi signal, disk, or the remote server's limits.
No fixed speed multiplier is promised. **Multiple-Wi-Fi bonding is not included**;
combining connections needs OS/router/VPN support. Flow uses normal Windows
routing and can use a bonded connection if you configure one separately.

## Troubleshooting

- **Bridge not found:** run **Install Flow.cmd** again, restart Chrome, and confirm
  that you loaded the installed `2.0.0\ChromeExtension` folder. Portable mode alone
  does not register the bridge.
- **Older Flow instance:** choose Quit Flow in the old app, then reopen the new
  Desktop shortcut. Avoid running old and new versions at the same time.
- **Developer mode is disabled by your organization:** the browser's administrator
  must allow the extension. Flow does not override managed Chrome policies.
- **403/401 or an HTML login page:** keep that download in Chrome; Flow does not
  collect your session cookies.
- **Folder preview finds no files:** use a public listing, a provider's ZIP export,
  or a manifest with explicit file URLs.
- **Connection errors at 16 connections:** lower the setting to 4 or 1 and retry.

Uninstall Flow from Windows **Installed apps**. Downloads and history are retained;
the Flow shortcuts and its Chrome native-host registration are removed. Remove
the unpacked extension separately from `chrome://extensions` if no longer wanted.

## Validation status

The Linux test suite exercises actual bytes, segment resume, fallback, folder
paths, native protocol framing/identity restrictions, and the desktop/API workflow.
The web dashboard is tested in Chromium. The managed cloud browser blocks loading
unpacked extensions by administrator policy, so its full Chrome native integration
cannot be verified there. The native [Windows CI run](https://github.com/Maruf2103/Internet_download_manager/actions/runs/42370345154) passed installation, bridge registration, real Chromium extension handoff, desktop launch/reuse/shutdown, and uninstall with retained downloads for application commit `45a2e4f`. These results are separate from the Linux archive checks.
