"""Bounded parallel scrape helpers (issue #136).

Standalone, directly unit-testable machinery for the standard (non-selenium)
link-scrape phase. ``ScraperManager`` delegates its per-seed crawl loop here so
that the pool and per-host bounding logic can be tested without constructing a
manager, a persistence service, or a Postgres connection.

``HostLimiter`` is the per-host concurrency cap: a lock-guarded registry of one
``threading.Semaphore`` per hostname, exposed as a context manager. A worker
acquires its seed's host slot for the duration of the crawl, so at most
``per_host_workers`` seeds targeting the same host run concurrently, independently
of and in addition to the global worker pool bound. A crawl that redirects onto a
different host moves its slot there (``rekey_current``) so the cap follows the host
actually being requested. Limiters are shared process-wide by cap
(``shared_host_limiter``), because a budget owed to a server has to hold across
every batch this process has in flight, not within one call.

``run_seeds`` is the bounded seed pool: it fans a list of seed URLs across a
``ThreadPoolExecutor`` sized by ``workers``, wraps each per-seed crawl in the
``HostLimiter`` so the per-host cap is enforced on top of the global bound, and
sums the per-seed resource counts on the calling thread as futures complete. A
seed whose crawl raises is isolated — the failure is logged and contributes zero
while every other seed still runs.

``host_key`` and ``interleave_by_host`` are the two pure functions the pool leans
on, kept separate so their behavior is assertable without threads: the first
decides *which* slot a seed contends for, the second decides *when* each seed is
handed to the pool.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from itertools import zip_longest
from urllib.parse import urlsplit

from src.utils.logging import get_logger

logger = get_logger(__name__)


def host_key(url):
    """Return the canonical per-host slot key for ``url``.

    The cap exists to be polite to a *server*, so every spelling that reaches the
    same server must contend for the same slot. ``urlsplit(...).netloc`` does not
    give that: it keeps letter case, an explicitly written port, and any
    ``user:pw@`` prefix, so ``https://EXAMPLE.com/a``, ``https://example.com:443/b``
    and ``https://example.com/c`` would take three separate semaphores and together
    run three times the configured per-host concurrency at one host.

    ``.hostname`` normalizes all three at once — it lowercases, drops userinfo, and
    drops the port. Dropping the port is deliberate rather than incidental: a
    non-default port is still the same machine, so ``example.com:8443`` must not buy
    itself a second slot alongside ``example.com``. A seed with no parseable
    hostname falls back to the raw string, which keeps the key stable and non-empty
    so malformed seeds contend only with themselves instead of collapsing into one
    shared ``""`` bucket.

    ``urlsplit`` *raises* on a malformed authority (``http://[broken/path`` ->
    ``ValueError: Invalid IPv6 URL``), and :func:`interleave_by_host` keys every
    seed on the calling thread before a single future is submitted — outside the
    per-seed fault isolation in :func:`run_seeds`. An unguarded parse would let one
    bad line in a source list abort the whole ingest, so the same raw-seed fallback
    covers unparseable URLs: the seed keeps a stable private slot and fails (or
    succeeds) on its own.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        logger.debug("Unparseable seed URL %r; keying it on the raw string", url)
        return url
    return parts.hostname or parts.netloc or url


def interleave_by_host(seeds):
    """Reorder ``seeds`` round-robin across hosts, stable within each host.

    A worker blocked in ``HostLimiter.acquire`` is still holding a thread in the
    global pool. So with a host-grouped list — the shape the catalog actually
    produces, with all 212 ``docs.rc.fas.harvard.edu`` seeds adjacent — the first
    ``workers`` threads all take that host, ``per_host_workers`` of them run and the
    rest block, and seeds for a completely idle host sit in the queue with no thread
    free to pick them up. Throughput then depends on input order, and a mixed-host
    run can crawl at nearly the per-host rate.

    Rotating the submission order fixes that at the point where it is cheap: the
    first ``n`` seeds dispatched span up to ``n`` distinct hosts, so pile-up on any
    one host is bounded by how many of its seeds appear in the first window rather
    than by how the list happened to be sorted. Relative order within a host is
    preserved, and hosts are visited in first-appearance order, so the result is
    fully deterministic. Keying on :func:`host_key` means spelling variants rotate as
    one host, matching what the limiter will actually enforce.

    This is intentionally not a fair scheduler. Per-host queues with slot-aware
    dispatch would guarantee no worker ever blocks; that is a larger machine than
    this phase needs, and it would trade the deterministic ordering above for
    completion-order nondeterminism.
    """
    by_host = {}
    for seed in seeds:
        by_host.setdefault(host_key(seed), []).append(seed)

    rotated = []
    queues = list(by_host.values())
    for row in zip_longest(*queues):
        rotated.extend(seed for seed in row if seed is not None)
    return rotated


