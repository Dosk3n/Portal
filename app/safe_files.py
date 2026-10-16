"""Read package files through pinned directories without following symlinks."""
from contextlib import contextmanager
import os
import errno
from pathlib import Path
import stat

_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
MAX_PACKAGE_ENTRIES = 2000
MAX_PACKAGE_DEPTH = 64


def _parts(relative):
    value = os.fspath(relative)
    parts = value.split('/')
    if not value or value.startswith('/') or any(part in {'', '.', '..'} for part in parts) or '\\' in value:
        raise ValueError('Unsafe package path.')
    return parts


@contextmanager
def open_directory(root):
    """Pin a directory, rejecting symbolic links in every ancestor component.

    A directory descriptor is also accepted, so callers can retain a pinned root.
    """
    if isinstance(root, int):
        descriptor = os.dup(root)
    else:
        path = os.path.abspath(os.fspath(root))
        descriptor = os.open('/', _DIRECTORY)
        try:
            for part in path.split('/'):
                if not part:
                    continue
                child = os.open(part, _DIRECTORY, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
        except BaseException:
            os.close(descriptor)
            raise
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError('Package directory is unavailable.')
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def open_regular(root, relative, index=False):
    """Open a regular file beneath a pinned root, including nested paths."""
    value = os.fspath(relative)
    if value.startswith('/'):
        raise ValueError('Unsafe package path.')
    if index and value.endswith('/'):
        value = value[:-1]
    parts = _parts(value) if value else ['index.html'] if index else _parts(value)
    with open_directory(root) as directory:
        parent = os.dup(directory)
        try:
            for component in parts[:-1]:
                child = os.open(component, _DIRECTORY, dir_fd=parent)
                os.close(parent)
                parent = child
            descriptor = os.open(parts[-1], _FILE, dir_fd=parent)
            filename = parts[-1]
            try:
                if index and stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    child = os.open('index.html', _FILE, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                    filename = 'index.html'
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError('Packages may contain only regular files and directories.')
                stream = os.fdopen(descriptor, 'rb')
                stream.portal_filename = filename
            except BaseException:
                os.close(descriptor)
                raise
            with stream:
                yield stream
        finally:
            os.close(parent)


def read_regular(root, relative, maximum=MAX_PACKAGE_BYTES):
    with open_regular(root, relative) as stream:
        if maximum is not None and os.fstat(stream.fileno()).st_size > maximum:
            raise ValueError('File exceeds the allowed size.')
        value = stream.read() if maximum is None else stream.read(maximum + 1)
        if maximum is not None and len(value) > maximum:
            raise ValueError('File exceeds the allowed size.')
        return value


def _entries(directory, count, maximum):
    # Stream directory names: an oversized directory must not allocate an
    # unbounded list before the entry limit can be enforced.
    with os.scandir(directory) as entries:
        for entry in entries:
            count[0] += 1
            if count[0] > maximum:
                raise ValueError('Package contains too many entries.')
            yield entry.name


def _sync_directory(directory):
    try:
        os.fsync(directory)
    except OSError as error:
        if error.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise


def iter_files(root, maximum=MAX_PACKAGE_BYTES, max_files=MAX_PACKAGE_ENTRIES, max_depth=MAX_PACKAGE_DEPTH):
    """List a bounded package while refusing links and special files."""
    result = []
    count = [0]
    total = 0
    def visit(directory, prefix, depth):
        nonlocal total
        if depth > max_depth:
            raise ValueError('Package directory nesting is too deep.')
        for name in _entries(directory, count, max_files):
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            relative = prefix + name
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, _DIRECTORY, dir_fd=directory)
                try:
                    visit(child, relative + '/', depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                with open_regular(directory, name) as stream:
                    total += os.fstat(stream.fileno()).st_size
                    if total > maximum:
                        raise ValueError('Package exceeds the expanded size limit.')
                result.append(relative)
            else:
                raise ValueError('Packages may contain only regular files and directories; symlinks are not allowed.')
    with open_directory(root) as directory:
        visit(directory, '', 0)
    return result


def copy_tree(source, destination, maximum=MAX_PACKAGE_BYTES, max_files=MAX_PACKAGE_ENTRIES, max_depth=MAX_PACKAGE_DEPTH):
    """Copy and sync a bounded package without dereferencing any links."""
    count = [0]
    total = 0
    def copy_directory(source_fd, target_fd, depth):
        nonlocal total
        if depth > max_depth:
            raise ValueError('Package directory nesting is too deep.')
        for name in _entries(source_fd, count, max_files):
            info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, _DIRECTORY, dir_fd=source_fd)
                try:
                    os.mkdir(name, 0o700, dir_fd=target_fd)
                    target_child = os.open(name, _DIRECTORY, dir_fd=target_fd)
                    try:
                        copy_directory(child, target_child, depth + 1)
                    finally:
                        os.close(target_child)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                with open_regular(source_fd, name) as incoming:
                    if os.fstat(incoming.fileno()).st_size > maximum - total:
                        raise ValueError('Package exceeds the expanded size limit.')
                    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                         0o600, dir_fd=target_fd)
                    with os.fdopen(descriptor, 'wb') as outgoing:
                        while True:
                            block = incoming.read(min(1024 * 1024, maximum - total + 1))
                            if not block:
                                break
                            total += len(block)
                            if total > maximum:
                                raise ValueError('Package exceeds the expanded size limit.')
                            outgoing.write(block)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
            else:
                raise ValueError('Packages may contain only regular files and directories; symlinks are not allowed.')
        _sync_directory(target_fd)
    with open_directory(source) as source_fd:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=False)
        with open_directory(destination) as target_fd:
            copy_directory(source_fd, target_fd, 0)
        # Persist the newly created report/version name and its token parent.
        with open_directory(destination.parent) as parent_fd:
            _sync_directory(parent_fd)
        with open_directory(destination.parent.parent) as parent_fd:
            _sync_directory(parent_fd)
