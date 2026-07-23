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

``run_seeds`` is the bounded seed pool: it fans a list of seed URLs across a
``ThreadPoolExecutor`` sized by ``workers``, wraps each per-seed crawl in the
``HostLimiter`` so the per-host cap is enforced on top of the global bound, and
sums the per-seed resource counts on the calling thread as futures complete. A
seed whose crawl raises is isolated — the failure is logged and contributes zero
while every other seed still runs.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from urllib.parse import urlsplit

from src.utils.logging import get_logger

logger = get_logger(__name__)


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


def run_seeds(seeds, scrape_one, workers, per_host_workers):
    """Scrape ``seeds`` concurrently, bounded globally and per host.

    ``seeds`` is an ordered iterable of seed URLs; ``scrape_one(seed)`` performs
    one seed's crawl and returns its resource count. Seeds run on a
    ``ThreadPoolExecutor`` capped at ``max(1, workers)`` threads, and each crawl
    holds its host's slot (derived from the seed URL's network location) via a
    ``HostLimiter`` bounded by ``per_host_workers`` — so the per-host cap applies
    even when the global pool has idle capacity.

    Per-seed failures are isolated: an exception raised by ``scrape_one`` is
    logged and counts as zero, leaving the rest of the batch untouched. The
    returned total is the exact sum of the successful per-seed counts, accumulated
    on the calling thread as futures complete. With ``workers`` of 1 the seeds run
    one at a time in their input order, reproducing the sequential path.
    """
    seeds = list(seeds)
    workers = max(1, int(workers))
    limiter = HostLimiter(per_host_workers)

    def _work(seed):
        host = urlsplit(seed).netloc
        with limiter.acquire(host):
            return scrape_one(seed)

    total = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_work, seed): seed for seed in seeds}
        for future in as_completed(futures):
            seed = futures[future]
            try:
                total += future.result()
            except Exception:
                logger.warning("seed scrape failed: %s", seed, exc_info=True)
    return total