class HostLimiter:
    """Caps concurrent acquisitions per host via a registry of semaphores.

    Semaphores are created lazily on first use of a host and reused thereafter.
    The registry mutation is guarded by a lock; the semaphore itself provides the
    actual per-host bound. Each worker acquires exactly one semaphore and holds no
    other lock while blocking on it, so there is no acquisition ordering and no
    deadlock cycle.

    A thread holds at most one slot at a time, and which host that slot belongs to
    can change mid-crawl — see :meth:`rekey_current`. That "current slot" is tracked
    per thread, which is the exactly right scope here: one seed crawl runs start to
    finish on one pool thread.
    """

    def __init__(self, per_host_workers):
        self._per_host = max(1, int(per_host_workers))
        self._lock = threading.Lock()
        self._semaphores = {}
        self._held = threading.local()

    @property
    def per_host_workers(self):
        """The effective (clamped) per-host cap this limiter enforces."""
        return self._per_host

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
        body raises, so a failing crawl never leaks its host slot. The slot released
        on exit is whichever host is current at that moment, so a :meth:`rekey_current`
        performed inside the block is honored rather than double-releasing the
        original host.
        """
        sem = self._semaphore_for(host)
        sem.acquire()
        previous = getattr(self._held, "host", None)
        self._held.host = host
        try:
            yield
        finally:
            current = getattr(self._held, "host", None)
            self._held.host = previous
            if current is not None:
                self._semaphore_for(current).release()

    def rekey_current(self, host):
        """Move this thread's held slot to ``host``.

        ``requests`` follows redirects transparently and ``LinkScraper`` resolves the
        rest of a crawl against the response's *final* URL, so a seed on one host can
        spend its entire crawl requesting another. Keyed on the seed alone, two such
        crawls from different origins occupy two semaphores while pounding one
        destination — the cap is enforced against hosts nobody is talking to.

        Called with the host actually being requested, this hands the old slot back
        and takes the destination's. The release happens *before* the acquire on
        purpose: a thread never holds two host slots at once, so two crawls that swap
        hosts in opposite directions cannot deadlock against each other. The cost is
        that the destination can momentarily hold one acquirer more than the cap
        during the swap window, which is the right trade against a deadlock.

        A no-op when this thread holds no slot (the sequential and selenium/SSO paths
        never enter the limiter) or is already keyed to ``host`` (the common case,
        called once per response).
        """
        current = getattr(self._held, "host", None)
        if current is None or current == host:
            return
        self._semaphore_for(current).release()
        # Hold nothing while blocking, and leave nothing to release if the acquire
        # below is interrupted.
        self._held.host = None
        self._semaphore_for(host).acquire()
        self._held.host = host


# The per-host cap is a politeness budget owed to a *server*, so it has to hold
# across everything this process is doing at once, not within one call. A single
# ``ScraperManager`` is shared by the data-manager's cron ingest thread and the
# uploader's `/document_index/upload_url` handler (see `src/bin/service_data_manager.py`),
# so batches genuinely overlap; a limiter built per ``run_seeds`` call would let each
# batch spend the full budget independently and multiply the real request rate at one
# host by the number of batches in flight.
#
# Keyed by the effective cap: batches configured differently get different limiters.
# In practice every batch reads the same config, so they share one. Callers that need
# an isolated bound pass ``limiter=`` to :func:`run_seeds` explicitly.
_SHARED_LIMITERS = {}
_SHARED_LIMITERS_LOCK = threading.Lock()


def shared_host_limiter(per_host_workers):
    """Return the process-wide :class:`HostLimiter` for ``per_host_workers``."""
    cap = max(1, int(per_host_workers))
    with _SHARED_LIMITERS_LOCK:
        limiter = _SHARED_LIMITERS.get(cap)
        if limiter is None:
            limiter = HostLimiter(cap)
            _SHARED_LIMITERS[cap] = limiter
        return limiter


def reset_shared_host_limiters():
    """Drop the process-wide limiter registry.

    Exists so tests can start from a clean bound; production code has no reason to
    call it, and doing so mid-run would detach in-flight crawls from the cap.
    """
    with _SHARED_LIMITERS_LOCK:
        _SHARED_LIMITERS.clear()


def run_seeds(seeds, scrape_one, workers, per_host_workers, limiter=None):
    """Scrape ``seeds`` concurrently, bounded globally and per host.

    ``seeds`` is an ordered iterable of seed URLs; ``scrape_one(seed)`` performs
    one seed's crawl and returns its resource count. Seeds run on a
    ``ThreadPoolExecutor`` capped at ``max(1, workers)`` threads, and each crawl
    holds its host's slot (derived from the seed URL's network location) via a
    ``HostLimiter`` bounded by ``per_host_workers`` — so the per-host cap applies
    even when the global pool has idle capacity.

    That limiter is the process-wide one for ``per_host_workers``
    (:func:`shared_host_limiter`), so concurrent batches contend with each other
    rather than each spending the budget again. Pass ``limiter`` to override it —
    the caller then owns the bound, and ``per_host_workers`` is used only for the
    summary line.

    With more than one worker the seeds are dispatched round-robin across hosts
    (see :func:`interleave_by_host`) so a host-grouped input list cannot starve the
    other hosts. With ``workers`` of 1 that rotation is skipped and the input order
    is preserved exactly: there is no shared pool to monopolize, and keeping
    ``scrape_workers: 1`` a byte-for-byte replay of the old sequential path is worth
    more than a reordering that could not help it.

    Per-seed failures are isolated: an exception raised by ``scrape_one`` is
    logged and counts as zero, leaving the rest of the batch untouched. The
    returned total is the exact sum of the successful per-seed counts, accumulated
    on the calling thread as futures complete.

    Exactly one summary line is logged once the pool drains, reporting the seed
    count, the effective (clamped) worker count and per-host cap, and the elapsed
    wall-clock time — regardless of whether any seed failed.
    """
    seeds = list(seeds)
    workers = max(1, int(workers))
    if workers > 1:
        seeds = interleave_by_host(seeds)
    if limiter is None:
        limiter = shared_host_limiter(per_host_workers)
    started = time.perf_counter()

    def _work(seed):
        with limiter.acquire(host_key(seed)):
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

    elapsed = time.perf_counter() - started
    logger.info(
        "link scrape phase complete: %d seeds, %d workers, per-host cap %d, %.2fs elapsed",
        len(seeds),
        workers,
        limiter.per_host_workers,
        elapsed,
    )
    return total
