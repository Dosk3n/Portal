from contextlib import closing
import http.client
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from library import Library
from server import PortalServer
from watcher import Watcher


def bundle(entries):
    target=io.BytesIO()
    with zipfile.ZipFile(target,'w') as archive:
        for path,content in entries.items():archive.writestr(path,content)
    return target.getvalue()


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.lib=Library(self.base/'data',self.base/'media')
        self.report=self.lib.import_report(bundle({'index.html':'<title>A useful report</title><meta name="description" content="A small summary"><p>Unusual café research</p><img src="assets/image.svg"><script>document.body.dataset.interactive="yes"</script>',
            'assets/image.svg':'<svg xmlns="http://www.w3.org/2000/svg"><text>Asset</text></svg>'}), 'report.zip',{'topic':'Research','tags':['gift','ideas']})
        self.server=PortalServer(('127.0.0.1',0),self.lib,base_url='https://portal.example.com')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path,method='GET',body=None,headers=None):
        headers=dict(headers or {})
        if isinstance(body,dict):
            body=json.dumps(body);headers.setdefault('Content-Type','application/json')
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        conn.request(method,path,body=body,headers=headers)
        response=conn.getresponse();result=response.status,dict(response.getheaders()),response.read();conn.close();return result

    def api(self,path,method='GET',body=None,key=None,headers=None):
        return self.request('/api'+path,method,body,{'X-Api-Key':key or self.lib.setting('api_key'),**(headers or {})})

    def test_each_submission_is_new_and_search_filters(self):
        second=self.lib.import_report(b'<title>A useful report</title>Unusual research','report.html',{'topic':'Other'})
        self.assertNotEqual(self.report['id'],second['id'])
        self.assertEqual(self.lib.search({'q':'café!!!','topic':'Research','tag':'gift'})['total'],1)
        self.assertEqual(self.lib.search({'source':"' OR 1=1 --"})['total'],0)
        self.assertEqual(self.lib.search({'q':'document'})['total'],0)
        self.assertEqual(self.lib.search({'page':'100','limit':'1'})['page'],2)
        self.assertEqual(self.lib.facets()['topics'][1]['name'],'Research')

    def test_api_and_browser_auth_boundaries_and_key_rotation(self):
        self.assertEqual(self.request('/api/reports')[0],401)
        self.assertEqual(self.api('/reports')[0],200)
        self.assertEqual(self.request('/internal/reports')[0],200)
        self.assertEqual(self.request('/internal/settings/api-key','POST')[0],403)
        self.assertEqual(self.request('/internal/settings/api-key','POST',headers={'X-Portal-Request':'1','Origin':'null'})[0],403)
        old=self.lib.setting('api_key')
        self.assertEqual(self.request('/internal/settings/api-key','POST',headers={'X-Portal-Request':'1'})[0],200)
        self.assertEqual(self.api('/reports',key=old)[0],401)
        self.assertEqual(self.api('/settings')[0],404)
        restored=Library(self.lib.data,self.lib.storage)
        self.assertEqual(restored.setting('api_key'),self.lib.setting('api_key'))
        for path in ['/','/internal/settings',self.report['url'],'/api/reports']:
            self.assertEqual(self.request(path,headers={'X-Portal-Public':'1'})[0],404)

    def test_raw_and_multipart_upload(self):
        status,_,body=self.api('/reports','POST',b'<title>Raw upload</title>hello',headers={'Content-Type':'text/html'})
        self.assertEqual(status,201,body)
        boundary='test-boundary'
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.html"\r\nContent-Type: text/html\r\n\r\n<title>Multipart</title>\r\n--{boundary}\r\nContent-Disposition: form-data; name="topic"\r\n\r\nGifts\r\n--{boundary}--\r\n').encode()
        status,_,result=self.api('/reports','POST',body,headers={'Content-Type':'multipart/form-data; boundary='+boundary})
        self.assertEqual(status,201,result);self.assertEqual(json.loads(result)['topic'],'Gifts')

    def test_unsafe_and_ambiguous_bundles_do_not_import(self):
        for entries in [{'../escape.html':'bad'},{'/escape.html':'bad'},{'x\\escape.html':'bad'},
                        {'a.html':'a','b.html':'b'},{'x.txt':'no report'}]:
            with self.subTest(entries=entries),self.assertRaises(ValueError):self.lib.import_report(bundle(entries),'bad.zip')
        self.assertEqual(self.lib.search({})['total'],1)
        self.assertFalse((self.base/'escape.html').exists())
        self.assertEqual(len([p for p in self.lib.reports.iterdir() if not p.name.startswith('.')]),1)

    def test_reader_assets_sandbox_and_containment(self):
        status,headers,body=self.request(self.report['url'])
        self.assertEqual(status,200);self.assertIn(b'dataset.interactive',body)
        self.assertIn('sandbox allow-scripts',headers['Content-Security-Policy'])
        self.assertNotIn('allow-same-origin',headers['Content-Security-Policy'])
        prefix=f"/files/{self.report['id']}/"
        self.assertEqual(self.request(prefix+'assets/image.svg')[0],200)
        self.assertEqual(self.request(self.report['url'],'HEAD')[2],b'')
        for path in [prefix+'../portal.sqlite',prefix+'%2e%2e/portal.sqlite','/static/../server.py']:
            self.assertEqual(self.request(path)[0],404)
        (self.lib.reports/self.report['id']/'escape').symlink_to(self.lib.data/'portal.sqlite')
        self.assertEqual(self.request(prefix+'escape')[0],404)

    def test_password_protects_assets_and_snapshot_update_keeps_access(self):
        share=self.lib.save_share({'report_id':self.report['id'],'password':'secret','expires_in_days':7})
        base=share['url'];entry=base+'files/index.html'
        self.assertEqual(self.request(base)[0],200)
        self.assertEqual(self.request(entry)[0],403)
        self.assertEqual(self.request(base+'files/assets/image.svg')[0],403)
        self.assertEqual(self.request(base+'_portal.css',headers={'X-Portal-Public':'1'})[0],200)
        self.assertEqual(self.request(base+'unlock','POST',urlencode({'password':'wrong'}),{'Content-Type':'application/x-www-form-urlencoded'})[0],403)
        status,headers,_=self.request(base+'unlock','POST',urlencode({'password':'secret'}),{'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(status,303);self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('Secure',headers['Set-Cookie'])
        cookie=headers['Set-Cookie'].split(';',1)[0]
        original=self.request(entry,headers={'Cookie':cookie})[2]
        (self.lib.reports/self.report['id']/'index.html').write_text('Changed source')
        self.assertEqual(self.request(entry,headers={'Cookie':cookie})[2],original)
        newer=self.lib.import_report(b'<title>Replacement</title>new report','new.html')
        updated=self.lib.save_share({'report_id':newer['id']},share['token'])
        self.assertEqual(updated['url'],base)
        self.assertEqual(updated['expires_at'],share['expires_at'])
        self.assertTrue(updated['password_protected'])
        self.assertIn(b'new report',self.request(entry,headers={'Cookie':cookie})[2])
        self.lib.save_share({'password':'different'},share['token'])
        self.assertEqual(self.request(entry,headers={'Cookie':cookie})[0],403)

    def test_expiry_revocation_and_independent_assets(self):
        share=self.lib.save_share({'report_id':self.report['id'],'expires_in_days':1})
        base=share['url']
        self.assertEqual(self.request(base)[0],302)
        self.assertEqual(self.request(base+'files/assets/image.svg')[0],200)
        for path in ['files/../../portal.sqlite','files/%2e%2e/portal.sqlite','files/not-present.html']:
            self.assertEqual(self.request(base+path)[0],404)
        with self.lib.db() as db:db.execute('UPDATE shares SET expires_at=? WHERE token=?',(time.time()-1,share['token']))
        self.assertEqual(self.request(base)[0],404)
        self.assertEqual(self.request(base+'files/assets/image.svg')[0],404)
        self.lib.revoke(share['token']);self.assertFalse((self.lib.shares/share['token']).exists())

    def test_share_api_create_replace_and_revoke(self):
        status,_,body=self.api('/shares','POST',{'report_id':self.report['id'],'expires_in_days':1})
        self.assertEqual(status,201,body)
        share=json.loads(body)
        self.assertEqual(self.api('/shares/'+share['token'],'PATCH',{'password':'new'})[0],200)
        self.assertEqual(self.api('/shares/'+share['token'],'DELETE')[0],200)
        self.assertEqual(self.request(share['url'])[0],404)

    def test_watcher_disabled_then_consumes_bundle_and_keeps_failure(self):
        inbox=self.base/'inbox';inbox.mkdir()
        self.lib.add_watcher(str(inbox))
        good=inbox/'good.zip';good.write_bytes(bundle({'index.html':'<title>Watched</title>','a.svg':'<svg/>'}))
        bad=inbox/'bad.zip';bad.write_bytes(b'broken zip')
        watcher=Watcher(self.lib,stable_seconds=0)
        watcher.scan();watcher.scan();self.assertTrue(good.exists())
        self.lib.set_setting('watching_enabled',True)
        watcher.scan();watcher.scan()
        self.assertFalse(good.exists());self.assertTrue(bad.exists())
        self.assertEqual(self.lib.search({'q':'Watched'})['total'],1)
        watcher.scan();self.assertEqual(self.lib.search({'q':'Watched'})['total'],1)
        folder=inbox/'folder';folder.mkdir();(folder/'index.html').write_text('<title>Folder report</title>');(folder/'image.svg').write_text('<svg/>')
        watcher.scan();watcher.scan();self.assertFalse(folder.exists())
        self.assertEqual(self.lib.search({'q':'Folder'})['total'],1)

    def test_watcher_blocks_managed_storage_and_symlinks(self):
        for path in [self.lib.reports,self.lib.shares,self.lib.storage,self.lib.data,self.base]:
            with self.assertRaises(ValueError):self.lib.add_watcher(str(path))
        inbox=self.base/'inbox';inbox.mkdir();outside=self.base/'outside.html';outside.write_text('private')
        (inbox/'linked.html').symlink_to(outside)
        self.lib.add_watcher(str(inbox));self.lib.set_setting('watching_enabled',True)
        watcher=Watcher(self.lib,stable_seconds=0);watcher.scan();watcher.scan()
        self.assertTrue(outside.exists());self.assertEqual(self.lib.search({})['total'],1)

    def test_watch_import_ledger_prevents_duplicate_after_failed_consume(self):
        inbox=self.base/'inbox';inbox.mkdir();path=inbox/'report.html';payload=b'<title>Consume once</title>';path.write_bytes(payload)
        import hashlib
        self.lib.import_report(payload,path.name,watch_identity=(str(path),hashlib.sha256(payload).hexdigest()))
        self.lib.add_watcher(str(inbox));self.lib.set_setting('watching_enabled',True)
        watcher=Watcher(self.lib,stable_seconds=0);watcher.scan();watcher.scan()
        self.assertFalse(path.exists());self.assertEqual(self.lib.search({'q':'Consume once'})['total'],1)

    def test_failed_snapshot_preserves_existing_share(self):
        share=self.lib.save_share({'report_id':self.report['id']})
        import shutil
        shutil.rmtree(self.lib.reports/self.report['id'])
        with self.assertRaises(OSError):self.lib.save_share({},share['token'])
        self.assertEqual(self.request(share['url']+'files/index.html')[0],200)


if __name__=='__main__':unittest.main()
