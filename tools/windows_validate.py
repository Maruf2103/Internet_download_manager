"""Native Windows release validation with concise, non-secret CI diagnostics."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from http.client import HTTPConnection
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from idm import __version__

PHASE = 'initialization'


def phase(name):
    global PHASE
    PHASE = name
    print(f'Windows validation: {name}', flush=True)


def run(command):
    result = subprocess.run([str(p) for p in command], capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=ROOT)
    if result.returncode:
        detail = (result.stdout + '\n' + result.stderr)[-7000:]
        raise RuntimeError(f'{PHASE}: exit {result.returncode}\n{detail}')
    if result.stdout.strip(): print(result.stdout.strip(), flush=True)
    return result


def request(port, path, body=None):
    connection = HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('GET' if body is None else 'POST', path, body=body, headers={} if body is None else {'Content-Type': 'application/json'})
        response = connection.getresponse(); data = response.read()
        if response.status != 200: raise RuntimeError(f'Unexpected local response {response.status}')
        return data
    finally: connection.close()


def main():
    if sys.platform != 'win32': raise RuntimeError('This validation requires Windows')
    import winreg
    archive = ROOT / 'dist' / f'Flow-{__version__}-Windows-x64.zip'
    with tempfile.TemporaryDirectory(prefix='flow-native-') as folder:
        root = Path(folder); prefix = root / 'Installed'; downloads = root / 'Downloads'
        phase('extract and verify bundled runtime')
        with zipfile.ZipFile(archive) as package: package.extractall(root / 'release')
        release = next((root / 'release').iterdir()); runtime = release / 'runtime/python.exe'
        run([runtime, '-I', release / 'install.py', '--check'])
        phase('offline installation and Windows registration')
        run([runtime, '-I', release / 'install.py', '--prefix', prefix, '--no-launch'])
        installed = prefix / __version__; runtime = installed / 'runtime/python.exe'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\FlowDownloadManager') as key:
            assert winreg.QueryValueEx(key, 'DisplayVersion')[0] == __version__
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Software\\Google\\Chrome\\NativeMessagingHosts\\com.maruf2103.flow') as key:
            host = Path(winreg.QueryValueEx(key, '')[0])
        assert host.is_file() and json.loads(host.read_text())['allowed_origins']
        phase('desktop launch using bundled pythonw')
        with socket.socket() as probe: probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        process = subprocess.Popen([str(installed / 'runtime/pythonw.exe'), '-I', str(installed / 'launch.py'), '--no-browser', '--port', str(port), '--directory', str(downloads)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 20
            healthy = False
            while time.monotonic() < deadline:
                try:
                    health = json.loads(request(port, '/api/health'))
                    healthy = health.get('app') == 'flow' and health.get('version') == __version__
                    if healthy: break
                except OSError: time.sleep(.1)
            assert healthy, 'The installed desktop launcher did not become healthy'
            assert b'Download folder' in request(port, '/')
            phase('real Chrome extension and native bridge')
            run([sys.executable, ROOT / 'tools/chrome_smoke.py', '--python', runtime])
            phase('reuse running application')
            run([runtime, '-I', installed / 'launch.py', '--no-browser', '--port', str(port)])
            phase('clean desktop shutdown')
            request(port, '/api/shutdown', '{}')
            assert process.wait(timeout=15) == 0
        finally:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=10)
        phase('uninstall while retaining downloaded files')
        (downloads / 'keep.txt').write_text('retained user file')
        run([runtime, '-I', installed / 'uninstall.py', '--prefix', prefix, '--yes'])
        deadline = time.monotonic() + 30
        while prefix.exists() and time.monotonic() < deadline: time.sleep(.2)
        assert not prefix.exists(), 'Uninstaller left application files behind'
        assert (downloads / 'keep.txt').read_text() == 'retained user file'
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Software\\Google\\Chrome\\NativeMessagingHosts\\com.maruf2103.flow'):
                raise AssertionError('Uninstaller left the Chrome native-host registry key')
        except FileNotFoundError: pass
        phase('all native Windows checks passed')


if __name__ == '__main__':
    try: main()
    except Exception as error:
        message = f'{PHASE}: {error}'[:8500]
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            message = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
            print(f'::error title=Flow Windows validation::{message}', flush=True)
        raise
