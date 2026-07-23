"""Unit tests for the bounded parallel scrape helper (issue #136).

Covers ``src/data_manager/collectors/scrapers/scrape_pool.py`` — the standalone,
directly unit-testable pool + per-host semaphore registry that ``ScraperManager``
delegates its parallel link-scrape phase to. These tests assert on an *observed
peak concurrency* counter (incremented/decremented around an injected fake
workload, tracking its max) rather than wall-clock, so they are stable on loaded
CI.

The ``HostLimiter`` under test is a lock-guarded ``dict[str, threading.Semaphore]``
exposing a context manager keyed by hostname: entering ``acquire(host)`` blocks
until a slot for that host is free, and leaving it releases the slot.
"""

import threading

from src.data_manager.collectors.scrapers.scrape_pool import HostLimiter


class _PeakTracker:
    """Records the maximum number of threads simultaneously inside its ``enter``.

    ``enter()`` is a context manager that bumps a live counter, remembers the
    high-water mark, holds for a short spin so contending threads overlap, then
    decrements. Assertions read ``peak`` after the threads join.
    """

    def __init__(self, hold_iters=200_000):
        self._lock = threading.Lock()
        self._live = 0
        self.peak = 0
        self._hold_iters = hold_iters

    def _spin(self):
        # Busy-hold (no sleep) so overlap is observable without relying on the
        # scheduler waking a sleeping thread at a particular time.
        acc = 0
        for i in range(self._hold_iters):
            acc += i
        return acc

    class _Ctx:
        def __init__(self, outer):
            self._outer = outer

        def __enter__(self):
            outer = self._outer
            with outer._lock:
                outer._live += 1
                if outer._live > outer.peak:
                    outer.peak = outer._live
            outer._spin()
            return self

        def __exit__(self, *exc):
            outer = self._outer
            with outer._lock:
                outer._live -= 1
            return False

    def enter(self):
        return self._Ctx(self)


class TestHostLimiterPerHostCap:
    def test_same_host_never_exceeds_cap(self):
        """N threads all acquiring one host stay at or below the configured cap."""
        cap = 3
        host = "docs.example.edu"
        limiter = HostLimiter(cap)
        tracker = _PeakTracker()

        def worker():
            with limiter.acquire(host):
                with tracker.enter():
                    pass

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert tracker.peak >= 1
        assert tracker.peak <= cap

    def test_distinct_hosts_do_not_contend(self):
        """With cap 1, four distinct hosts all hold a slot simultaneously.

        The barrier only releases when all four workers are inside their own
        host's ``acquire`` block at once; if a per-host cap wrongly serialized
        across hosts, the barrier would time out and ``reached`` stay short.
        """
        cap = 1
        hosts = ["a.example.edu", "b.example.edu", "c.example.edu", "d.example.edu"]
        limiter = HostLimiter(cap)
        barrier = threading.Barrier(len(hosts), timeout=5)
        reached = []
        reached_lock = threading.Lock()

        def worker(host):
            with limiter.acquire(host):
                barrier.wait()
                with reached_lock:
                    reached.append(host)

        threads = [threading.Thread(target=worker, args=(h,)) for h in hosts]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(reached) == sorted(hosts)

    def test_slot_released_when_guarded_body_raises(self):
        """An exception inside ``acquire`` still frees the slot for a later acquirer."""
        cap = 1
        host = "docs.example.edu"
        limiter = HostLimiter(cap)

        class Boom(Exception):
            pass

        try:
            with limiter.acquire(host):
                raise Boom()
        except Boom:
            pass

        acquired = threading.Event()

        def worker():
            with limiter.acquire(host):
                acquired.set()

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)

        assert (
            acquired.is_set()
        ), "slot was not released after exception; acquirer deadlocked"
