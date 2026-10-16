"""Persistent catalogue, immutable report packages, and explicit snapshot sharing."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import uuid
import zipfile
from urllib.parse import quote

from metadata import MetadataParser, read_text
from packages import validate_package
from safe_files import copy_tree

MAX_UPLOAD = 50 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
MAX_FILES = 2000
HTML = {'.html', '.htm', '.xhtml'}


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


class Library:
    def __init__(self, data_dir, storage_dir):
        self.data = Path(data_dir).resolve()
        self.storage = Path(storage_dir).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        # The retrievable API key lives in SQLite. Restrict its containing
        # directory on POSIX filesystems; deployment ownership must match UID.
        self.data.chmod(0o700)
        self.reports = self.storage / 'reports'
        self.shares = self.storage / 'shares'
        self.reports.mkdir(parents=True, exist_ok=True)
        self.shares.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reports(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                    topic TEXT NOT NULL, date TEXT NOT NULL, created_at TEXT NOT NULL,
                    excerpt TEXT NOT NULL, body TEXT NOT NULL, entry TEXT NOT NULL,
                    bytes INTEGER NOT NULL, tags TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS reports_fts USING fts5(id UNINDEXED,title,body,tags);
                CREATE TABLE IF NOT EXISTS shares(
                    token TEXT PRIMARY KEY, report_id TEXT NOT NULL, title TEXT NOT NULL,
                    version TEXT NOT NULL, entry TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, expires_at REAL, password TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS share_sessions(
                    session TEXT PRIMARY KEY, token TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS watch_imports(
                    path TEXT NOT NULL, digest TEXT NOT NULL, report_id TEXT NOT NULL,
                    PRIMARY KEY(path,digest));
                CREATE TABLE IF NOT EXISTS watch_activity(
                    id INTEGER PRIMARY KEY, watcher_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    filename TEXT NOT NULL, outcome TEXT NOT NULL, report_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS watchers(
                    id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL,
                    last_checked TEXT, last_import TEXT, error TEXT NOT NULL DEFAULT '');
            ''')
            for key,value in {'api_key':secrets.token_urlsafe(32),'watching_enabled':False}.items():
                db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(key,json.dumps(value)))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.data / 'portal.sqlite', timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def setting(self, key):
        with self.db() as db:
            return json.loads(db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()[0])

    def set_setting(self,key,value):
        with self.db() as db:
            db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(value),key))

    def settings(self):
        with self.db() as db:
            watchers = [dict(r) for r in db.execute('SELECT * FROM watchers ORDER BY path')]
            activity = [dict(r) for r in db.execute('SELECT * FROM watch_activity ORDER BY id DESC LIMIT 20')]
        return {'api_key': self.setting('api_key'), 'watching_enabled':self.setting('watching_enabled'),
                'watchers':watchers,'activity':activity,'storage':str(self.storage),'max_upload_mb':MAX_UPLOAD//1024//1024}

    def report(self, report_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM reports WHERE id=?',(report_id,)).fetchone()
        if row is None:
            raise LookupError('Report not found.')
        return self.public_report(row)

    @staticmethod
    def public_report(row):
        value = dict(row)
        value.pop('body',None)
        value['tags']=json.loads(value['tags'])
        value['url']=f"/files/{value['id']}/"+quote(value['entry'],safe='/')
        value['reader_url']=f"/read/{value['id']}"
        return value

    def search(self, params):
        page=max(1,int(params.get('page','1')))
        limit=min(100,max(1,int(params.get('limit','24'))))
        conditions=[]; values=[]
        terms=re.findall(r'[^\W_]+',params.get('q',''))
        if terms:
            conditions.append('id IN (SELECT id FROM reports_fts WHERE reports_fts MATCH ?)')
            values.append(' AND '.join('"'+term+'"*' for term in terms))
        for field in ('source','topic'):
            if params.get(field):
                conditions.append(field+'=?'); values.append(params[field])
        if params.get('tag'):
            conditions.append('EXISTS (SELECT 1 FROM json_each(reports.tags) WHERE value=?)')
            values.append(params['tag'])
        for key,op in (('from','>='),('to','<=')):
            if params.get(key):
                date=datetime.strptime(params[key],'%Y-%m-%d').date().isoformat()
                conditions.append('date '+op+' ?'); values.append(date)
        where=' WHERE '+' AND '.join(conditions) if conditions else ''
        order={'newest':'created_at DESC,id','oldest':'created_at ASC,id','title':'title COLLATE NOCASE,id'}.get(params.get('sort','newest'))
        if not order: raise ValueError('Unknown sort order.')
        with self.db() as db:
            total=db.execute('SELECT count(*) FROM reports'+where,values).fetchone()[0]
            pages=max(1,(total+limit-1)//limit); page=min(page,pages)
            rows=db.execute('SELECT * FROM reports'+where+' ORDER BY '+order+' LIMIT ? OFFSET ?',(*values,limit,(page-1)*limit)).fetchall()
        return {'reports':[self.public_report(r) for r in rows],'total':total,'pages':pages,'page':page}

    def facets(self):
        with self.db() as db:
            result={name:[dict(r) for r in db.execute(f'SELECT {field} name,count(*) count FROM reports GROUP BY {field} ORDER BY {field} COLLATE NOCASE')]
                    for field,name in [('source','sources'),('topic','topics')]}
            result['tags']=[dict(r) for r in db.execute('SELECT value name,count(*) count FROM reports,json_each(reports.tags) GROUP BY value ORDER BY value')]
            result['total']=db.execute('SELECT count(*) FROM reports').fetchone()[0]
        return result

    def extract(self, payload, filename, target):
        if len(payload)>MAX_UPLOAD: raise ValueError('Upload exceeds 50 MB.')
        if Path(filename).suffix.lower() in HTML:
            (target/'index.html').write_bytes(payload)
            return 'index.html'
        if Path(filename).suffix.lower()!='.zip':
            raise ValueError('Choose an HTML file or ZIP report bundle.')
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                entries=archive.infolist()
                if len(entries)>MAX_FILES or sum(x.file_size for x in entries)>MAX_EXPANDED:
                    raise ValueError('Bundle exceeds 2,000 files or 200 MB expanded.')
                names=set()
                for item in entries:
                    name=item.filename
                    path=PurePosixPath(name)
                    mode=item.external_attr>>16
                    if (not name or '\\' in name or ':' in name or '\x00' in name or path.is_absolute()
                            or '..' in path.parts or stat.S_ISLNK(mode) or item.flag_bits & 1):
                        raise ValueError('Bundle contains an unsafe or encrypted entry.')
                    if path.as_posix() in names: raise ValueError('Bundle contains duplicate paths.')
                    names.add(path.as_posix())
                    destination=target.joinpath(*path.parts)
                    if not destination.resolve().is_relative_to(target.resolve()): raise ValueError('Unsafe bundle path.')
                    if item.is_dir(): destination.mkdir(parents=True,exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True,exist_ok=True)
                        with archive.open(item) as source, destination.open('wb') as output:
                            shutil.copyfileobj(source,output)
                if (target/'index.html').is_file(): return 'index.html'
                htmls=[p for p in target.rglob('*') if p.suffix.lower() in HTML and p.is_file()]
                if len(htmls)==1: return htmls[0].relative_to(target).as_posix()
                raise ValueError('ZIP bundles with multiple HTML files need index.html at the root.')
        except zipfile.BadZipFile:
            raise ValueError('The ZIP bundle is invalid.') from None

    def import_report(self,payload,filename,metadata=None,watch_identity=None):
        metadata=metadata or {}
        report_id=str(uuid.uuid4())
        with self.lock, tempfile.TemporaryDirectory(prefix='.import-',dir=self.reports) as temporary:
            stage=Path(temporary)
            entry=self.extract(payload,filename,stage)
            validate_package(stage,entry)
            parser=MetadataParser(); parser.feed(read_text(stage/entry)); parser.close()
            parsed=parser.result()
            title=str(metadata.get('title') or parsed['title'] or Path(filename).stem).strip()[:500]
            source=str(metadata.get('source') or 'Upload').strip()[:100]
            topic=str(metadata.get('topic') or 'General').strip()[:100]
            date=str(metadata.get('date') or now()[:10])
            datetime.strptime(date,'%Y-%m-%d')
            tags=metadata.get('tags',[])
            if isinstance(tags,str): tags=tags.split(',')
            if not isinstance(tags,list) or any(not isinstance(t,str) for t in tags): raise ValueError('Tags must be text.')
            tags=list(dict.fromkeys(t.strip()[:80] for t in tags if t.strip()))[:30]
            size=sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())
            destination=self.reports/report_id
            try:
                copy_tree(stage,destination)
                with self.db() as db:
                    db.execute('INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                               (report_id,title,source,topic,date,now(),parsed['excerpt'],parsed['body'],entry,size,json.dumps(tags)))
                    db.execute('INSERT INTO reports_fts VALUES (?,?,?,?)',(report_id,title,parsed['body'],' '.join(tags)))
                    if watch_identity:
                        db.execute('INSERT OR REPLACE INTO watch_imports VALUES (?,?,?)',(*watch_identity,report_id))
            except Exception:
                shutil.rmtree(destination,ignore_errors=True)
                raise
        return self.report(report_id)

    @staticmethod
    def password_hash(password):
        if not isinstance(password,str) or len(password)>256: raise ValueError('Password must be at most 256 characters.')
        if not password:return ''
        salt=secrets.token_hex(16)
        return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),310000).hex()

    @staticmethod
    def check_password(password,encoded):
        salt,digest=encoded.split(':',1)
        return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),310000).hex(),digest)

    def share(self,token,include_expired=False):
        with self.db() as db:
            row=db.execute('SELECT * FROM shares WHERE token=?',(token,)).fetchone()
        if row is None or (not include_expired and row['expires_at'] is not None and row['expires_at']<=time.time()):
            raise LookupError('This share is unavailable or has expired.')
        return dict(row)

    @staticmethod
    def public_share(row):
        return {**{k:row[k] for k in ('token','report_id','title','created_at','updated_at','expires_at')},
                'password_protected':bool(row['password']),'url':'/share/'+row['token']+'/'}

    def list_shares(self,report_id=None):
        with self.db() as db:
            rows=db.execute('SELECT * FROM shares'+(' WHERE report_id=?' if report_id else '')+' ORDER BY created_at DESC',
                            (report_id,) if report_id else ()).fetchall()
        return [self.public_share(dict(r)) for r in rows]

    def save_share(self,params,token=None):
        with self.lock:
            previous=self.share(token,True) if token else None
            reuse=previous and params.get('report_id',previous['report_id'])==previous['report_id']
            report=({'id':previous['report_id'],'title':previous['title'],'entry':previous['entry']} if reuse
                    else self.report(params.get('report_id')))
            expires=previous['expires_at'] if previous else None
            if 'expires_in_days' in params:
                days=params['expires_in_days']
                if days is not None and (type(days) not in (float,int) or not 0<days<=3650):
                    raise ValueError('Expiry must be between 0 and 3,650 days, or null for no expiry.')
                expires=time.time()+days*86400 if days is not None else None
            password=previous['password'] if previous else ''
            if 'password' in params: password=self.password_hash(params['password'])
            token=token or str(uuid.uuid4())
            version=str(uuid.uuid4())
            destination=self.shares/token/version
            try:
                source=self.shares/token/previous['version'] if reuse else self.reports/report['id']
                copy_tree(source,destination)
                validate_package(destination,report['entry'])
                with self.db() as db:
                    db.execute('INSERT OR REPLACE INTO shares VALUES (?,?,?,?,?,?,?,?,?)',
                               (token,report['id'],report['title'],version,report['entry'],previous['created_at'] if previous else now(),now(),expires,password))
                    if previous and password!=previous['password']:
                        db.execute('DELETE FROM share_sessions WHERE token=?',(token,))
            except Exception:
                shutil.rmtree(destination,ignore_errors=True);raise
            if previous:
                shutil.rmtree(self.shares/token/previous['version'],ignore_errors=True)
            return self.public_share(self.share(token,True))

    def delete_report(self,report_id):
        with self.lock:
            self.report(report_id)
            # Remove files first: a storage failure leaves the catalogue recoverable.
            folder=self.reports/report_id
            if folder.exists(): shutil.rmtree(folder)
            with self.db() as db:
                db.execute('DELETE FROM reports WHERE id=?',(report_id,))
                db.execute('DELETE FROM reports_fts WHERE id=?',(report_id,))
                db.execute('DELETE FROM watch_imports WHERE report_id=?',(report_id,))

    def revoke(self,token):
        with self.lock,self.db() as db:
            if not db.execute('DELETE FROM shares WHERE token=?',(token,)).rowcount: raise LookupError('Share not found.')
            db.execute('DELETE FROM share_sessions WHERE token=?',(token,))
        if identifier(token): shutil.rmtree(self.shares/token,ignore_errors=True)

    def unlock(self,token,password):
        # A password change or revocation must invalidate every preceding
        # password check, including checks which are still in progress.
        with self.lock:
            share=self.share(token)
            if not share['password'] or not self.check_password(password,share['password']): return None
            session=secrets.token_urlsafe(32)
            with self.db() as db:
                db.execute('DELETE FROM share_sessions WHERE expires_at<?',(time.time(),))
                db.execute('INSERT INTO share_sessions VALUES (?,?,?)',
                           (hashlib.sha256(session.encode()).hexdigest(),token,min(time.time()+43200,share['expires_at'] or float('inf'))))
            return session

    def unlocked(self,token,session):
        with self.db() as db:
            return db.execute('SELECT 1 FROM share_sessions WHERE token=? AND session=? AND expires_at>?',
                              (token,hashlib.sha256(session.encode()).hexdigest(),time.time())).fetchone() is not None

    def add_watcher(self,path,enabled=True):
        if not isinstance(path,str) or not Path(path).is_absolute(): raise ValueError('Use an absolute folder path.')
        root=Path(path).resolve()
        for protected in (self.data,self.reports,self.shares):
            if root.is_relative_to(protected) or protected.is_relative_to(root):
                raise ValueError('A watched folder cannot overlap application data, reports, or shares.')
        if not root.is_dir(): raise ValueError('The watched folder must exist inside the container.')
        watcher_id=str(uuid.uuid4())
        with self.db() as db:
            for row in db.execute('SELECT path FROM watchers'):
                other=Path(row['path']).resolve()
                if root.is_relative_to(other) or other.is_relative_to(root):
                    raise ValueError('Watched locations cannot overlap each other.')
            db.execute('INSERT INTO watchers(id,path,enabled) VALUES (?,?,?)',(watcher_id,str(root),bool(enabled)))
        return watcher_id
