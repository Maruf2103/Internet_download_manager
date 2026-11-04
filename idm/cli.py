"""Command-line interface for sequential downloads."""

import argparse
from http.client import HTTPException
import math
from pathlib import Path
import sys
from urllib.error import URLError

from . import __version__
from .download import DownloadError, download, filename_from_url


def positive_timeout(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a number") from error
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive, finite number")
    return number


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        from .web import serve

        return serve(argv[1:])
    parser = argparse.ArgumentParser(description="Download HTTP(S) files safely.")
    parser.epilog = "Use 'idm serve' to launch the browser dashboard."
    parser.add_argument("urls", nargs="+", help="one or more HTTP(S) URLs")
    parser.add_argument("-d", "--directory", type=Path, default=Path("downloads"), help="output directory (default: downloads)")
    parser.add_argument("-o", "--output", type=Path, help="output file, allowed with one URL only")
    parser.add_argument("--overwrite", action="store_true", help="replace existing files only after a complete download")
    parser.add_argument("--timeout", type=positive_timeout, default=30, help="socket timeout in seconds (default: 30)")
    parser.add_argument("--version", action="version", version=f"idm {__version__}")
    args = parser.parse_args(argv)
    if args.output is not None and len(args.urls) != 1:
        parser.error("--output requires exactly one URL")

    failures = 0
    for index, url in enumerate(args.urls, start=1):
        try:
            destination = args.output if args.output is not None else args.directory / filename_from_url(url)
            print(f"[{index}/{len(args.urls)}] {destination}")

            def show_progress(size: int, total: int | None) -> None:
                if sys.stderr.isatty():
                    detail = f"{size:,} / {total:,} bytes" if total is not None else f"{size:,} bytes"
                    print(f"\r  {detail}", end="", file=sys.stderr, flush=True)

            result = download(url, destination, overwrite=args.overwrite, timeout=args.timeout, progress=show_progress)
            if sys.stderr.isatty():
                print(file=sys.stderr)
            print(f"Saved {result.size:,} bytes; SHA-256 {result.sha256}")
        except KeyboardInterrupt:
            print("\nDownload cancelled.", file=sys.stderr)
            return 130
        except (DownloadError, OSError, ValueError, URLError, HTTPException) as error:
            print(f"\nDownload failed: {error}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0
