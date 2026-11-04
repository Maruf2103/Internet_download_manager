"""Resumable transfers, with validators to avoid joining different file versions."""

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request

from .download import CHUNK_SIZE, DownloadError, DownloadResult, open_url, validate_url


class TransferStopped(Exception):
    """The manager requested a pause or cancellation."""


def transfer(url, destination: Path, *, stopped, progress, timeout=10) -> DownloadResult:
    validate_url(url)
    partial = destination.with_name(destination.name + ".part")
    metadata = destination.with_name(destination.name + ".resume.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    saved = {}
    if metadata.exists():
        try:
            saved = json.loads(metadata.read_text())
        except (ValueError, OSError):
            pass
    offset = partial.stat().st_size if partial.exists() else 0
    validator = saved.get("validator") if isinstance(saved, dict) and saved.get("url") == url else None
    if not validator:
        offset = 0
    headers = {"User-Agent": "InternetDownloadManager/1.0", "Accept-Encoding": "identity"}
    if offset:
        headers.update({"Range": f"bytes={offset}-", "If-Range": validator})
    if stopped():
        raise TransferStopped
    try:
        response = open_url(Request(url, headers=headers), timeout)
    except HTTPError as error:
        if offset and error.code == 416:
            # A changed/shrunken source or a completed partial: fetch afresh.
            error.close()
            response = open_url(Request(url, headers={k: v for k, v in headers.items() if k not in {"Range", "If-Range"}}), timeout)
            offset = 0
        else:
            raise
    with response:
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")
        current_validator = etag if etag and not etag.startswith("W/") else last_modified
        length = response.headers.get("Content-Length")
        try:
            length = int(length) if length is not None else None
        except ValueError as error:
            raise DownloadError("Invalid Content-Length") from error
        if length is not None and length < 0:
            raise DownloadError("Invalid Content-Length")
        if response.status == 206 and offset:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if not match:
                metadata.unlink(missing_ok=True)
                raise DownloadError("Server returned an invalid resume range")
            start, end, total = map(int, match.groups())
            if start != offset or end != total - 1 or end < start or (length is not None and length != end - start + 1):
                metadata.unlink(missing_ok=True)
                raise DownloadError("Server returned an inconsistent resume range")
            if validator != current_validator:
                metadata.unlink(missing_ok=True)
                raise DownloadError("The source changed during resume; retry the download")
        elif response.status == 200:
            offset = 0
            total = length
        else:
            raise DownloadError(f"Unexpected HTTP status: {response.status}")
        received = offset
        # Truncate stale data before publishing its new validator. An interruption
        # between these operations can only cause a restart, never mixed bytes.
        with partial.open("ab" if offset else "wb") as output:
            temp_metadata = metadata.with_suffix(".tmp")
            temp_metadata.write_text(json.dumps({"url": url, "validator": current_validator}))
            os.replace(temp_metadata, metadata)
            progress(received, total)
            while True:
                if stopped():
                    raise TransferStopped
                chunk = response.read1(CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
                received += len(chunk)
                progress(received, total)
            if stopped():
                raise TransferStopped
            if total is not None and received != total:
                raise DownloadError(f"Incomplete download: expected {total} bytes, received {received}")
            output.flush()
            os.fsync(output.fileno())
    digest = hashlib.sha256()
    with partial.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            if stopped():
                raise TransferStopped
            digest.update(chunk)
    if stopped():
        raise TransferStopped
    os.replace(partial, destination)
    metadata.unlink(missing_ok=True)
    return DownloadResult(destination, received, digest.hexdigest())
