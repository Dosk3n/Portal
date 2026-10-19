"""Portal HTTP boundaries: private browser UI, keyed API, public snapshots."""
from __future__ import annotations
import argparse
import errno
from email.parser import BytesParser
from email.policy import default
import hashlib
import hmac
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit

from library import Library, MAX_UPLOAD, identifier, RevisionConflict
from settings import canonical_origin
from watcher import Watcher
from safe_files import open_regular

STATIC=Path(__file__).parent/'static'
READER_VIEW_SECONDS=3600
SANDBOX="sandbox allow-scripts allow-forms allow-downloads allow-popups allow-modals; frame-ancestors 'self'"


class PortalServer(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=16
    def __init__(self,address,library,base_url=None):
        self.library=library
        self.asset_version=hashlib.sha256(b''.join(
            path.read_bytes() for path in sorted(STATIC.rglob('*')) if path.is_file())).hexdigest()[:16]
        self.base_url=canonical_origin(base_url) if base_url else None
        self.reader_secret=secrets.token_bytes(32)
        self.attempts={}
        self.attempt_lock=threading.Lock()
        self.upload_slots=threading.BoundedSemaphore(3)
        self.request_slots=threading.BoundedSemaphore(32)
        super().__init__(address,Handler)

    def process_request(self,request,client_address):
        if not self.request_slots.acquire(blocking=False):
            try:
                request.settimeout(.2)
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\nRetry-After: 2\r\n\r\n')
            except OSError: pass
            finally: self.shutdown_request(request)
            return
        try: super().process_request(request,client_address)
        except Exception:
            self.request_slots.release()
            raise

    def process_request_thread(self,request,client_address):
        try: super().process_request_thread(request,client_address)
        finally: self.request_slots.release()

    def reader_link(self, report_id, version, relative):
        # Independent from the API key and login cookie. Scope every grant to
        # one retained package and one hour; restarts invalidate existing grants.
        expiry=int(time.time())+READER_VIEW_SECONDS
        payload=f'{report_id}.{version}.{expiry}.{secrets.token_urlsafe(16)}'
        signature=hmac.new(self.reader_secret,payload.encode(),hashlib.sha256).hexdigest()
        return '/share/_reader/'+payload+'.'+signature+'/files/'+quote(relative,safe='/')

    def reader_scope(self, ticket):
        try:
            if len(ticket)>256:raise ValueError
            report_id,version,expires,nonce,signature=ticket.split('.')
            if not identifier(report_id) or not identifier(version):raise ValueError
            if not re.fullmatch(r'[A-Za-z0-9_-]{22}',nonce) or not re.fullmatch(r'[a-f0-9]{64}',signature):raise ValueError
            expiry=int(expires)
            payload='.'.join((report_id,version,expires,nonce))
            expected=hmac.new(self.reader_secret,payload.encode(),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature,expected) or not time.time()<expiry<=time.time()+READER_VIEW_SECONDS:
                raise ValueError
            return report_id,version
        except (ValueError,TypeError):
            raise LookupError('This report view has expired or is unavailable. Reopen it from the private library.') from None

    def trusted_host(self,host):
        if not host:return False
        allowed=set()
        if self.base_url:allowed.add(urlsplit(self.base_url).netloc)
        address,port=self.server_address[:2]
        for name in {'localhost','127.0.0.1','[::1]',address}:
            if name in {'0.0.0.0','::'}:continue
            allowed.add(name+(':'+str(port) if port!=80 else ''))
        # Canonicalisation removes standard ports and rejects credentials/path syntax.
        for scheme in ('http','https'):
            try:
                if urlsplit(canonical_origin(scheme+'://'+host)).netloc in allowed:return True
            except ValueError:pass
        return False


class Handler(BaseHTTPRequestHandler):
    server_version='Portal'
    def log_message(self,format,*args):
        # Never log request URLs: share tokens, search terms and filenames are private.
        pass

    def setup(self):
        super().setup();self.connection.settimeout(60)

    def do_GET(self):self.handle_request()
    def do_HEAD(self):self.handle_request()
    def do_POST(self):self.handle_request()
    def do_PATCH(self):self.handle_request()
    def do_DELETE(self):self.handle_request()

    def send(self,status,body=b'',mime='application/json; charset=utf-8',headers=None,sandbox=False):
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Robots-Tag','noindex, nofollow, noarchive')
        if sandbox:self.send_header('Content-Security-Policy',SANDBOX)
        elif mime.startswith('text/html'):
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
        for key,value in (headers or {}).items():self.send_header(key,value)
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(body)

    def json(self,data,status=200):self.send(status,json.dumps(data,ensure_ascii=False).encode())

    def error(self,status,message):
        path=urlsplit(self.path).path
        reader=path.startswith('/share/_reader/')
        public=path.startswith('/share/') and not reader
        browser=self.command in {'GET','HEAD'} and not path.startswith(('/api','/internal/','/static/'))
        if public or browser:
            title='Report view unavailable' if reader else 'Share unavailable' if public else 'Report unavailable' if path.startswith('/files/') else 'Page unavailable'
            recovery='<a href="/" target="_top">Back to library</a>' if not public else ''
            token=path.split('/')[2] if public else ''
            if public and identifier(token) and status==403:
                recovery=f'<a href="/share/{token}/" target="_top">Enter the share password</a>'
            body=('<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">'
                  '<title>'+title+' · Portal</title><style>body{font:17px/1.7 system-ui;color:#244d40;background:#f8f7f3;'
                  'margin:12vh auto;padding:24px;max-width:540px}a{color:inherit}.brand{font-weight:700}</style>'
                  '<main><p class="brand">Portal</p><h1>'+title+'</h1><p>'+escape(message)+'</p>'+recovery+'</main></html>')
            self.send(status,body.encode(),'text/html; charset=utf-8')
        else:self.json({'error':message},status)

    def body(self,maximum=16384):
        if self.headers.get('Transfer-Encoding'):raise ValueError('Chunked requests are not supported.')
        if len(self.headers.get_all('Content-Length',[]))>1:raise ValueError('Ambiguous request length.')
        length=int(self.headers.get('Content-Length','0'))
        if length<0 or length>maximum:raise ValueError('Request is too large.')
        chunks=[];remaining=length;deadline=time.monotonic()+60
        try:
            while remaining:
                timeout=deadline-time.monotonic()
                if timeout<=0:raise ValueError('Request body timed out.')
                self.connection.settimeout(timeout)
                chunk=self.rfile.read1(min(65536,remaining))
                if not chunk:raise ValueError('Incomplete request body.')
                chunks.append(chunk);remaining-=len(chunk)
        except (TimeoutError,socket.timeout):raise ValueError('Request body timed out.') from None
        finally:self.connection.settimeout(60)
        return b''.join(chunks)

    def json_body(self):
        if self.headers.get_content_type()!='application/json':raise ValueError('Expected application/json.')
        value=json.loads(self.body())
        if not isinstance(value,dict):raise ValueError('Expected a JSON object.')
        return value

    def private_write(self):
        if self.headers.get('X-Portal-Request')!='1' or self.headers.get('Sec-Fetch-Site')=='cross-site':return False
        origin=self.headers.get('Origin')
        allowed={self.server.base_url}
        for scheme in ('http','https'):
            try:allowed.add(canonical_origin(scheme+'://'+self.headers.get('Host','')))
            except ValueError:pass
        return origin is None or origin in allowed

    def handle_request(self):
        try:self.route()
        except (BrokenPipeError,ConnectionResetError):pass
        except RevisionConflict as exc:self.error(409,str(exc))
        except LookupError as exc:self.error(404,str(exc))
        except (ValueError,UnicodeError,json.JSONDecodeError) as exc:self.error(400,str(exc))
        except sqlite3.IntegrityError:self.error(409,'That entry already exists.')
        except (OSError,sqlite3.Error):self.error(503,'Storage is temporarily unavailable. Your existing reports have been retained.')
        except Exception as exc:
            print('Request failed:',type(exc).__name__,flush=True)
            self.error(500,'The request could not be completed.')

    def route(self):
        if not self.server.trusted_host(self.headers.get('Host')):
            return self.error(421,'Use the configured Portal address.')
        if len(self.headers.get_all('Host',[]))!=1:
            return self.error(400,'Use a single Host header.')
        parsed=urlsplit(self.path)
        path=unquote(parsed.path)
        params={k:v[-1] for k,v in parse_qs(parsed.query).items()}
        if any(part in {'.','..'} for part in path.split('/')) or '\\' in path:
            return self.error(404,'Not found.')
        public=self.headers.get('X-Portal-Public')=='1'
        if path.startswith('/share/_reader/'):
            return self.reader_route(path)
        if path.startswith('/share/'):
            return self.share_route(path)
        if public:
            # Public router can also serve only the minimal password-page stylesheet.
            if path=='/static/public.css' and self.command in {'GET','HEAD'}:
                return self.file(STATIC,'public.css')
            return self.error(404,'Not found.')
        api=path=='/api' or path.startswith('/api/')
        if api:
            key=self.headers.get('X-Api-Key','')
            if not key or not hmac.compare_digest(key.encode(),self.server.library.setting('api_key').encode()):
                return self.error(401,'A valid X-Api-Key header is required.')
            suffix=path.removeprefix('/api')
        elif path.startswith('/internal/'):
            suffix=path.removeprefix('/internal')
            if self.command not in {'GET','HEAD'} and not self.private_write():
                return self.error(403,'Open Portal to make this change.')
        else:
            if self.command not in {'GET','HEAD'}:return self.error(405,'Method not allowed.')
            if path=='/health':
                with self.server.library.db() as db:db.execute('SELECT 1').fetchone()
                return self.json({'status':'ok'})
            if path in {'/','/index.html','/settings','/upload','/shares'} or path.startswith('/read/'):
                return self.index_page()
            if path.startswith('/static/'):return self.file(STATIC,path.removeprefix('/static/'))
            if path.startswith('/files/'):
                parts=path.removeprefix('/files/').split('/',1)
                if len(parts)!=2 or not identifier(parts[0]):raise LookupError('File not found.')
                return self.report_file(parts[0],parts[1],reader=True)
            return self.error(404,'Not found.')
        lib=self.server.library
        if self.command in {'GET','HEAD'}:
            if suffix in {'','/'}:return self.json({'name':'Portal API','version':'1.1.0','docs':'/api/docs'})
            if suffix=='/docs':return self.json(API_DOCS)
            if suffix=='/reports':return self.json(lib.search(params))
            if suffix=='/facets':return self.json(lib.facets())
            if suffix.startswith('/reports/'):
                parts=suffix.removeprefix('/reports/').split('/',2)
                report=lib.report(parts[0])
                if len(parts)==1:return self.json(report)
                if len(parts)==2 and parts[1]=='versions':return self.json({'versions':lib.versions(report['id'])})
                if len(parts)==3 and parts[1]=='files':return self.report_file(report['id'],parts[2])
                if len(parts)==3 and parts[1]=='versions':return self.report_file(report['id'],'versions/'+parts[2])
            if suffix=='/shares':return self.json({'shares':lib.list_shares(params.get('report_id'))})
            if not api and suffix=='/settings':return self.json(lib.settings())
            if not api and suffix=='/settings/retention-preview':
                return self.json({'versions_to_remove':lib.retention_preview(int(params.get('limit','3')))})
        elif self.command=='POST':
            if suffix=='/reports':return self.upload()
            if suffix.startswith('/reports/'):
                parts=suffix.removeprefix('/reports/').split('/')
                if len(parts)==2 and parts[1]=='versions':return self.upload(parts[0])
                if len(parts)==2 and parts[1]=='restore':
                    value=self.json_body()
                    return self.json(lib.restore_version(parts[0],value.get('version'),value.get('expected_version')),201)
            if suffix=='/shares':return self.json(lib.save_share(self.json_body()),201)
            if not api and suffix=='/settings/api-key':
                with lib.lock:lib.set_setting('api_key',secrets.token_urlsafe(32))
                return self.json({'api_key':lib.setting('api_key')})
            if not api and suffix=='/watchers':
                value=self.json_body()
                with lib.lock:result=lib.add_watcher(value.get('path'),value.get('enabled',True))
                return self.json({'id':result},201)
        elif self.command=='PATCH':
            if suffix.startswith('/reports/') and '/' not in suffix.removeprefix('/reports/'):
                return self.json(lib.edit_report(suffix.removeprefix('/reports/'),self.json_body()))
            if suffix.startswith('/shares/'):
                return self.json(lib.save_share(self.json_body(),suffix.removeprefix('/shares/')))
            if not api and suffix=='/settings':
                value=self.json_body()
                lib.update_settings(value)
                return self.json({'saved':True})
            if not api and suffix.startswith('/watchers/'):
                value=self.json_body()
                if type(value.get('enabled')) is not bool:raise ValueError('Choose whether the folder is enabled.')
                with lib.lock,lib.db() as db:
                    if not db.execute('UPDATE watchers SET enabled=? WHERE id=?',(value['enabled'],suffix.removeprefix('/watchers/'))).rowcount:
                        raise LookupError('Folder not found.')
                return self.json({'saved':True})
        elif self.command=='DELETE':
            if suffix.startswith('/reports/') and not api:
                lib.delete_report(suffix.removeprefix('/reports/'));return self.json({'removed':True})
            if suffix.startswith('/shares/'):
                lib.revoke(suffix.removeprefix('/shares/'));return self.json({'revoked':True})
            if not api and suffix.startswith('/watchers/'):
                with lib.lock,lib.db() as db:db.execute('DELETE FROM watchers WHERE id=?',(suffix.removeprefix('/watchers/'),))
                return self.json({'removed':True})
        self.error(404,'Endpoint not found.')

    def index_page(self):
        body=(STATIC/'index.html').read_bytes().replace(b'__ASSET_VERSION__',self.server.asset_version.encode())
        self.send(200,body,'text/html; charset=utf-8')

    def report_file(self, report_id, relative, reader=False):
        version=None
        if relative.startswith('versions/'):
            parts=relative.split('/',3)
            if len(parts)!=4 or parts[2]!='files' or not identifier(parts[1]):raise LookupError('File not found.')
            version=parts[1];relative=parts[3]
        selected=self.server.library.version(report_id,version)['version']
        return self.file(self.server.library.package_path(report_id,selected),relative,sandbox=True,
                         reader_scope=(report_id,selected) if reader else None)

    def upload(self, report_id=None):
        if not self.server.upload_slots.acquire(blocking=False):return self.error(429,'Uploads are busy. Try again shortly.')
        try:
            raw=self.body(MAX_UPLOAD+65536)
            mime=self.headers.get_content_type()
            metadata={}
            if mime=='multipart/form-data':
                message=BytesParser(policy=default).parsebytes(('Content-Type: '+self.headers.get('Content-Type','')+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
                if not message.is_multipart():raise ValueError('Invalid multipart upload.')
                payload=None;filename=''
                for part in message.iter_parts():
                    name=part.get_param('name',header='content-disposition')
                    if name=='file':
                        if payload is not None:raise ValueError('Submit one report at a time.')
                        payload=part.get_payload(decode=True);filename=part.get_filename() or ''
                    elif name in {'title','source','topic','date','tags'}:
                        metadata[name]=(part.get_payload(decode=True) or b'').decode('utf-8')
                if payload is None:raise ValueError('Choose a report file.')
            elif mime in {'text/html','application/zip','application/octet-stream'}:
                payload=raw;filename=self.headers.get('X-Filename','report.zip' if mime=='application/zip' else 'report.html')
                for key in ('title','source','topic','date','tags'):
                    value=self.headers.get('X-Report-'+key)
                    if value:metadata[key]=value
            else:raise ValueError('Upload multipart/form-data, text/html, or application/zip.')
            self.json(self.server.library.import_report(payload,filename,metadata,report_id=report_id,
                      expected=self.headers.get('X-Report-Version')),201)
        finally:self.server.upload_slots.release()

    def reader_route(self,path):
        if self.command not in {'GET','HEAD'}:return self.error(405,'Method not allowed.')
        parts=path.removeprefix('/share/_reader/').split('/',2)
        if len(parts)!=3 or parts[1]!='files':raise LookupError('File not found.')
        report_id,version=self.server.reader_scope(parts[0])
        # Revalidate catalogue ownership on every request: deletion and pruning
        # invalidate the view immediately, and paths cannot cross packages.
        return self.file(self.server.library.package_path(report_id,version),parts[2],sandbox=True)

    def share_route(self,path):
        parts=path.removeprefix('/share/').split('/',1)
        token=parts[0]
        if not identifier(token):raise LookupError('This share is unavailable or has expired.')
        share=self.server.library.share(token)
        relative=parts[1] if len(parts)==2 else ''
        if relative=='_portal.css' and self.command in {'GET','HEAD'}:
            return self.file(STATIC,'public.css')
        if relative=='_logo.svg' and self.command in {'GET','HEAD'}:
            return self.file(STATIC/'branding','portal-logo.svg')
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:pass
        session=cookie.get('portal_share')
        credential=session.value if session else ''
        # Opaque sandbox documents cannot reliably attach SameSite cookies to
        # subresources. Scope the short-lived unlocked session to a URL prefix
        # so relative assets remain authorised without weakening the sandbox.
        session_route=relative.startswith('session/')
        if session_route:
            session_parts=relative.split('/',2)
            if len(session_parts)!=3:raise LookupError('File not found.')
            credential=session_parts[1]
            relative=session_parts[2]
        unlocked=not share['password'] or self.server.library.unlocked(token,credential)
        if self.command=='POST' and relative=='unlock':
            if self.headers.get_content_type()!='application/x-www-form-urlencoded':raise ValueError('Invalid password form.')
            key=(self.client_address[0],token)
            with self.server.attempt_lock:
                current=time.time()
                self.server.attempts={k:v for k,v in self.server.attempts.items() if v[0]>current-900}
                window,count=self.server.attempts.get(key,(current,0))
                if count>=10:return self.error(429,'Too many attempts. Try again in 15 minutes.')
                self.server.attempts[key]=(window,count+1)
            password=parse_qs(self.body(4096).decode()).get('password',[''])[0]
            if len(password)>256:raise ValueError('Password is too long.')
            session=self.server.library.unlock(token,password)
            if not session:return self.password_form(token,True)
            secure='; Secure' if (self.server.base_url or '').startswith('https:') else ''
            return self.send(303,headers={'Location':f'/share/{token}/',
                'Set-Cookie':f'portal_share={session}; Path=/share/{token}/; HttpOnly; SameSite=Lax; Max-Age=43200'+secure})
        if self.command not in {'GET','HEAD'}:return self.error(405,'Method not allowed.')
        if not unlocked:
            if relative not in {'','index.html'}:return self.error(403,'Enter the share password first.')
            return self.password_form(token)
        root=self.server.library.shares/token/share['version']
        if not relative:
            prefix=f'/share/{token}/'
            if share['password']:prefix+=f'session/{credential}/'
            return self.send(302,headers={'Location':prefix+'files/'+quote(share['entry'],safe='/')})
        if relative.startswith('files/'):
            if share['password'] and not session_route:
                return self.send(302,headers={'Location':f'/share/{token}/session/{credential}/'+quote(relative,safe='/')})
            return self.file(root,relative.removeprefix('files/'),sandbox=True)
        raise LookupError('File not found.')

    def password_form(self,token,failed=False):
        body=('<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">'
              f'<title>Protected report · Portal</title><link rel="stylesheet" href="/share/{token}/_portal.css">'
              f'<main><img class="share-brand" src="/share/{token}/_logo.svg" alt="Portal" width="144" height="40">'
              '<h1>A report shared with you.</h1><p>Enter the password to open this report.</p>'
              +('<p role="alert">That password was incorrect.</p>' if failed else '')+
              f'<form method="post" action="/share/{token}/unlock"><label for="password">Password</label>'
              '<input id="password" name="password" type="password" required maxlength="256" autocomplete="current-password">'
              '<button>Open report</button></form></main></html>')
        self.send(403 if failed else 200,body.encode(),'text/html; charset=utf-8')

    def file(self,root,relative,sandbox=False,reader_scope=None):
        # Descriptor-relative opens reject links in every component, including
        # a directory index; keep the verified descriptor through the response.
        try:
            context=open_regular(root,relative,index=True)
            stream=context.__enter__()
        except (FileNotFoundError,NotADirectoryError,ValueError):
            raise LookupError('File not found.') from None
        except OSError as error:
            if error.errno==errno.ELOOP:raise LookupError('File not found.') from None
            raise
        try:
            filename=stream.portal_filename
            mime=mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            if reader_scope and self.command=='GET' and mime in {'text/html','application/xhtml+xml'}:
                # Authenticate the initial document through the private route,
                # then load all its relative dependencies without SameSite cookies.
                entry=relative.rstrip('/')
                if not entry or Path(entry).name!=filename:entry=(entry+'/' if entry else '')+filename
                return self.send(302,headers={'Location':self.server.reader_link(*reader_scope,entry)})
            if mime.startswith('text/'):mime+='; charset=utf-8'
            size=os.fstat(stream.fileno()).st_size
            self.send_response(200)
            for key,value in {'Content-Type':mime,'Content-Length':str(size),
                              'Cache-Control':'no-store' if sandbox else 'no-cache','X-Content-Type-Options':'nosniff',
                              'Referrer-Policy':'no-referrer','X-Robots-Tag':'noindex, nofollow, noarchive'}.items():self.send_header(key,value)
            if sandbox:self.send_header('Content-Security-Policy',SANDBOX)
            elif mime.startswith('text/html'):self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
            self.end_headers()
            if self.command!='HEAD':
                remaining=size
                while remaining:
                    chunk=stream.read(min(65536,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)
        finally:context.__exit__(None,None,None)


API_DOCS={'version':'1.1.0','authentication':'X-Api-Key header. Manage the single key in browser Settings.',
 'endpoints':{'GET /api/reports':'Search with q, source, topic, tag, from, to, sort, page, limit.',
 'GET /api/reports/{id}':'Report metadata. Browser reader_url uses private library access.',
 'GET /api/reports/{id}/files/{path}':'Download report content with API key.',
 'POST /api/reports':'Multipart file + optional title, source, topic, date (YYYY-MM-DD), tags (comma separated). Or raw text/html or application/zip with optional X-Filename and X-Report-* headers.',
 'POST /api/reports/{id}/versions':'Revise HTML/assets using the same upload formats; keep report ID/link. Optional X-Report-Version rejects stale updates with 409. Old versions pruned to Settings retention (3 total by default).',
 'GET /api/reports/{id}/versions':'List retained versions, newest first; includes version UUID, number, current flag and private preview URL.',
 'GET /api/reports/{id}/versions/{version}/files/{path}':'Download a specific retained version with API key.',
 'POST /api/reports/{id}/restore':'JSON version UUID and optional expected_version; restore as a new current version, subject to retention.',
 'PATCH /api/reports/{id}':'Edit title, source, topic, date or tags without creating a content version. Optional expected_version.',
 'GET /api/facets':'Collections, sources, tags and counts.',
 'GET /api/shares':'List snapshots; optional report_id filter.',
 'POST /api/shares':'JSON report_id, expires_in_days (number or null), optional password.',
 'PATCH /api/shares/{token}':'Replace snapshot using report_id. For the same revised report use refresh_snapshot:true; otherwise its existing snapshot is kept. Optionally change expires_in_days or password (empty removes it). Omitted fields remain unchanged.',
 'DELETE /api/shares/{token}':'Revoke snapshot.'},
 'limits':{'upload_mb':50,'expanded_mb':200,'files':2000},
 'bundles':'Use relative asset paths. A multi-page ZIP requires index.html at its root. HTML runs in an opaque-origin sandbox; storage and some browser APIs are unavailable.'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8769)
    parser.add_argument('--data-dir',default='./data');parser.add_argument('--storage-dir',default='./storage')
    args=parser.parse_args()
    library=Library(args.data_dir,args.storage_dir)
    watcher=Watcher(library);watcher.start()
    server=PortalServer((args.host,args.port),library,os.environ.get('PUBLIC_BASE_URL'))
    import signal
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    print(f'Portal listening on port {server.server_port}',flush=True)
    try:server.serve_forever()
    finally:watcher.close();server.server_close()


if __name__=='__main__':main()
