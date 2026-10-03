"""Shared dashboard login throttling; stores no raw passwords."""
from collections import deque
import threading
import time
from .config import verify_app_password

_failures = deque()
_lock = threading.Lock()


def authenticate(password, now=None):
    now=time.monotonic() if now is None else now
    with _lock:
        while _failures and _failures[0] <= now-60:
            _failures.popleft()
        if len(_failures) >= 5:
            return False, 'Too many failed attempts. Wait one minute and try again.'
        if verify_app_password(password):
            _failures.clear()
            return True, ''
        _failures.append(now)
        return False, 'Invalid password.'
