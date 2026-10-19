"""Bounded standard-library Portal API client and report bundle builder."""
from __future__ import annotations

import base64
import binascii
import io
import json
from pathlib import PurePosixPath
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

MAX_UPLOAD=50*1024*1024
MAX_PACKAGE=200*1024*1024
MAX_RESPONSE=8*1024*1024
MAX_FILES=2000
USER_AGENT='Nexus-Portal/0.1.0'


class PortalError(RuntimeError):
    """An API failure safe to display without exposing authentication headers."""
    def __init__(self, status, message):
        self.status=status
        super().__init__(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the API key to a redirected login or another host.
        return None


def canonical_uuid(value):
    if not isinstance(value,str):raise ValueError('Use a report, share or version UUID.')
    try:
        if str(uuid.UUID(value))==value:return value
    except ValueError:pass
    raise ValueError('Use a canonical UUID returned by Portal.')


def asset_path(value):
    if not isinstance(value,str) or not value or any(c in value for c in ('\\',':','\0')):
        raise ValueError('Asset paths must be relative package paths.')
    path=PurePosixPath(value)
    if path.is_absolute() or any(part in {'.','..',''} for part in value.split('/')):
        raise ValueError('Asset paths must be relative, without traversal or empty components.')
    if len(path.parts)>64:raise ValueError('Asset path is too deep.')
    return path.as_posix()


def build_bundle(html, assets=None):
    """Build one complete ZIP; accept named text/base64 assets, never local paths."""
    if not isinstance(html,str) or not html.strip():raise ValueError('Provide complete, non-empty HTML.')
    if assets is not None and not isinstance(assets,list):raise ValueError('Assets must be a list of named text/base64 files.')
    if len(assets or [])+1>MAX_FILES:raise ValueError('A report may contain at most 2,000 files.')
    entries={'index.html':html.encode('utf-8')};total=len(entries['index.html'])
    if total>MAX_PACKAGE:raise ValueError('Expanded report exceeds 200 MB.')
    for asset in assets or []:
        if not isinstance(asset,dict) or set(asset) not in ({'path','text'},{'path','base64'}):
            raise ValueError('Each asset needs path and exactly one of text or base64.')
        name=asset_path(asset['path'])
        if name in entries:raise ValueError('Asset paths must be unique; index.html is reserved.')
        if any(name.startswith(old+'/') or old.startswith(name+'/') for old in entries):
            raise ValueError('An asset path conflicts with another file.')
        content=asset.get('text',asset.get('base64'))
        if not isinstance(content,str):raise ValueError('Asset content must be text or a base64 string.')
        if len(content)>MAX_PACKAGE*4//3+4:raise ValueError('Asset exceeds the report size limit.')
        try:data=content.encode('utf-8') if 'text' in asset else base64.b64decode(content,validate=True)
        except (ValueError,binascii.Error):raise ValueError('Invalid base64 asset.') from None
        total+=len(data)
        if total>MAX_PACKAGE:raise ValueError('Expanded report exceeds 200 MB.')
        entries[name]=data
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in entries.items():archive.writestr(name,data)
    payload=output.getvalue()
    if len(payload)>MAX_UPLOAD:raise ValueError('Compressed report exceeds the 50 MB upload limit.')
    return payload


class PortalClient:
    def __init__(self, base_url, api_key, timeout=30):
        if not isinstance(base_url,str):raise ValueError('Configure PORTAL_BASE_URL in Nexus private settings.')
        try:
            parts=urlsplit(base_url)
            port=parts.port
        except (ValueError,TypeError):raise ValueError('PORTAL_BASE_URL must be an HTTP(S) origin or its /api URL.') from None
        if (parts.scheme not in {'http','https'} or not parts.hostname or parts.username is not None or parts.password is not None
                or parts.path.rstrip('/') not in {'','/api'} or parts.query or parts.fragment
                or '\\' in base_url or any(c.isspace() for c in base_url)):
            raise ValueError('PORTAL_BASE_URL must be an HTTP(S) origin or its /api URL.')
        if parts.scheme!='https' and parts.hostname not in {'localhost','127.0.0.1','::1'}:
            raise ValueError('Use HTTPS for a remote Portal instance.')
        if not isinstance(api_key,str) or not api_key or not api_key.isascii() or any(c.isspace() for c in api_key):
            raise ValueError('Configure PORTAL_API_KEY in Nexus private settings.')
        self.origin=parts.scheme+'://'+parts.netloc
        self.api_url=self.origin+'/api'
        self._key=api_key
        self.timeout=timeout
        self.opener=build_opener(NoRedirect)

    def request(self, method, path, body=None, query=None, content_type='application/json', headers=None, binary=False):
        if not path.startswith('/') or path.startswith('//') or '#' in path or '?' in path:
            raise ValueError('Use a relative API endpoint.')
        url=self.api_url+path
        if query:url+='?'+urlencode({k:v for k,v in query.items() if v is not None and v!=''})
        outgoing={'X-Api-Key':self._key,'User-Agent':USER_AGENT,'Accept':'application/json',**(headers or {})}
        if body is not None:
            outgoing['Content-Type']=content_type
            if content_type=='application/json':body=json.dumps(body).encode('utf-8')
        req=Request(url,data=body,headers=outgoing,method=method)
        try:
            with self.opener.open(req,timeout=self.timeout) as response:
                data=response.read(MAX_RESPONSE+1)
                mime=response.headers.get('Content-Type','application/octet-stream').split(';')[0]
                if len(data)>MAX_RESPONSE:raise PortalError(413,'Portal response exceeds the 8 MB tool limit; request fewer results or smaller assets.')
                if binary:return data,mime
                try:return json.loads(data) if data else {}
                except (ValueError,UnicodeError):raise PortalError(response.status,'Portal returned an unexpected response. Check the API address and proxy configuration.') from None
        except HTTPError as exc:
            with exc:
                raw=exc.read(16384)
            message={401:'Portal rejected the API key. Update PORTAL_API_KEY in Nexus if it was regenerated.',
                     403:'Portal access was blocked. Check the API endpoint and proxy access rules.',
                     409:'This report has changed. Read its current version before updating.',
                     429:'Portal is busy. Try again shortly.'}.get(exc.code,'Portal request failed (HTTP '+str(exc.code)+').')
            if exc.code in {400,404,413,503}:
                try:
                    detail=json.loads(raw).get('error')
                    if isinstance(detail,str):message=detail[:500]
                except (ValueError,AttributeError):pass
            if 300<=exc.code<400:message='Portal redirected the API request. Check the API address and proxy bypass.'
            raise PortalError(exc.code,message.replace(self._key,'[redacted]')) from None
        except (URLError,TimeoutError,OSError):
            raise PortalError(0,'Could not reach Portal. Check its address, certificate and network connection.') from None

    def report_links(self, report):
        value=dict(report)
        for key in ('reader_url','url'):
            if key in value and value[key].startswith('/') and not value[key].startswith('//'):
                value[key]=self.origin+value[key]
        return value

    def search_reports(self, **params):
        data=self.request('GET','/reports',query=params)
        data['reports']=[self.report_links(r) for r in data['reports']]
        return data

    def get_report(self, report_id, include_html=True, version=None, asset_paths=None):
        rid=canonical_uuid(report_id)
        if version is not None:
            version=canonical_uuid(version)
            versions=self.list_versions(rid)['versions']
            metadata=next((r for r in versions if r['version']==version),None)
            if metadata is None:raise PortalError(404,'Report version not found or no longer retained.')
        else:metadata=self.request('GET','/reports/'+rid)
        selected=canonical_uuid(metadata['current_version'])
        root=f'/reports/{rid}/versions/{selected}/files/'
        result={'report':self.report_links(metadata)}
        if include_html:
            data,_=self.request('GET',root+quote(asset_path(metadata['entry']),safe='/'),binary=True)
            try:result['html']=data.decode('utf-8-sig')
            except UnicodeError:result['html']=data.decode('windows-1252',errors='replace')
        if asset_paths is not None:
            if not isinstance(asset_paths,list) or len(asset_paths)>50:raise ValueError('Request at most 50 asset paths at once.')
            assets=[];total=0
            for path in asset_paths:
                name=asset_path(path)
                data,mime=self.request('GET',root+quote(name,safe='/'),binary=True)
                total+=len(data)
                if total>MAX_RESPONSE:raise PortalError(413,'Requested assets exceed the 8 MB tool limit.')
                assets.append({'path':name,'mime_type':mime,'base64':base64.b64encode(data).decode('ascii')})
            result['assets']=assets
        return result

    def upload(self, html, assets=None, metadata=None, report_id=None, expected_version=None):
        package=build_bundle(html,assets)
        boundary='portal-'+uuid.uuid4().hex
        body=bytearray()
        for key,value in (metadata or {}).items():
            if value is None:continue
            if key not in {'title','source','topic','date','tags'}:raise ValueError('Unsupported report detail.')
            if key=='tags' and isinstance(value,list):
                if any(not isinstance(t,str) for t in value):raise ValueError('Tags must be text.')
                value=','.join(value)
            if not isinstance(value,str):raise ValueError('Report details must be text.')
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            body.extend(value.encode('utf-8'));body.extend(b'\r\n')
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="report.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode())
        body.extend(package);body.extend(f'\r\n--{boundary}--\r\n'.encode())
        if len(body)>MAX_UPLOAD+65536:raise ValueError('Report and metadata exceed the upload limit.')
        path='/reports';headers={}
        if report_id is not None:
            path+='/'+canonical_uuid(report_id)+'/versions'
            headers['X-Report-Version']=canonical_uuid(expected_version)
        return self.report_links(self.request('POST',path,bytes(body),content_type='multipart/form-data; boundary='+boundary,headers=headers))

    def edit_report_details(self, report_id, **values):
        body={k:v for k,v in values.items() if v is not None}
        if not body:raise ValueError('Provide at least one report detail to change.')
        return self.report_links(self.request('PATCH','/reports/'+canonical_uuid(report_id),body))

    def list_versions(self, report_id):
        data=self.request('GET','/reports/'+canonical_uuid(report_id)+'/versions')
        data['versions']=[self.report_links(r) for r in data['versions']]
        return data

    def restore_version(self, report_id, version, expected_version):
        return self.report_links(self.request('POST','/reports/'+canonical_uuid(report_id)+'/restore',
            {'version':canonical_uuid(version),'expected_version':canonical_uuid(expected_version)}))

    def list_shares(self, report_id=None):
        data=self.request('GET','/shares',query={'report_id':canonical_uuid(report_id)} if report_id else None)
        data['shares']=[self.report_links(r) for r in data['shares']]
        return data

    def create_share(self, report_id, expires_in_days=7, password=None):
        body={'report_id':canonical_uuid(report_id),'expires_in_days':expires_in_days}
        if password is not None:body['password']=password
        return self.report_links(self.request('POST','/shares',body))

    def update_share(self, token, refresh_snapshot=False, report_id=None, change_expiry=False, expires_in_days=None, password=None):
        if type(refresh_snapshot) is not bool or type(change_expiry) is not bool:raise ValueError('Use booleans for refresh_snapshot and change_expiry.')
        if expires_in_days is not None and not change_expiry:raise ValueError('Set change_expiry=True to change expiry.')
        body={}
        if refresh_snapshot:body['refresh_snapshot']=True
        if report_id is not None:body['report_id']=canonical_uuid(report_id)
        if change_expiry:body['expires_in_days']=expires_in_days
        if password is not None:body['password']=password
        if not body:raise ValueError('Choose a snapshot or access change.')
        return self.report_links(self.request('PATCH','/shares/'+canonical_uuid(token),body))

    def revoke_share(self, token):
        return self.request('DELETE','/shares/'+canonical_uuid(token))


def get_client():
    # Read private Nexus configuration per call, including key rotations.
    from nexus.config import get_setting
    base=get_setting('PORTAL_BASE_URL')
    key=get_setting('PORTAL_API_KEY')
    if not base or not key:raise ValueError('Configure PORTAL_BASE_URL and PORTAL_API_KEY in Nexus private settings.')
    return PortalClient(base,key)
