"""Real Chromium extension -> native host -> Flow test. Needs an unmanaged browser."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from idm.chrome_host import HOST_NAME, extension_id
from idm.manager import DownloadManager
from idm.web import DashboardServer
from test_manager import fixture_server, wait_for, PAYLOAD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', help='Chromium executable; defaults to Playwright Chromium')
    parser.add_argument('--python', default=sys.executable, help='Python executable for native host')
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='flow-chrome-') as folder:
        root = Path(folder); home = root / 'home'; home.mkdir()
        manager = DownloadManager(root / 'downloads'); server = DashboardServer(('127.0.0.1', 0), manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        fixture, fixture_thread = fixture_server()
        host = root / 'native.py'
        host.write_text(f'import sys\nsys.path.insert(0,{str(ROOT)!r})\nfrom idm.chrome_host import main\nraise SystemExit(main())\n')
        if sys.platform == 'win32':
            wrapper = root / 'native.cmd'
            wrapper.write_text(f'@echo off\n"{args.python}" -I "{host}" %*\n')
        else:
            wrapper = root / 'native-host'
            wrapper.write_text(f'#!{args.python}\nimport runpy\nrunpy.run_path({str(host)!r},run_name="__main__")\n'); wrapper.chmod(0o755)
        manifest = {'name': HOST_NAME, 'description': 'Flow test bridge', 'path': str(wrapper), 'type': 'stdio', 'allowed_origins': [f'chrome-extension://{extension_id()}/']}
        host_manifest = root / 'native.json'; host_manifest.write_text(json.dumps(manifest))
        old_registry = None
        key_name = 'Software\\Google\\Chrome\\NativeMessagingHosts\\' + HOST_NAME
        if sys.platform == 'win32':
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_name) as key:
                try: old_registry = winreg.QueryValue(key, None)
                except OSError: pass
                winreg.SetValue(key, '', winreg.REG_SZ, str(host_manifest))
        else:
            native = home / '.config/chromium/NativeMessagingHosts'; native.mkdir(parents=True)
            (native / (HOST_NAME + '.json')).write_text(json.dumps(manifest))
        env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'), FLOW_PORT=str(server.server_port), NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
        try:
            with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}), sync_playwright() as p:
                extension = ROOT / 'idm/chrome_extension'
                browser = args.browser or p.chromium.executable_path
                context = p.chromium.launch_persistent_context(str(root / 'profile'), executable_path=browser, headless=True, ignore_default_args=['--disable-extensions'], env=env, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}'])
                try:
                    workers = context.service_workers or [context.wait_for_event('serviceworker', timeout=15000)]
                    assert extension_id() in workers[0].url
                    page = context.new_page(); errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
                    page.goto(f'chrome-extension://{extension_id()}/popup.html')
                    try:
                        expect(page.locator('#connection')).to_contain_text('bridge is ready', timeout=15000)
                    except Exception:
                        raise RuntimeError('Chrome native bridge: ' + page.locator('#connection').inner_text()) from None
                    page.locator('#links').fill(f'http://127.0.0.1:{fixture.server_port}/setup.exe\nhttp://127.0.0.1:{fixture.server_port}/archive.zip')
                    page.locator('#send').click(); expect(page.locator('#message')).to_contain_text('2 file(s) added', timeout=15000)
                    for job in manager.snapshot()['jobs']:
                        completed = wait_for(manager, job['id'], lambda j: j['state'] == 'completed')
                        assert manager.path(completed).read_bytes() == PAYLOAD
                    assert {j['name'] for j in manager.snapshot()['jobs']} == {'setup.exe', 'archive.zip'}
                    assert not page.locator('#capture').is_checked()
                    if args.screenshot:
                        args.screenshot.parent.mkdir(parents=True, exist_ok=True); page.screenshot(path=str(args.screenshot))
                    assert not errors, errors
                finally:
                    context.close()
            print('Real Chrome extension passed: unpacked extension, native host, popup, generic files/app installers, exact bytes, and capture off by default.')
        finally:
            if sys.platform == 'win32':
                if old_registry:
                    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_name) as key: winreg.SetValue(key, '', winreg.REG_SZ, old_registry)
                else: winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)
            server.shutdown(); server.server_close(); thread.join(); manager.close()
            fixture.shutdown(); fixture.server_close(); fixture_thread.join()

if __name__ == '__main__':
    main()
