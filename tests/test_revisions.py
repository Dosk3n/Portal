"""Revision retention, migration and API/snapshot boundaries."""
import http.client
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from library import Library, RevisionConflict
from server import PortalServer


def package(text, asset='new'):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        archive.writestr('index.html',f'<title>{text}</title><p>{text}</p><img src="image.svg">')
        archive.writestr('image.svg',f'<svg xmlns="http://www.w3.org/2000/svg"><text>{asset}</text></svg>')
    return output.getvalue()


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.lib=Library(self.root/'data',self.root/'storage')
        self.report=self.lib.import_report(package('First','old'),'report.zip',{'topic':'Research','tags':['original']})
        self.id=self.report['id']

    def tearDown(self):self.temp.cleanup()

    def revise(self,text):
        return self.lib.import_report(package(text), 'report.zip', report_id=self.id)

    def test_same_link_new_content_assets_search_and_metadata(self):
        updated=self.revise('Second')
        self.assertEqual(updated['id'],self.id)
        self.assertEqual(updated['reader_url'],self.report['reader_url'])
        self.assertEqual(updated['topic'],'Research');self.assertEqual(updated['tags'],['original'])
        self.assertEqual(updated['created_at'],self.report['created_at'])
        self.assertNotEqual(updated['url'],self.report['url'])
        self.assertEqual(self.lib.search({'q':'First'})['total'],0)
        self.assertEqual(self.lib.search({'q':'Second'})['total'],1)
        self.assertIn('old', (self.lib.package_path(self.id,self.report['current_version'])/'image.svg').read_text())
        self.assertIn('new', (self.lib.package_path(self.id)/'image.svg').read_text())
        self.assertEqual(self.lib.facets()['total'],1)

    def test_default_three_total_prunes_files_and_old_preview(self):
        for name in ('Second','Third','Fourth','Fifth'):self.revise(name)
        history=self.lib.versions(self.id)
        self.assertEqual([v['number'] for v in history],[5,4,3])
        self.assertEqual(len(list(self.lib.reports.iterdir())),3)
        self.assertFalse((self.lib.reports/self.id).exists())
        with self.assertRaises(LookupError):self.lib.package_path(self.id,self.id)
        self.assertTrue(history[0]['current']);self.assertFalse(history[1]['current'])

    def test_one_version_and_settings_reduction_across_reports(self):
        self.revise('Second');self.revise('Third')
        other=self.lib.import_report(package('Other'),'report.zip')
        self.lib.import_report(package('Other updated'),'report.zip',report_id=other['id'])
        self.assertEqual(self.lib.retention_preview(1),3)
        self.lib.update_settings({'versions_to_keep':1})
        self.assertEqual(len(list(self.lib.reports.iterdir())),2)
        self.assertEqual(len(self.lib.versions(self.id)),1)
        self.revise('Fourth');self.assertEqual(len(self.lib.versions(self.id)),1)
        for value in (0,-1,101,True,2.5,'3'):
            with self.assertRaises(ValueError):self.lib.update_settings({'versions_to_keep':value})
        self.assertEqual(self.lib.setting('versions_to_keep'),1)
        restarted=Library(self.root/'data',self.root/'storage')
        self.assertEqual(restarted.setting('versions_to_keep'),1)

    def test_snapshots_survive_pruning_and_refresh_only_explicitly(self):
        share=self.lib.save_share({'report_id':self.id,'password':'test','expires_in_days':7})
        before=self.lib.share(share['token']);self.lib.update_settings({'versions_to_keep':1})
        self.revise('Second')
        self.lib.save_share({'report_id':self.id},share['token'])
        unchanged=self.lib.share(share['token'])
        self.assertIn('First',(self.lib.shares/share['token']/unchanged['version']/'index.html').read_text())
        self.lib.save_share({'refresh_snapshot':True},share['token'])
        after=self.lib.share(share['token'])
        self.assertIn('Second',(self.lib.shares/share['token']/after['version']/'index.html').read_text())
        self.assertEqual(after['password'],before['password']);self.assertEqual(after['expires_at'],before['expires_at'])
        self.assertEqual(after['token'],before['token'])
        self.lib.delete_report(self.id)
        self.assertEqual(len(list(self.lib.reports.iterdir())),0)
        self.assertTrue((self.lib.shares/share['token']/after['version']/'image.svg').exists())

    def test_restore_creates_current_and_respects_retention(self):
        self.lib.update_settings({'versions_to_keep':2})
        self.revise('Second')
        restored=self.lib.restore_version(self.id,self.report['current_version'])
        self.assertEqual(restored['title'],'First')
        self.assertEqual([v['number'] for v in self.lib.versions(self.id)],[3,2])
        self.assertIn('old',(self.lib.package_path(self.id)/'image.svg').read_text())
        self.assertEqual(self.lib.search({'q':'First'})['total'],1)

    def test_metadata_edit_does_not_copy_package(self):
        changed=self.lib.edit_report(self.id,{'title':'Renamed','topic':'Gifts','tags':'ideas, options','date':'2026-10-16'})
        self.assertEqual(changed['current_version'],self.report['current_version'])
        self.assertEqual(len(self.lib.versions(self.id)),1)
        self.assertEqual(self.lib.versions(self.id)[0]['title'],'Renamed')
        self.assertEqual(self.lib.search({'q':'Renamed','topic':'Gifts','tag':'ideas'})['total'],1)
        with self.assertRaises(ValueError):self.lib.edit_report(self.id,{'entry':'../../secret'})

    def test_invalid_or_failed_revision_preserves_existing_and_leaves_no_files(self):
        with self.assertRaises(ValueError):self.lib.import_report(b'<img src="missing.png">','report.html',report_id=self.id)
        with patch('library.copy_tree',side_effect=OSError('offline')):
            with self.assertRaises(OSError):self.revise('Second')
        self.assertEqual(self.lib.report(self.id),self.report)
        self.assertEqual(len(list(self.lib.reports.iterdir())),1)
        self.assertEqual(len(self.lib.versions(self.id)),1)

    def test_stale_and_parallel_revisions_do_not_overwrite(self):
        expected=self.report['current_version'];outcomes=[]
        def update():
            try:self.lib.import_report(package('Second'),'report.zip',report_id=self.id,expected=expected);outcomes.append('saved')
            except RevisionConflict:outcomes.append('conflict')
        threads=[threading.Thread(target=update) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertCountEqual(outcomes,['saved','conflict'])
        self.assertEqual(len(self.lib.versions(self.id)),2)
        with self.assertRaises(RevisionConflict):self.lib.edit_report(self.id,{'title':'stale','expected_version':expected})

    def test_version_cannot_be_read_or_restored_under_another_report(self):
        other=self.lib.import_report(package('Other'),'report.zip')
        with self.assertRaises(LookupError):self.lib.package_path(other['id'],self.id)
        with self.assertRaises(LookupError):self.lib.restore_version(other['id'],self.id)

    def test_legacy_catalogue_migrates_in_place_and_idempotently(self):
        key=self.lib.setting('api_key')
        with self.lib.db() as db:
            db.execute('DROP TABLE report_versions')
            db.execute('ALTER TABLE reports DROP COLUMN current_version')
            db.execute("DELETE FROM settings WHERE key='versions_to_keep'")
        for _ in range(2):
            migrated=Library(self.root/'data',self.root/'storage')
            self.assertEqual(migrated.setting('api_key'),key)
            self.assertEqual(len(migrated.versions(self.id)),1)
            self.assertEqual(migrated.package_path(self.id),migrated.reports/self.id)
            self.assertEqual(migrated.setting('versions_to_keep'),3)
        self.lib=migrated;self.revise('After migration')
        self.assertEqual(len(self.lib.versions(self.id)),2)

    def test_failed_pruning_keeps_current_and_can_be_retried(self):
        self.lib.update_settings({'versions_to_keep':1})
        import shutil
        remove=shutil.rmtree
        def fail_old(path,*args,**kwargs):
            if Path(path)==self.lib.reports/self.id:raise OSError('NAS unavailable')
            return remove(path,*args,**kwargs)
        with patch('library.shutil.rmtree',side_effect=fail_old):
            with self.assertRaises(OSError):self.revise('Second')
        self.assertEqual(self.lib.report(self.id)['title'],'Second')
        self.assertTrue((self.lib.package_path(self.id)/'index.html').exists())
        self.assertEqual(len(self.lib.versions(self.id)),2)
        self.lib.prune_versions()
        self.assertEqual(len(self.lib.versions(self.id)),1)
        self.assertFalse((self.lib.reports/self.id).exists())

    def test_revising_watched_report_invalidates_old_duplicate_identity(self):
        with self.lib.db() as db:db.execute('INSERT INTO watch_imports VALUES (?,?,?)',('/inbox/report.zip','original-digest',self.id))
        self.revise('Second')
        with self.lib.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM watch_imports').fetchone()[0],0)

    def test_http_revisions_and_settings_boundaries(self):
        server=PortalServer(('127.0.0.1',0),self.lib)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,method='GET',body=None,private=False,**headers):
            if private:headers['X-Portal-Request']='1'
            else:headers['X-Api-Key']=self.lib.setting('api_key')
            if isinstance(body,dict):body=json.dumps(body);headers['Content-Type']='application/json'
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            conn.request(method,path,body,headers);r=conn.getresponse();result=(r.status,r.read());conn.close();return result
        try:
            code,index=request('/',private=True)
            self.assertEqual(code,200);self.assertNotIn(b'__ASSET_VERSION__',index)
            self.assertIn(('/static/app.js?v='+server.asset_version).encode(),index)
            self.assertEqual(request('/static/app.js?v='+server.asset_version,private=True)[0],200)
            path='/api/reports/'+self.id
            code,body=request(path+'/versions','POST',package('Second'),**{'Content-Type':'application/zip','X-Report-Version':self.report['current_version']})
            self.assertEqual(code,201);updated=json.loads(body)
            self.assertEqual(request(path+'/versions')[0],200)
            self.assertEqual(request(updated['url'],private=True)[0],200)
            self.assertEqual(request(path+'/files/index.html')[0],200)
            self.assertEqual(request(path+'/versions/'+self.id+'/files/image.svg')[0],200)
            self.assertEqual(request(path+'/versions','POST',package('Stale'),**{'Content-Type':'application/zip','X-Report-Version':self.id})[0],409)
            self.assertEqual(request(path,'PATCH',{'title':'Updated details'})[0],200)
            self.assertEqual(request('/api/settings','PATCH',{'versions_to_keep':1})[0],404)
            code,body=request('/internal/settings/retention-preview?limit=1',private=True)
            self.assertEqual(code,200);self.assertEqual(json.loads(body)['versions_to_remove'],1)
            self.assertEqual(request('/internal/settings','PATCH',{'versions_to_keep':1},private=True)[0],200)
            self.assertEqual(request(path+'/versions/'+self.id+'/files/index.html')[0],404)
            self.assertEqual(request(path+'/restore','POST',{})[0],400)
            self.assertEqual(request('/api/docs')[0],200)
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
