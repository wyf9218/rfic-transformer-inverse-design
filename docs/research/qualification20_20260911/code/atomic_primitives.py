from contextlib import contextmanager
import fcntl,json,os,tempfile,hashlib
from pathlib import Path
class BusyStudy(RuntimeError):
    pass

def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def atomic_json(path, value, *, immutable=False):
    """Durable complete JSON, exclusive publication for immutable records."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False) + "\n").encode()
    fd, temp = tempfile.mkstemp(prefix=".unpublished-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            os.link(temp, path)
        else:
            os.replace(temp, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return hashlib.sha256(raw).hexdigest()

@contextmanager
def lease(path):
    """Lock inode is permanent: never unlink a possibly held lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusyStudy(str(path)) from exc
        os.set_inheritable(fd, True)
        yield fd
    finally:
        # close, not LOCK_UN: inherited children must retain the shared lease.
        os.close(fd)

