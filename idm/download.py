"""Stream HTTP downloads to temporary files before publishing completed files."""

from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
import hashlib
import math
import os
from pathlib import Path
import tempfile
import re
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CHUNK_SIZE = 64 * 1024


class DownloadError(Exception):
    """A failed or incomplete transfer."""


def validate_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 8192 or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError("Enter a valid HTTP(S) URL without spaces")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http:// or https:// and include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in URLs are not supported")
    _ = parsed.port  # Validate the port before starting a transfer.
    return url


class HTTPOnlyRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(request: Request, timeout: float):
    # Keep the default proxy and TLS handlers while limiting redirects to HTTP(S).
    return build_opener(HTTPOnlyRedirects()).open(request, timeout=timeout)


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size: int
    sha256: str


def filename_from_url(url: str) -> str:
    """Use only a safe final path component, never remote directory names."""
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char.isprintable())
    return safe_name(name)


def safe_name(name):
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).strip().rstrip(". ")
    if not name or name in {".", ".."}:
        name = "download"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{n}" for n in range(1, 10)], *[f"LPT{n}" for n in range(1, 10)]}:
        name = "_" + name
    return name.encode("utf-8")[:180].decode("utf-8", errors="ignore")


def response_filename(headers):
    message = Message()
    message["Content-Disposition"] = headers.get("Content-Disposition", "")
    name = message.get_filename()
    return safe_name(name) if name else None


def download(
    url: str,
    destination: Path,
    *,
    overwrite: bool = False,
    timeout: float = 30,
    progress: Callable[[int, int | None], None] | None = None,
) -> DownloadResult:
    """Download one URL, keeping any existing destination intact on failure.

    Python's configured HTTPS trust and HTTP(S)_PROXY settings are respected.
    The temporary file lives beside the destination so publication is atomic.
    """
    validate_url(url)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be greater than zero")
    destination = Path(destination)
    if not overwrite and destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    digest = hashlib.sha256()
    size = 0
    try:
        request = Request(url, headers={"User-Agent": "InternetDownloadManager/0.1"})
        with open_url(request, timeout=timeout) as response:
            if response.status != 200:
                raise DownloadError(f"Expected HTTP 200, received {response.status}")
            length = response.headers.get("Content-Length")
            try:
                total = int(length) if length is not None else None
            except ValueError as error:
                raise DownloadError("Server returned an invalid Content-Length") from error
            if total is not None and total < 0:
                raise DownloadError("Server returned a negative Content-Length")
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=".idm-", suffix=".part", delete=False
            ) as output:
                temporary = Path(output.name)
                while chunk := response.read(CHUNK_SIZE):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if progress:
                        progress(size, total)
                if total is not None and size != total:
                    raise DownloadError(f"Incomplete download: expected {total} bytes, received {size}")
                output.flush()
                os.fsync(output.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            # A hard link publishes the complete file without clobbering a file
            # another process may have created while the transfer was running.
            os.link(temporary, destination)
        return DownloadResult(destination, size, digest.hexdigest())
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
