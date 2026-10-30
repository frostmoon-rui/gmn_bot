"""Process-held instance lock, shared by desktop and boot sessions."""
import os
from pathlib import Path


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        try:
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        return True

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def require_instance(script):
    lock = InstanceLock(Path(script).resolve().parent / 'startup_runtime' / 'bot-instance.lock')
    if not lock.acquire():
        print('[Startup] Another bot instance owns this project; duplicate launch cancelled.', flush=True)
        raise SystemExit(0)
    return lock
