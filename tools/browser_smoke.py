"""Optional real-browser smoke test. Requires Playwright and a Chromium browser.

Run: python3 tools/browser_smoke.py --browser /usr/bin/chromium
The application itself and its required test suite do not need Playwright.
"""

import argparse
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from playwright.sync_api import expect, sync_playwright

from idm.manager import DownloadManager
from idm.web import DashboardServer
from test_manager import PAYLOAD, fixture_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="optional Chromium executable path")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    with patch.dict(os.environ, {"no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}), tempfile.TemporaryDirectory(prefix="flow-browser-") as folder:
        fixture, fixture_thread = fixture_server()
        fixture.delay = 0.025
        manager = DownloadManager(folder)
        server = DashboardServer(("127.0.0.1", 0), manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors = []
        try:
            with sync_playwright() as p:
                options = {"headless": True}
                if args.browser:
                    options["executable_path"] = args.browser
                browser = p.chromium.launch(**options)
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                expect(page.locator("#empty")).to_be_visible()
                expect(page.locator("#connection")).to_contain_text("Connected")
                page.get_by_role("button", name="New download", exact=False).first.click()
                page.locator("#urls").fill("ftp://example.com/file")
                page.locator("#add-submit").click()
                expect(page.locator("#form-error")).to_contain_text("HTTP(S)")
                page.locator("#urls").fill(f"http://127.0.0.1:{fixture.server_port}/range")
                page.locator("#add-submit").click()
                expect(page.locator("#add-dialog")).not_to_be_visible()
                row = page.locator(".download-row").first
                expect(row).to_be_visible()
                row.get_by_role("button", name="Pause range", exact=True).click()
                expect(row.locator(".state-pill")).to_contain_text("Paused")
                expect(row.get_by_role("button", name="Resume range")).to_be_visible()
                row.get_by_role("button", name="Resume range").click()
                expect(row.locator(".state-pill")).to_contain_text("Completed", timeout=15000)
                with page.expect_download() as downloaded:
                    row.get_by_role("link", name="Save", exact=True).click()
                download = downloaded.value
                assert download.failure() is None
                assert Path(download.path()).read_bytes() == PAYLOAD
                expect(row.get_by_role("progressbar")).to_have_attribute("aria-valuenow", "100")
                page.locator("#quick-url").fill(f"http://127.0.0.1:{fixture.server_port}/another.zip")
                page.locator("#quick-submit").click()
                expect(page.locator(".download-row")).to_have_count(2)
                other = page.locator(".download-row").filter(has=page.locator(".file-name", has_text="another.zip"))
                expect(other.locator(".state-pill")).to_contain_text("Completed", timeout=15000)
                page.locator("#sort").select_option("name")
                expect(page.locator(".file-name").first).to_have_text("another.zip")
                other.get_by_role("button", name="Delete another.zip and remove it from history").click()
                expect(page.locator("#confirm-dialog")).to_be_visible()
                page.locator("#confirm-submit").click()
                expect(page.locator(".download-row")).to_have_count(1)
                page.locator("#search").fill("does-not-exist")
                expect(page.locator("#empty")).to_be_visible()
                page.locator("#search").fill("")
                expect(row).to_be_visible()
                page.locator("#theme").click()
                expect(page.locator("html")).to_have_attribute("data-theme", "dark")
                page.reload()
                expect(page.locator("html")).to_have_attribute("data-theme", "dark")
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.screenshot.with_name(args.screenshot.stem + "-dark.png")), full_page=True, animations="disabled")
                page.locator("#theme").click()
                expect(page.locator(".download-row")).to_have_count(1)
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.screenshot), full_page=True, animations="disabled")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile layout overflows horizontally"
                expect(page.locator("#quit-top")).to_be_visible()
                expect(page.get_by_role("navigation").get_by_role("button", name="All downloads", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="New download", exact=False).first).to_be_visible()
                if args.screenshot:
                    page.screenshot(path=str(args.screenshot.with_name(args.screenshot.stem + "-mobile.png")), full_page=True, animations="disabled")
                page.get_by_role("button", name="Delete range and remove it from history").click()
                expect(page.locator("#confirm-dialog")).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.locator(".download-row")).to_have_count(1)
                page.get_by_role("button", name="Delete range and remove it from history").click()
                page.locator("#confirm-submit").click()
                expect(page.locator("#empty")).to_be_visible()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.keyboard.press("n")
                expect(page.locator("#add-dialog")).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.locator("#add-dialog")).not_to_be_visible()
                page.keyboard.press("/")
                expect(page.locator("#search")).to_be_focused()
                page.locator("#quit").click()
                page.locator("#confirm-submit").click()
                expect(page.locator("#connection")).to_have_text("Flow is closed")
                assert not errors, errors
                browser.close()
            print("Browser smoke passed: batch/quick add, validation, pause/resume, exact file download, accessible progress, sorting, search, theme persistence, mobile layout, confirmation/cancel, shortcuts, quit, and no JavaScript errors.")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            manager.close()
            fixture.shutdown()
            fixture.server_close()
            fixture_thread.join()


if __name__ == "__main__":
    main()
