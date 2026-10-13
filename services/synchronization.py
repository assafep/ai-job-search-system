"""Reentrant instance locks for shared registries and persistence clients."""

from functools import wraps


def synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked
