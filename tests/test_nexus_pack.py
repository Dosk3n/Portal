"""Portable Nexus client tests against an isolated real Portal HTTP server."""
import base64
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
sys.path.insert(0,str(ROOT/'integrations/nexus'))
from library import Library
from server import PortalServer
from nexus_tools_portal import client as module
from nexus_tools_portal.client import PortalClient,PortalError,build_bundle


class NexusPackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.lib=Library(root/'data',root/'storage')
        self.server=PortalServer(('127.0.0.1',0),self.lib)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.client=PortalClient(self.base,self.lib.setting('api_key'))

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def create(self,text='First'):
        return self.client.upload('<title>'+text+'</title><h1>'+text+'</h1>',metadata={'topic':'Research','tags':['ideas']})

    def test_html_assets_unicode_metadata_and_search(self):
        svg='<svg xmlns="http://www.w3.org/2000/svg"><text>café</text></svg>'
        image=b'\x89PNG\r\n\x1a\nsynthetic'
        html='<title>Gift ideas</title><h1>Useful gifts</h1><img src="assets/picture with spaces.svg"><img src="assets/image.png">'
        report=self.client.upload(html,[{'path':'assets/picture with spaces.svg','text':svg},
            {'path':'assets/image.png','base64':base64.b64encode(image).decode()}],{'title':'Café ideas','tags':['gifts'],'topic':'Gifts'})
        self.assertEqual(report['reader_url'],self.base+'/read/'+report['id'])
        data=self.client.get_report(report['id'],asset_paths=['assets/picture with spaces.svg','assets/image.png'])
        self.assertEqual(data['html'],html)
        self.assertEqual(base64.b64decode(data['assets'][0]['base64']).decode(),svg)
        self.assertEqual(base64.b64decode(data['assets'][1]['base64']),image)
        results=self.client.search_reports(q='café',topic='Gifts',tag='gifts',page=1,limit=5)
        self.assertEqual(results['total'],1);self.assertEqual(results['reports'][0]['id'],report['id'])
        self.assertNotIn('html',self.client.get_report(report['id'],include_html=False))

    def test_revisions_conflicts_versions_restore_and_metadata(self):
        first=self.create()
        second=self.client.upload('<title>Second</title><h1>New findings</h1>',report_id=first['id'],expected_version=first['current_version'])
        self.assertEqual(first['reader_url'],second['reader_url'])
        with self.assertRaises(PortalError) as error:
            self.client.upload('<p>Stale</p>',report_id=first['id'],expected_version=first['current_version'])
        self.assertEqual(error.exception.status,409)
        self.assertIn('First',self.client.get_report(first['id'],version=first['current_version'])['html'])
        restored=self.client.restore_version(first['id'],first['current_version'],second['current_version'])
        self.assertEqual(restored['title'],'First')
        changed=self.client.edit_report_details(first['id'],title='Renamed',topic='Conversations',tags=[],expected_version=restored['current_version'])
        self.assertEqual(changed['current_version'],restored['current_version']);self.assertEqual(changed['tags'],[])
        self.assertEqual(len(self.client.list_versions(first['id'])['versions']),3)
        self.client.upload('<title>Fourth</title><p>Latest</p>',report_id=first['id'],expected_version=restored['current_version'])
        with self.assertRaises(PortalError):self.client.get_report(first['id'],version=first['current_version'])
        self.assertEqual(len(self.client.list_versions(first['id'])['versions']),3)

    def test_share_defaults_refresh_access_removal_and_revoke(self):
        first=self.create()
        share=self.client.create_share(first['id'],password='synthetic')
        before=self.lib.share(share['token']);self.assertTrue(before['expires_at'])
        second=self.client.upload('<title>Second</title><p>New findings</p>',report_id=first['id'],expected_version=first['current_version'])
        self.client.update_share(share['token'],password='changed')
        unchanged=self.lib.share(share['token'])
        self.assertIn('First',(self.lib.shares/share['token']/unchanged['version']/'index.html').read_text())
        updated=self.client.update_share(share['token'],refresh_snapshot=True)
        after=self.lib.share(share['token'])
        self.assertEqual(updated['url'],share['url']);self.assertEqual(after['expires_at'],before['expires_at'])
        self.assertEqual(after['password'],unchanged['password'])
        self.assertIn('Second',(self.lib.shares/share['token']/after['version']/'index.html').read_text())
        removed=self.client.update_share(share['token'],change_expiry=True,expires_in_days=None,password='')
        self.assertIsNone(removed['expires_at']);self.assertFalse(removed['password_protected'])
        self.assertEqual(len(self.client.list_shares(first['id'])['shares']),1)
        self.assertTrue(self.client.revoke_share(share['token'])['revoked'])
        self.assertEqual(self.client.list_shares()['shares'],[])
        self.assertEqual(self.lib.report(first['id'])['current_version'],second['current_version'])

    def test_bundle_rejects_unsafe_duplicate_and_conflicting_paths(self):
        for path in ('../escape','/absolute','a/../b','a\\b','a//b','a/./b','a:stream','index.html'):
            with self.subTest(path=path),self.assertRaises(ValueError):build_bundle('<p>Test</p>',[{'path':path,'text':'x'}])
        for assets in ([{'path':'asset','text':'x'},{'path':'asset','text':'y'}],
                       [{'path':'asset','text':'x'},{'path':'asset/nested','text':'y'}],
                       [{'path':'bad','base64':'not base64!'}],
                       [{'path':'bad','text':'x','base64':'eA=='}]):
            with self.assertRaises(ValueError):build_bundle('<p>Test</p>',assets)
        with self.assertRaises(ValueError):build_bundle('',[])
        payload=build_bundle('<p>Test</p>',[{'path':'assets/style.css','text':'body{}'}])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:self.assertEqual(archive.namelist(),['index.html','assets/style.css'])

    def test_bundle_and_download_bounds(self):
        with patch.object(module,'MAX_PACKAGE',10),self.assertRaises(ValueError):build_bundle('<p>Oversized</p>')
        with patch.object(module,'MAX_FILES',1),self.assertRaises(ValueError):build_bundle('<p>Test</p>',[{'path':'x','text':'y'}])
        with patch.object(module,'MAX_UPLOAD',10),self.assertRaises(ValueError):build_bundle('<p>Test</p>')
        self.create()
        with patch.object(module,'MAX_RESPONSE',10),self.assertRaises(PortalError) as error:self.client.search_reports()
        self.assertEqual(error.exception.status,413)

    def test_auth_errors_configuration_and_uuid_validation(self):
        key='synthetic-invalid-private-key'
        with self.assertRaises(PortalError) as error:PortalClient(self.base+'/api',key).search_reports()
        self.assertEqual(error.exception.status,401);self.assertNotIn(key,str(error.exception))
        for url in ('http://remote.example.com','https://user:password@example.com','https://@example.com','https://example.com/wrong','https://example.com/api?q=x',None):
            with self.subTest(url=url),self.assertRaises(ValueError):PortalClient(url,'synthetic')
        for value in ('../report','not-a-uuid','',None):
            with self.assertRaises(ValueError):self.client.get_report(value)
        self.assertEqual(PortalClient(self.base+'/api/',self.lib.setting('api_key')).search_reports()['total'],0)

    def test_access_changes_require_explicit_expiry_flag(self):
        first=self.create();share=self.client.create_share(first['id'])
        with self.assertRaises(ValueError):self.client.update_share(share['token'],expires_in_days=30)
        with self.assertRaises(ValueError):self.client.update_share(share['token'])
        after=self.lib.share(share['token']);self.assertIsNotNone(after['expires_at'])
        with self.assertRaises(ValueError):self.client.edit_report_details(first['id'])

    def test_redirects_do_not_forward_api_key(self):
        received=[]
        class Target(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                received.append(self.headers.get('X-Api-Key'));self.send_response(200);self.end_headers()
        target=ThreadingHTTPServer(('127.0.0.1',0),Target)
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(302);self.send_header('Location','http://127.0.0.1:'+str(target.server_port)+'/capture');self.end_headers()
        redirect=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        threads=[]
        for server in (target,redirect):
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start();threads.append(t)
        try:
            with self.assertRaises(PortalError) as error:PortalClient('http://127.0.0.1:'+str(redirect.server_port),'synthetic-key').search_reports()
            self.assertEqual(error.exception.status,302);self.assertEqual(received,[])
        finally:
            for server in (target,redirect):server.shutdown();server.server_close()
            for t in threads:t.join()


if __name__=='__main__':unittest.main()
