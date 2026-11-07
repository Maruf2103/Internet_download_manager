"""Validated, resumable parallel ranges with a normal-transfer fallback."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.request import Request

from .download import CHUNK_SIZE, DownloadError, DownloadResult, open_url
from .transfer import TransferStopped, transfer

MIN_PARALLEL_SIZE = 4 * 1024 * 1024


class RangeUnsupported(DownloadError):
    pass


def accelerated_transfer(url, destination, *, stopped, progress, connections=8, timeout=10, on_metadata=None, on_mode=None):
    destination = Path(destination)
    info = None
    if connections > 1:
        try:
            with open_url(Request(url, method="HEAD", headers={"Accept-Encoding": "identity"}), timeout) as response:
                total = int(response.headers.get("Content-Length", "0"))
                etag = response.headers.get("ETag")
                key = "ETag" if etag and not etag.startswith("W/") else "Last-Modified"
                validator = response.headers.get(key)
                if on_metadata:
                    destination = on_metadata(response.headers)
                if response.status == 200 and total >= MIN_PARALLEL_SIZE and validator and response.headers.get("Accept-Ranges", "").lower() == "bytes" and response.headers.get("Content-Encoding", "identity") == "identity":
                    info = dict(total=total, validator=validator, key=key)
        except (OSError, ValueError, HTTPException):
            pass  # HEAD is optional; the ordinary GET remains authoritative.
    cache = destination.with_name(destination.name + ".segments")
    partial = destination.with_name(destination.name + ".part")
    # Keep an existing single-stream download resumable rather than discarding it.
    if info and not (partial.exists() and not cache.exists()):
        count = min(connections, info["total"] // (1024 * 1024))
        if on_mode:
            on_mode("parallel", count)
        try:
            return parallel_ranges(url, destination, info, count, stopped, progress, timeout)
        except RangeUnsupported:
            shutil.rmtree(cache, ignore_errors=True)
            partial.unlink(missing_ok=True)
    elif cache.exists():
        # A different source, changed settings, or a server losing range support.
        shutil.rmtree(cache)
        partial.unlink(missing_ok=True)
    if on_mode:
        on_mode("single", 1)
    return transfer(url, destination, stopped=stopped, progress=progress, timeout=timeout, on_metadata=on_metadata)


def parallel_ranges(url, destination, info, count, stopped, progress, timeout):
    cache = destination.with_name(destination.name + ".segments")
    state = dict(url=url, **info, count=count)
    try:
        previous = json.loads((cache / "state.json").read_text())
    except (OSError, ValueError):
        previous = None
    if previous != state and cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "state.json").write_text(json.dumps(state))
    bounds = [(info["total"] * n // count, info["total"] * (n+1) // count - 1) for n in range(count)]
    amounts = []
    for n, (start, end) in enumerate(bounds):
        part = cache / f"{n}.part"
        size = part.stat().st_size if part.exists() else 0
        if size > end-start+1:
            part.unlink()
            size = 0
        amounts.append(size)
    lock = threading.Lock()
    abort = threading.Event()
    last_report = 0

    def report(index, amount):
        nonlocal last_report
        with lock:
            amounts[index] = amount
            stamp = time.monotonic()
            received = sum(amounts)
            if stamp-last_report >= .1 or received == info["total"]:
                progress(received, info["total"])
                last_report = stamp

    progress(sum(amounts), info["total"])

    def fetch(index):
        start, end = bounds[index]
        part = cache / f"{index}.part"
        for attempt in range(3):
            if stopped() or abort.is_set():
                raise TransferStopped
            have = part.stat().st_size if part.exists() else 0
            if have == end-start+1:
                return
            offset = start+have
            headers = {"Range": f"bytes={offset}-{end}", "If-Range": info["validator"], "Accept-Encoding": "identity", "User-Agent": "Flow/2.0"}
            try:
                with open_url(Request(url, headers=headers), timeout) as response:
                    expected = f"bytes {offset}-{end}/{info['total']}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected or response.headers.get(info["key"]) != info["validator"] or response.headers.get("Content-Encoding", "identity") != "identity":
                        raise RangeUnsupported("The server did not honor a validated byte range")
                    length = response.headers.get("Content-Length")
                    if length is not None and int(length) != end-offset+1:
                        raise RangeUnsupported("Inconsistent range length")
                    with part.open("ab") as output:
                        while True:
                            if stopped() or abort.is_set():
                                raise TransferStopped
                            data = response.read1(CHUNK_SIZE)
                            if not data:
                                break
                            if have+len(data) > end-start+1:
                                raise RangeUnsupported("Range exceeded its expected size")
                            output.write(data)
                            have += len(data)
                            report(index, have)
                    if have != end-start+1:
                        raise DownloadError("The range ended before all bytes arrived")
                    return
            except (OSError, HTTPException, DownloadError) as error:
                if getattr(error, "code", None) == 416:
                    raise RangeUnsupported("The server rejected a previously valid range") from error
                if isinstance(error, RangeUnsupported) or attempt == 2:
                    raise
                # Retain valid bytes and retry only this segment. Respect rate limits.
                if getattr(error, "code", None) in {401, 403, 429}:
                    raise
                if abort.wait(.25 * (attempt+1)) or stopped():
                    raise TransferStopped

    failure = None
    with ThreadPoolExecutor(max_workers=count, thread_name_prefix="flow-range") as pool:
        futures = [pool.submit(fetch, n) for n in range(count)]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as error:
                if failure is None or isinstance(error, RangeUnsupported):
                    failure = error
                abort.set()
    if stopped():
        raise TransferStopped
    if failure:
        raise failure
    digest = hashlib.sha256()
    partial = destination.with_name(destination.name + ".part")
    with partial.open("wb") as output:
        for n in range(count):
            with (cache / f"{n}.part").open("rb") as source:
                while data := source.read(1024 * 1024):
                    if stopped():
                        raise TransferStopped
                    output.write(data)
                    digest.update(data)
        output.flush()
        os.fsync(output.fileno())
    if stopped():
        raise TransferStopped
    os.replace(partial, destination)
    shutil.rmtree(cache)
    return DownloadResult(destination, info["total"], digest.hexdigest())
