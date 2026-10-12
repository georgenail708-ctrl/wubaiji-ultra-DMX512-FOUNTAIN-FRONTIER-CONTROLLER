"""One thread-owned bake writer; complete stores still use original DiskCache.

Only stores constructed inside the exact current token participate. No previous
published store, consumer thread, transaction or frame value is adopted. Every
registered cache is closed on its actual writer thread before returning to the
original freshness/publication chain. Reference: DiskCache 5.6.3 core.py 2337-2358.
"""
from pathlib import Path
import threading


_CURRENT = threading.local()


class _FrameWriterScope:
    def __init__(self):
        self.thread = threading.current_thread()
        self.active = self.failed = False
        self.stores = []
        self._bindings = {}

    def is_current(self):
        return (self.active and getattr(_CURRENT, "token", None) is self
                and threading.current_thread() is self.thread)

    def __enter__(self):
        if getattr(_CURRENT, "token", None) is not None:
            raise RuntimeError("Nested owned frame writer scopes are not supported")
        if threading.current_thread() is not self.thread or self.active or self.failed:
            raise RuntimeError("Owned frame writer scope cannot change its thread or be reused after failure")
        self.active = True
        _CURRENT.token = self
        return self

    def __exit__(self, *_exception):
        if not self.is_current():
            raise RuntimeError("Owned frame writer exit does not own the current thread token")
        errors = []
        try:
            for store in self.stores:
                try:
                    self._close_store(store)
                except BaseException as error:
                    errors.append(error)
        finally:
            self.active = False
            self.failed = bool(errors)
            _CURRENT.token = None
            self.stores.clear()
            self._bindings.clear()
        if errors:
            raise errors[0]
        return False

    def cache_for(self, store):
        cache, temporary = store._disk, store._temporary
        if cache is None or temporary is None:
            raise RuntimeError("Owned writer Cache lacks its actual private store directory")
        binding = self._bindings.get(id(store))
        if binding is None:
            directory = Path(temporary.name).resolve()
            if not directory.name.startswith("qascade_bake_") or directory != Path(cache.directory).resolve():
                raise RuntimeError("Owned writer Cache is outside its original private directory")
            self._bindings[id(store)] = (cache, temporary)
        elif binding[0] is not cache or binding[1] is not temporary:
            raise RuntimeError("Owned writer Cache/store directory identity changed")
        return cache

    def _close_store(self, store):
        with store._lock:
            if store._writer_owner is not self:
                raise RuntimeError("Registered frame store lost its original writer token")
            if store._disk is not None:
                cache = self.cache_for(store)
                close_cache_connection(cache)
            store._writer_owner = None


class _RetainedCacheContext:
    def __init__(self, store, owner):
        self.store, self.owner = store, owner
        self.cache = owner.cache_for(store)

    def __enter__(self):
        return self.cache.__enter__()

    def __exit__(self, *exception):
        if (self.owner.is_current() and self.store._writer_owner is self.owner
                and self.owner.cache_for(self.store) is self.cache):
            return None
        return self.cache.__exit__(*exception)


def owned_frame_writer_scope():
    """Root's original owned bake boundary is the only product caller."""
    return _FrameWriterScope()


def register_store(store):
    owner = getattr(_CURRENT, "token", None)
    if owner is not None and owner.is_current():
        store._writer_owner = owner
        owner.stores.append(store)


def cache_context(store):
    owner = store._writer_owner
    if owner is not None and owner.is_current():
        return _RetainedCacheContext(store, owner)
    return store._disk


def check_clear_owner(store):
    owner = store._writer_owner
    if owner is not None and (owner.active or owner.failed) and threading.current_thread() is not owner.thread:
        raise RuntimeError("Foreign thread cannot clear an active or failed owned writer store")


def close_cache_connection(cache):
    cache.close()
    if getattr(cache._local, "con", None) is not None:
        raise RuntimeError("Frame store SQLite connection survived Cache.close")


def after_store_clear(store):
    owner = store._writer_owner
    if owner is None:
        return
    owner._bindings.pop(id(store), None)
    if not owner.is_current():
        store._writer_owner = None
