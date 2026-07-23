"""Bounded parallel scrape helpers (issue #136).

Standalone, directly unit-testable machinery for the standard (non-selenium)
link-scrape phase. ``ScraperManager`` delegates its per-seed crawl loop here so
that the pool and per-host bounding logic can be tested without constructing a
manager, a persistence service, or a Postgres connection.

``HostLimiter`` is the per-host concurrency cap: a lock-guarded registry of one
``threading.Semaphore`` per hostname, exposed as a context manager. A worker
acquires its seed's host slot for the duration of the crawl, so at most
``per_host_workers`` seeds targeting the same host run concurrently, independently
of and in addition to the global worker pool bound.
"""

import threading
from contextlib import contextmanager


class HostLimiter:
    """Caps concurrent acquisitions per host via a registry of semaphores.

    Semaphores are created lazily on first use of a host and reused thereafter.
    The registry mutation is guarded by a lock; the semaphore itself provides the
    actual per-host bound. Each worker acquires exactly one semaphore and holds no
    other lock while blocking on it, so there is no acquisition ordering and no
    deadlock cycle.
    """

    def __init__(self, per_host_workers):
        self._per_host = max(1, int(per_host_workers))
        self._lock = threading.Lock()
        self._semaphores = {}

    def _semaphore_for(self, host):
        with self._lock:
            sem = self._semaphores.get(host)
            if sem is None:
                sem = threading.Semaphore(self._per_host)
                self._semaphores[host] = sem
            return sem

    @contextmanager
    def acquire(self, host):
        """Hold a slot for ``host`` for the duration of the ``with`` block.

        Blocks until a slot is free, and releases it on exit even if the guarded
        body raises, so a failing crawl never leaks its host slot.
        """
        sem = self._semaphore_for(host)
        sem.acquire()
        try:
            yield
        finally:
            sem.release()
