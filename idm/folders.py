"""Bounded directory-listing discovery and portable relative-path validation."""

from html.parser import HTMLParser
from pathlib import PurePosixPath
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request

from .download import DownloadError, open_url, safe_name, validate_url


def relative_path(value):
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError("Each file needs a relative path of at most 1000 characters")
    value = value.replace("\\", "/")
    parts = value.split("/")
    if value.startswith("/") or ":" in value or not 1 <= len(parts) <= 12 or any(p in {"", ".", ".."} for p in parts):
        raise ValueError("Folder paths must be relative and cannot contain traversal components")
    return "/".join(safe_name(p) for p in parts)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for key, value in attrs:
                if key == "href" and value:
                    self.links.append(value)


def discover_folder(url, *, max_files=500, max_directories=60, budget=25):
    validate_url(url)
    root = urlsplit(url)
    if root.query or root.fragment:
        raise ValueError("Use a direct directory-listing URL without a query or fragment, or import a file manifest")
    if not root.path.endswith("/"):
        root = root._replace(path=root.path + "/")
    base = urlunsplit(root)
    pending, seen, files = [base], set(), {}
    started = time.monotonic()
    while pending:
        if len(seen) >= max_directories or time.monotonic()-started > budget:
            raise ValueError("Folder discovery limit reached; split it into smaller folders or import a manifest")
        directory = pending.pop(0)
        if directory in seen:
            continue
        seen.add(directory)
        with open_url(Request(directory, headers={"User-Agent": "Flow/2.0"}), min(8, max(.5, budget-(time.monotonic()-started)))) as response:
            final = urlsplit(response.geturl())
            if (final.scheme, final.netloc) != (root.scheme, root.netloc) or not final.path.startswith(root.path):
                raise ValueError("Directory redirected outside the selected folder")
            if response.headers.get_content_type() != "text/html":
                raise ValueError("This URL is not a public HTML directory listing; use its archive download or a manifest")
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("Directory listing is too large")
            parser = Links()
            parser.feed(data.decode(response.headers.get_content_charset() or "utf-8", errors="replace"))
            for href in parser.links:
                candidate = urlsplit(urljoin(response.geturl(), href))
                if candidate.fragment or candidate.query or (candidate.scheme, candidate.netloc) != (root.scheme, root.netloc) or not candidate.path.startswith(root.path):
                    continue
                decoded = unquote(candidate.path[len(root.path):])
                if not decoded or decoded.startswith("/"):
                    continue
                path = relative_path(decoded.rstrip("/"))
                target = urlunsplit(candidate)
                if candidate.path.endswith("/"):
                    if len(PurePosixPath(path).parts) > 8:
                        raise ValueError("Folder nesting is too deep")
                    if target not in seen and target not in pending:
                        pending.append(target)
                else:
                    if path.casefold() in files and files[path.casefold()]["url"] != target:
                        raise ValueError("Different files map to the same Windows path; use a manifest with distinct names")
                    files[path.casefold()] = dict(url=target, path=path)
                    if len(files) > max_files:
                        raise ValueError(f"Folder contains more than {max_files} files; split it into smaller batches")
    if not files:
        raise ValueError("No files found. Private/cloud folders need a provider archive link or an explicit URL manifest")
    return {"name": safe_name(unquote(root.path.rstrip("/").rsplit("/", 1)[-1]) or "Folder"), "files": list(files.values())}
