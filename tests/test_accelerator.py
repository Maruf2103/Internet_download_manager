from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from idm.manager import DownloadManager
from idm.folders import discover_folder
from test_manager import wait_for

DATA = bytes(range(256)) * 32768
CHANGED = bytes(reversed(range(256))) * 32768

class FastHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_HEAD(self):
        if self.path == '/no-head':
            self.send_error(405); return
        self.send_response(200)
        self.send_header('Content-Length', str(len(DATA)))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('ETag', '"v2"' if self.server.changed else '"v1"')
        if self.path == '/download':
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''App%20Setup.exe")
        self.end_headers()

    def do_GET(self):
        if self.path in {'/folder/', '/folder/sub/'}:
            html = '<h1>Index of /folder/</h1><a href="../">Parent</a><a href="https://example.org/out.exe">Outside</a>'
            html += '<a href="sub/">Sub</a><a href="report.pdf">PDF</a><a href="installer.exe">App</a>' if self.path == '/folder/' else '<a href="app.apk">Android app</a>'
            data = html.encode(); self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        body = CHANGED if self.server.changed else DATA
        start, end = 0, len(body)-1
        request_range = self.headers.get('Range')
        if request_range and self.path != '/ignore':
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', request_range)
            start = int(match[1]); end = int(match[2]) if match[2] else end
        self.server.requests.append((self.path, request_range))
        partial = bool(request_range) and self.path != '/ignore'
        self.send_response(206 if partial else 200)
        if partial:
            self.send_header('Content-Range', f'bytes {start if self.path != "/bad-range" else start+1}-{end}/{len(body)}')
        self.send_header('Content-Length',str(end-start+1))
        self.send_header('ETag','"v2"' if self.server.changed else '"v1"')
        self.end_headers()
        with self.server.lock:
            self.server.active += 1; self.server.maximum = max(self.server.maximum,self.server.active)
        try:
            for index in range(start,end+1,32768):
                self.wfile.write(body[index:min(index+32768,end+1)]); self.wfile.flush()
                time.sleep(self.server.delay)
        except (BrokenPipeError,ConnectionResetError): pass
        finally:
            with self.server.lock: self.server.active -= 1

class AcceleratorTests(unittest.TestCase):
    def setUp(self):
        self.proxy=patch.dict(os.environ,{'NO_PROXY':'127.0.0.1','no_proxy':'127.0.0.1'});self.proxy.start()
        self.folder=tempfile.TemporaryDirectory()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),FastHandler);self.server.daemon_threads=True
        self.server.changed=False;self.server.requests=[];self.server.active=0;self.server.maximum=0;self.server.delay=.004;self.server.lock=threading.Lock()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.manager=DownloadManager(self.folder.name,workers=2);self.manager.configure(4)

    def tearDown(self):
        self.manager.close();self.server.shutdown();self.server.server_close();self.thread.join();self.folder.cleanup();self.proxy.stop()

    def add(self,path='/file.zip'):
        return self.manager.add([self.url+path])[0]['id']

    def complete(self,key):
        job=wait_for(self.manager,key,lambda j:j['state'] in {'failed','completed'},timeout=10)
        self.assertEqual(job['state'],'completed',job.get('error'));return job

    def pause(self,key):
        wait_for(self.manager,key,lambda j:j['received']>=262144 and j['state']=='downloading')
        self.manager.action(key,'pause');return wait_for(self.manager,key,lambda j:j['state']=='paused')

    def test_parallel_bytes_hash_and_connection_count(self):
        job=self.complete(self.add());self.assertEqual(self.manager.path(job).read_bytes(),DATA)
        self.assertEqual(job['sha256'],hashlib.sha256(DATA).hexdigest());self.assertEqual(job['connections'],4)
        self.assertEqual(job['transfer_mode'],'parallel');self.assertGreaterEqual(self.server.maximum,2)
        self.assertFalse(list(self.manager.path(job).parent.glob('*.segments')))

    def test_pause_resume_reuses_segment_progress(self):
        key=self.add();paused=self.pause(key);self.assertGreater(paused['received'],0)
        self.manager.action(key,'resume');job=self.complete(key)
        self.assertEqual(self.manager.path(job).read_bytes(),DATA)
        initial={f'bytes={len(DATA)*n//4}-{len(DATA)*(n+1)//4-1}' for n in range(4)}
        self.assertTrue(any(header not in initial for _,header in self.server.requests if header))

    def test_changed_source_restarts_all_segments(self):
        key=self.add();self.pause(key);self.server.changed=True;self.manager.action(key,'resume')
        self.assertEqual(self.manager.path(self.complete(key)).read_bytes(),CHANGED)

    def test_noncompliant_ranges_fall_back_without_corruption(self):
        for path in ['/ignore','/bad-range','/no-head']:
            with self.subTest(path=path):
                job=self.complete(self.add(path));self.assertEqual(self.manager.path(job).read_bytes(),DATA)
                self.assertEqual(job['transfer_mode'],'single')

    def test_content_disposition_preserves_app_installer_name(self):
        job=self.complete(self.add('/download'));self.assertEqual(job['name'],'App Setup.exe')
        self.assertEqual(self.manager.path(job).read_bytes(),DATA)

    def test_directory_listing_is_bounded_to_selected_folder(self):
        result=discover_folder(self.url+'/folder/')
        self.assertEqual({item['path'] for item in result['files']},{'report.pdf','installer.exe','sub/app.apk'})
        self.assertTrue(all(item['url'].startswith(self.url+'/folder/') for item in result['files']))

    def test_folder_tree_and_partial_names_do_not_collide(self):
        jobs=self.manager.add(items=[{'url':self.url+'/file','path':'sub/setup.exe'},{'url':self.url+'/file','path':'sub/setup.exe.part'}],folder_name='Apps')
        first,second=[self.complete(j['id']) for j in jobs]
        self.assertEqual(self.manager.path(first).parent,self.manager.path(second).parent)
        self.manager.action(first['id'],'remove')
        self.assertEqual(self.manager.path(second).read_bytes(),DATA)

    def test_manifest_rejects_traversal_collisions_and_file_directory_conflicts(self):
        batches=[[{'url':self.url+'/file','path':'../bad.exe'}],[{'url':self.url+'/file','path':'A.exe'},{'url':self.url+'/file','path':'a.exe'}],[{'url':self.url+'/file','path':'dir'},{'url':self.url+'/file','path':'dir/file'}]]
        for items in batches:
            with self.subTest(items=items),self.assertRaises(ValueError):self.manager.add(items=items,folder_name='Apps')
        self.assertEqual(self.manager.snapshot()['jobs'],[])

    def test_connection_setting_is_persistent_and_validated(self):
        self.manager.configure(16);self.manager.close();self.manager=DownloadManager(self.folder.name)
        self.assertEqual(self.manager.snapshot()['connections'],16)
        for value in [0,3,32,True,'8']:
            with self.assertRaises(ValueError):self.manager.configure(value)
