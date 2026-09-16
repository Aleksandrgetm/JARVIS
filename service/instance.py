"""Kernel-managed advisory lock; stale PID text never blocks a new process."""
import fcntl
import os


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path):
        self.path = path
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise AlreadyRunning('JARVIS уже запущен.') from None
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd
        return self

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        # Never unlink: a competing process may already hold the same inode.


def is_locked(path):
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)
