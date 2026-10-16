"""Consume stable inbox submissions; keep failed or changing inputs untouched."""
import hashlib
import io
import os
import stat
from contextlib import ExitStack
from pathlib import Path
import threading
import time
import zipfile

from library import HTML, MAX_FILES, MAX_UPLOAD, now
from packages import validate_package
from safe_files import open_directory, paths_overlap


class Submission:
    """Pin inbox directories so swaps cannot redirect reads or deletion."""
    def __init__(self, root_fd, name):
        self.stack = ExitStack()
        self.files = []
        self.directories = []
        self.entries = 0
        self.total = 0
        try:
            self._walk(root_fd, name, '', 0)
            self.folder = bool(self.directories)
        except BaseException:
            self.stack.close()
            raise

    def __enter__(self): return self
    def __exit__(self, *args): return self.stack.__exit__(*args)

    @staticmethod
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _walk(self, parent_fd, name, relative, depth):
        self.entries += 1
        if self.entries > MAX_FILES + 1 or depth > 64:
            raise ValueError('Submission exceeds 2,000 entries or 64 folder levels.')
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            self.stack.callback(os.close, fd)
            if (info.st_dev, info.st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
                raise ValueError('Input changed during import; original retained.')
            self.directories.append((parent_fd, name, fd, info))
            with os.scandir(fd) as entries:
                names = []
                for entry in entries:
                    names.append(entry.name)
                    if len(names) + self.entries > MAX_FILES + 1:
                        raise ValueError('Submission exceeds 2,000 entries or 64 folder levels.')
                names.sort()
            for child in names:
                self._walk(fd, child, relative + '/' + child if relative else child, depth + 1)
        elif stat.S_ISREG(info.st_mode):
            self.total += info.st_size
            if self.total > MAX_UPLOAD:
                raise ValueError('Submission exceeds 50 MB.')
            self.files.append((relative or name, parent_fd, name, info))
        else:
            raise ValueError('Submission folders cannot contain symlinks or special files.')

    @property
    def signature(self):
        return tuple((relative, self.identity(info)) for relative, _, _, info in self.files)

    def read(self):
        contents = {}
        remaining = MAX_UPLOAD
        for relative, parent, name, initial in self.files:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode) or self.identity(opened) != self.identity(initial):
                    raise ValueError('Input changed during import; original retained.')
                chunks = []
                size = 0
                while True:
                    chunk = os.read(fd, min(65536, remaining + 1))
                    if not chunk: break
                    size += len(chunk)
                    remaining -= len(chunk)
                    if remaining < 0: raise ValueError('Submission exceeds 50 MB.')
                    chunks.append(chunk)
                if size != initial.st_size or self.identity(os.fstat(fd)) != self.identity(initial):
                    raise ValueError('Input changed during import; original retained.')
                contents[relative] = b''.join(chunks)
            finally:
                os.close(fd)
        return contents

    def consume(self, contents):
        # Compare directory identities using their pinned parents. Deletion never
        # traverses a pathname again, even if an external writer renames a folder.
        for parent, name, fd, initial in self.directories:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino):
                raise ValueError('Input changed during import; original retained.')
        for _, parent, name, initial in self.files:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or self.identity(current) != self.identity(initial):
                raise ValueError('Input changed during import; original retained.')
        if self.read() != contents:
            raise ValueError('Input changed during import; original retained.')
        for _, parent, name, _ in self.files:
            os.unlink(name, dir_fd=parent)
        for parent, name, _, _ in reversed(self.directories):
            os.rmdir(name, dir_fd=parent)


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
                    if paths_overlap(resolved,protected):
                        raise OSError('Watched folder now overlaps managed storage; import stopped.')
                if not root.is_dir(): raise OSError('Watched folder is unavailable.')
                with open_directory(root) as root_fd:
                    with os.scandir(root_fd) as entries:
                        candidates=sorted(entry.name for entry in entries)
                    for name in candidates:
                        if name.startswith('.'):continue
                        candidate=root/name
                        key=str(candidate);active.add(key)
                        try:
                            info=os.stat(name,dir_fd=root_fd,follow_symlinks=False)
                            if stat.S_ISLNK(info.st_mode):continue
                            if not stat.S_ISDIR(info.st_mode) and candidate.suffix.lower() not in HTML|{'.zip'}:continue
                            with Submission(root_fd,name) as submission:
                                if not submission.files:continue
                                signature=submission.signature
                                observation=self.observed.get(key)
                                if not observation or observation[0]!=signature:
                                    self.observed[key]=(signature,time.monotonic());continue
                                if time.monotonic()-observation[1]<self.stable_seconds:continue
                                contents=submission.read()
                                if submission.folder:
                                    buffer=io.BytesIO()
                                    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as bundle:
                                        for relative,body in contents.items():
                                            item=zipfile.ZipInfo(relative,(1980,1,1,0,0,0))
                                            item.compress_type=zipfile.ZIP_DEFLATED
                                            bundle.writestr(item,body)
                                    payload=buffer.getvalue();filename='bundle.zip'
                                else:
                                    payload=contents[name];filename=name
                                digest=hashlib.sha256(payload).hexdigest()
                                identity=(key,digest)
                                # Serialize import and consumption with settings changes. A completed
                                # import is recorded transactionally, so failed deletion never duplicates it.
                                with self.library.lock:
                                    with self.library.db() as db:
                                        enabled=db.execute('SELECT enabled FROM watchers WHERE id=?',(watcher['id'],)).fetchone()
                                        exists=db.execute('SELECT report_id FROM watch_imports WHERE path=? AND digest=?',identity).fetchone()
                                    if not enabled or not enabled[0] or not self.library.setting('watching_enabled'):continue
                                    duplicate=False
                                    if exists:
                                        try:
                                            report=self.library.report(exists['report_id'])
                                            validate_package(self.library.package_path(report['id']),report['entry'])
                                            duplicate=True
                                        except (LookupError,OSError,ValueError):
                                            pass
                                    if not duplicate:
                                        report=self.library.import_report(payload,filename,{'source':'Watched folder'},identity)
                                    submission.consume(contents)
                                    with self.library.db() as db:
                                        db.execute('INSERT INTO watch_activity(watcher_id,created_at,filename,outcome,report_id) VALUES (?,?,?,?,?)',
                                                   (watcher['id'],now(),candidate.name,'Duplicate skipped' if duplicate else 'Imported',report['id']))
                                        db.execute('DELETE FROM watch_activity WHERE id NOT IN (SELECT id FROM watch_activity ORDER BY id DESC LIMIT 100)')
                                    imported=imported or not duplicate
                                    self.observed.pop(key,None)
                        except (OSError,ValueError,zipfile.BadZipFile) as exc:
                            error=f'{candidate.name}: {exc}'
            except OSError as exc:error=str(exc)
            with self.library.db() as db:
                db.execute('UPDATE watchers SET last_checked=?,last_import=CASE WHEN ? THEN ? ELSE last_import END,error=? WHERE id=?',
                           (now(),imported,now(),error,watcher['id']))
        self.observed={k:v for k,v in self.observed.items() if k in active}
