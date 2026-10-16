"""Consume stable inbox submissions; keep failed or changing inputs untouched."""
import hashlib
import io
from pathlib import Path
import threading
import time
import zipfile

from library import HTML, MAX_UPLOAD, now


class Watcher:
    def __init__(self,library,interval=5,stable_seconds=10):
        self.library=library
        self.interval=interval
        self.stable_seconds=stable_seconds
        self.observed={}
        self.stopped=threading.Event()

    def start(self):
        self.thread=threading.Thread(target=self.run,name='inbox-watcher',daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        if hasattr(self,'thread'): self.thread.join(timeout=10)

    def run(self):
        while not self.stopped.is_set():
            try:self.scan()
            except Exception as error: print('Watcher scan failed:',type(error).__name__,flush=True)
            self.stopped.wait(self.interval)

    @staticmethod
    def files(candidate):
        if candidate.is_symlink(): raise ValueError('Symlinks are not imported.')
        if candidate.is_file():return [candidate]
        files=[]
        for p in sorted(candidate.rglob('*')):
            if p.is_symlink():raise ValueError('Submission folders cannot contain symlinks.')
            if p.is_file():files.append(p)
        return files

    def scan(self):
        if not self.library.setting('watching_enabled'):
            self.observed.clear();return
        with self.library.db() as db:
            watchers=[dict(r) for r in db.execute('SELECT * FROM watchers WHERE enabled=1')]
        active=set()
        for watcher in watchers:
            error=''; imported=False
            try:
                root=Path(watcher['path'])
                resolved=root.resolve()
                for protected in (self.library.data,self.library.reports,self.library.shares):
                    if resolved.is_relative_to(protected) or protected.is_relative_to(resolved):
                        raise OSError('Watched folder now overlaps managed storage; import stopped.')
                if not root.is_dir(): raise OSError('Watched folder is unavailable.')
                for candidate in sorted(root.iterdir()):
                    if candidate.name.startswith('.') or candidate.is_symlink():continue
                    if candidate.is_file() and candidate.suffix.lower() not in HTML|{'.zip'}:continue
                    key=str(candidate); active.add(key)
                    try:
                        files=self.files(candidate)
                        if not files:continue
                        signature=tuple((str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in files)
                        observation=self.observed.get(key)
                        if not observation or observation[0]!=signature:
                            self.observed[key]=(signature,time.monotonic());continue
                        if time.monotonic()-observation[1]<self.stable_seconds:continue
                        if sum(p.stat().st_size for p in files)>MAX_UPLOAD:raise ValueError('Submission exceeds 50 MB.')
                        contents={p:p.read_bytes() for p in files}
                        if candidate.is_dir():
                            buffer=io.BytesIO()
                            with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as bundle:
                                for p,body in contents.items():
                                    info=zipfile.ZipInfo(p.relative_to(candidate).as_posix(),(1980,1,1,0,0,0))
                                    info.compress_type=zipfile.ZIP_DEFLATED
                                    bundle.writestr(info,body)
                            payload=buffer.getvalue();filename='bundle.zip'
                        else:
                            payload=contents[candidate];filename=candidate.name
                        digest=hashlib.sha256(payload).hexdigest()
                        identity=(key,digest)
                        # Serialize import and consumption with settings changes. A completed
                        # import is recorded transactionally, so failed deletion never duplicates it.
                        with self.library.lock:
                            with self.library.db() as db:
                                enabled=db.execute('SELECT enabled FROM watchers WHERE id=?',(watcher['id'],)).fetchone()
                                exists=db.execute('SELECT 1 FROM watch_imports WHERE path=? AND digest=?',identity).fetchone()
                            if not enabled or not enabled[0] or not self.library.setting('watching_enabled'):continue
                            if not exists:self.library.import_report(payload,filename,{'source':'Watched folder'},identity)
                            current=self.files(candidate)
                            if current!=files or any(p.read_bytes()!=body for p,body in contents.items()):
                                raise ValueError('Input changed during import; original retained.')
                            for p in files:p.unlink()
                            if candidate.is_dir():
                                for directory in sorted((p for p in candidate.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):directory.rmdir()
                                candidate.rmdir()
                            imported=True
                            self.observed.pop(key,None)
                    except (OSError,ValueError,zipfile.BadZipFile) as exc:
                        error=f'{candidate.name}: {exc}'
            except OSError as exc:error=str(exc)
            with self.library.db() as db:
                db.execute('UPDATE watchers SET last_checked=?,last_import=CASE WHEN ? THEN ? ELSE last_import END,error=? WHERE id=?',
                           (now(),imported,now(),error,watcher['id']))
        self.observed={k:v for k,v in self.observed.items() if k in active}
