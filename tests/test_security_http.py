"""HTTP boundary and resource regressions against a temporary local server."""
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from library import Library
from server import PortalServer


class HTTPBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.lib=Library(Path(self.temp.name)/'data',Path(self.temp.name)/'storage')
        self.server=PortalServer(('127.0.0.1',0),self.lib,'https://portal.example.com')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path,method='GET',body=None,headers=None):
        conn=http.client.HTTPConnection(*self.server.server_address,timeout=5)
        conn.request(method,path,body=body,headers=headers or {})
        response=conn.getresponse();result=response.status,dict(response.getheaders()),response.read()
        conn.close();return result

    def test_untrusted_hosts_cannot_read_or_mutate_private_routes(self):
        for path in ['/internal/settings','/','/api/reports']:
            self.assertEqual(self.request(path,headers={'Host':'attacker.example'})[0],421)
        self.assertEqual(self.request('/internal/settings/api-key','POST',headers={
            'Host':'attacker.example','Origin':'http://attacker.example','X-Portal-Request':'1'})[0],421)
        self.assertEqual(self.request('/internal/settings',headers={'Host':'portal.example.com'})[0],200)
        self.assertEqual(self.request('/health')[0],200)

    def test_nonascii_api_key_is_denied_without_server_error(self):
        self.assertEqual(self.request('/api/reports',headers={'X-Api-Key':'\xe9'})[0],401)

    def test_symlink_directory_index_is_not_served(self):
        report=self.lib.import_report(b'<h1>Example</h1>','example.html')
        root=self.lib.reports/report['id'];nested=root/'nested';nested.mkdir()
        outside=Path(self.temp.name)/'outside.txt';outside.write_text('outside synthetic marker')
        (nested/'index.html').symlink_to(outside)
        status,_,body=self.request(f"/files/{report['id']}/nested/")
        self.assertEqual(status,404);self.assertNotIn(b'outside synthetic marker',body)
        (nested/'index.html').unlink();(nested/'index.html').write_text('<h1>Inside</h1>')
        status,headers,body=self.request(f"/files/{report['id']}/nested/")
        self.assertEqual(status,200);self.assertIn('text/html',headers['Content-Type'])
        self.assertIn('sandbox',headers['Content-Security-Policy'])

    def test_public_snapshot_directory_index_cannot_follow_symlinks(self):
        report=self.lib.import_report(b'<h1>Example</h1>','example.html')
        share=self.lib.save_share({'report_id':report['id']})
        info=self.lib.share(share['token'])
        root=self.lib.shares/share['token']/info['version'];nested=root/'nested';nested.mkdir()
        outside=Path(self.temp.name)/'private.txt';outside.write_text('private synthetic marker')
        (nested/'index.html').symlink_to(outside)
        status,_,body=self.request(share['url']+'files/nested/')
        self.assertEqual(status,404);self.assertNotIn(b'private synthetic marker',body)

    def test_ambiguous_content_length_is_rejected(self):
        conn=socket.create_connection(self.server.server_address,timeout=5)
        try:
            request=(f'POST /internal/reports HTTP/1.0\r\nHost: 127.0.0.1:{self.server.server_port}\r\n'
                     'X-Portal-Request: 1\r\nContent-Type: text/html\r\nContent-Length: 1\r\nContent-Length: 8\r\n\r\nx').encode()
            conn.sendall(request)
            self.assertIn(b'400',conn.recv(4096).split(b'\r\n')[0])
        finally:conn.close()

    def test_busy_server_rejects_work_and_recovers(self):
        # One held request occupies the only slot; excess sockets do not spawn workers.
        self.server.request_slots=threading.BoundedSemaphore(1)
        conn=socket.create_connection(self.server.server_address,timeout=5)
        try:
            conn.sendall(b'GET /health HTTP/1.0\r\n')
            deadline=time.monotonic()+3
            while self.server.request_slots.acquire(blocking=False):
                self.server.request_slots.release()
                if time.monotonic()>deadline:self.fail('Request was not admitted')
                time.sleep(.01)
            self.assertEqual(self.request('/health')[0],503)
        finally:conn.close()
        deadline=time.monotonic()+3
        while self.request('/health')[0]!=200:
            if time.monotonic()>deadline:self.fail('Request slot was not released')
            time.sleep(.01)
