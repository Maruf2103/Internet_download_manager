import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from idm.chrome_host import extension_id
from idm.manager import DownloadManager
from idm.web import DashboardServer
from test_manager import fixture_server, wait_for, PAYLOAD

ROOT=Path(__file__).resolve().parents[1]

class ChromeHostTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.manager=DownloadManager(self.folder.name)
        self.server=DashboardServer(('127.0.0.1',0),self.manager)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.fixture,self.fixture_thread=fixture_server()
        self.env=dict(os.environ,FLOW_PORT=str(self.server.server_port),NO_PROXY='127.0.0.1',no_proxy='127.0.0.1')

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.manager.close()
        self.fixture.shutdown();self.fixture.server_close();self.fixture_thread.join();self.folder.cleanup()

    def native(self,messages,origin=None):
        payload=b''.join(struct.pack('<I',len(data))+data for data in [json.dumps(message).encode() for message in messages])
        result=subprocess.run([sys.executable,'-m','idm.chrome_host',origin or f'chrome-extension://{extension_id()}/'],input=payload,capture_output=True,cwd=ROOT,env=self.env,timeout=15)
        responses=[];data=result.stdout
        while data:
            length=struct.unpack('<I',data[:4])[0];responses.append(json.loads(data[4:4+length]));data=data[4+length:]
        return result,responses

    def test_framed_ping_and_queue_through_native_bridge(self):
        url=f'http://127.0.0.1:{self.fixture.server_port}/setup.exe'
        result,responses=self.native([{'action':'ping'},{'action':'queue','items':[{'url':url,'name':'Setup.exe'}]}])
        self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assertEqual(responses[0]['extension_id'],extension_id());self.assertTrue(responses[1]['ok'])
        job=wait_for(self.manager,responses[1]['ids'][0],lambda j:j['state']=='completed')
        self.assertEqual(job['name'],'Setup.exe');self.assertEqual(self.manager.path(job).read_bytes(),PAYLOAD)

    def test_unauthorized_extension_cannot_use_native_host(self):
        result,responses=self.native([{'action':'ping'}],'chrome-extension://'+'a'*32+'/')
        self.assertEqual(result.returncode,1);self.assertEqual(responses,[])

    def test_invalid_messages_fail_without_adding_jobs(self):
        result,responses=self.native([{'action':'execute','command':'bad'},{'action':'queue','items':[{'url':'file:///etc/passwd'}]}])
        self.assertEqual(result.returncode,0)
        self.assertTrue(all(not response['ok'] for response in responses));self.assertEqual(self.manager.snapshot()['jobs'],[])

    def test_oversized_native_frame_is_rejected(self):
        result=subprocess.run([sys.executable,'-m','idm.chrome_host',f'chrome-extension://{extension_id()}/'],input=struct.pack('<I',2**31),capture_output=True,cwd=ROOT,env=self.env,timeout=5)
        self.assertEqual(result.returncode,1);self.assertEqual(result.stdout,b'')
