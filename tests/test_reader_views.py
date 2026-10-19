"""Cookie-independent, expiring private views retain opaque sandbox isolation."""
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from library import Library
from server import PortalServer,READER_VIEW_SECONDS


class ReaderViewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.lib=Library(root/'data',root/'storage')
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('index.html','<title>Reader</title><h1>Private</h1><img src="assets/image.png">')
            z.writestr('assets/image.png',b'\x89PNG\r\n\x1a\nsynthetic')
        self.report=self.lib.import_report(data.getvalue(),'report.zip')
        self.server=PortalServer(('127.0.0.1',0),self.lib)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path,method='GET',headers=None):
        conn=http.client.HTTPConnection(*self.server.server_address,timeout=5)
        conn.request(method,path,headers=headers or {})
        r=conn.getresponse();result=(r.status,dict(r.getheaders()),r.read());conn.close();return result

    def view(self):
        status,headers,_=self.request(self.lib.report(self.report['id'])['url'])
        self.assertEqual(status,302);return headers['Location']

    def test_scoped_document_and_relative_png_need_no_cookie_or_api_key(self):
        view=self.view()
        self.assertNotIn(self.lib.setting('api_key'),view)
        status,headers,body=self.request(view,headers={'X-Portal-Public':'1'})
        self.assertEqual(status,200);self.assertIn(b'Private',body)
        self.assertIn('sandbox allow-scripts',headers['Content-Security-Policy'])
        self.assertNotIn('allow-same-origin',headers['Content-Security-Policy'])
        self.assertEqual(headers['Cache-Control'],'no-store');self.assertEqual(headers['Referrer-Policy'],'no-referrer')
        asset=view.replace('/files/index.html','/files/assets/image.png')
        status,headers,body=self.request(asset,headers={'X-Portal-Public':'1'})
        self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'image/png')
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff');self.assertTrue(body.startswith(b'\x89PNG'))
        self.assertEqual(self.request(asset,'HEAD')[2],b'')
        self.assertEqual(self.lib.list_shares(),[])
        self.assertEqual(self.request('/api/reports')[0],401)
        self.assertEqual(self.request('/internal/settings',headers={'X-Portal-Public':'1'})[0],404)

    def test_api_stays_keyed_and_returns_raw_html_and_private_head_stays_raw(self):
        path='/api/reports/'+self.report['id']+'/files/index.html'
        self.assertEqual(self.request(path)[0],401)
        status,headers,body=self.request(path,headers={'X-Api-Key':self.lib.setting('api_key')})
        self.assertEqual(status,200);self.assertIn(b'Private',body);self.assertNotIn('Location',headers)
        status,headers,body=self.request(self.report['url'],'HEAD')
        self.assertEqual(status,200);self.assertEqual(body,b'');self.assertNotIn('Location',headers)

    def test_expiry_refresh_and_restart_invalidation(self):
        with patch('server.time.time',return_value=100000):view=self.view()
        with patch('server.time.time',return_value=100000+READER_VIEW_SECONDS-1):self.assertEqual(self.request(view)[0],200)
        with patch('server.time.time',return_value=100000+READER_VIEW_SECONDS):
            self.assertEqual(self.request(view)[0],404)
            fresh=self.view();self.assertNotEqual(view,fresh);self.assertEqual(self.request(fresh)[0],200)
        self.server.reader_secret=b'new server secret after restart'
        self.assertEqual(self.request(fresh)[0],404)

    def test_tampering_cross_report_version_traversal_and_mutations_are_denied(self):
        view=self.view();ticket=view.split('/')[3]
        other=self.lib.import_report(b'<h1>Other private report</h1>','other.html')
        for replacement in (ticket.replace(self.report['id'],other['id'],1),ticket[:-1]+('0' if ticket[-1]!='0' else '1')):
            self.assertEqual(self.request(view.replace(ticket,replacement))[0],404)
        for path in (view.replace('/files/index.html','/files/../index.html'),view.replace('/files/index.html','/files/%2e%2e/index.html'),view.replace('/files/index.html','/internal/settings')):
            self.assertEqual(self.request(path)[0],404)
        for method in ('POST','PATCH','DELETE'):self.assertEqual(self.request(view,method)[0],405)
        (self.lib.package_path(self.report['id'])/'escape').symlink_to(self.lib.data/'portal.sqlite')
        self.assertEqual(self.request(view.replace('/files/index.html','/files/escape'))[0],404)

    def test_deleted_or_pruned_version_invalidates_grant_but_shares_survive(self):
        view=self.view();share=self.lib.save_share({'report_id':self.report['id']})
        self.lib.update_settings({'versions_to_keep':1})
        self.lib.import_report(b'<h1>New</h1>','updated.html',report_id=self.report['id'])
        self.assertEqual(self.request(view)[0],404)
        self.assertEqual(self.request(share['url']+'files/assets/image.png')[0],200)
        fresh=self.view();self.lib.delete_report(self.report['id'])
        self.assertEqual(self.request(fresh)[0],404)
        self.assertEqual(self.request(share['url']+'files/assets/image.png')[0],200)
