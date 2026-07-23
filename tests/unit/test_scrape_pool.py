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

import logging
import threading

from src.data_manager.collectors.scrapers.scrape_pool import HostLimiter, run_seeds


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


class TestRunSeedsBounds:
    """``run_seeds`` fans seeds across a bounded pool, capped per host.

    The concurrency assertions use a ``threading.Barrier`` sized to the expected
    in-flight count so overlap is *forced* rather than hoped for: a pool that
    fails to reach that many concurrent crawls leaves a thread stuck at the
    barrier until it times out, which surfaces as a clean failure instead of a
    flaky one. Upper-bound assertions then confirm the pool never exceeds its
    global or per-host limit.
    """

    def test_independent_seeds_run_in_parallel(self):
        """workers=8 over 8 independent seeds: peak in-flight fetch exceeds 1."""
        seeds = [f"https://host{i}.example.edu/" for i in range(8)]
        all_in_flight = threading.Barrier(len(seeds), timeout=5)
        tracker = _PeakTracker(hold_iters=0)

        def scrape_one(seed):
            with tracker.enter():
                all_in_flight.wait()
            return 1

        total = run_seeds(seeds, scrape_one, workers=8, per_host_workers=8)

        assert total == 8
        assert tracker.peak > 1

    def test_pool_is_bounded_to_worker_count(self):
        """workers=2 over 8 seeds never exceeds 2 crawls in flight at once."""
        seeds = [f"https://host{i}.example.edu/" for i in range(8)]
        pair = threading.Barrier(2, timeout=5)
        tracker = _PeakTracker(hold_iters=0)

        def scrape_one(seed):
            with tracker.enter():
                pair.wait()
            return 1

        total = run_seeds(seeds, scrape_one, workers=2, per_host_workers=8)

        assert total == 8
        assert 1 <= tracker.peak <= 2

    def test_per_host_cap_bounds_single_host(self):
        """workers=8 but per-host cap 4 over 8 same-host seeds: peak per host <= 4."""
        seeds = [f"https://docs.example.edu/page{i}" for i in range(8)]
        quad = threading.Barrier(4, timeout=5)
        tracker = _PeakTracker(hold_iters=0)

        def scrape_one(seed):
            with tracker.enter():
                quad.wait()
            return 1

        total = run_seeds(seeds, scrape_one, workers=8, per_host_workers=4)

        assert total == 8
        assert 1 <= tracker.peak <= 4

    def test_one_failing_seed_does_not_abort_batch(self, caplog):
        """One raising seed is isolated: the other three still run and are summed."""
        seeds = [
            "https://a.example.edu/",
            "https://b.example.edu/",
            "https://c.example.edu/",
            "https://boom.example.edu/",
        ]

        def scrape_one(seed):
            if "boom" in seed:
                raise RuntimeError("kaboom")
            return 5

        with caplog.at_level(logging.WARNING):
            total = run_seeds(seeds, scrape_one, workers=4, per_host_workers=4)

        assert total == 15
        assert "boom.example.edu" in caplog.text

    def test_all_seeds_failing_returns_zero(self, caplog):
        """Every seed raising returns 0 rather than propagating; each is logged."""
        seeds = [f"https://host{i}.example.edu/" for i in range(3)]

        def scrape_one(seed):
            raise RuntimeError("nope")

        with caplog.at_level(logging.WARNING):
            total = run_seeds(seeds, scrape_one, workers=3, per_host_workers=3)

        assert total == 0
        assert sum("seed scrape failed" in r.getMessage() for r in caplog.records) == 3

    def test_total_accumulated_without_loss(self):
        """Concurrent seeds contribute their exact per-seed counts to the total."""
        seeds = [f"https://host{i % 5}.example.edu/page{i}" for i in range(50)]
        counts = {seed: i + 1 for i, seed in enumerate(seeds)}

        def scrape_one(seed):
            return counts[seed]

        total = run_seeds(seeds, scrape_one, workers=8, per_host_workers=4)

        assert total == sum(counts.values())

    def test_workers_one_runs_seeds_serially_in_input_order(self):
        """workers=1 visits seeds strictly one at a time in their input order."""
        seeds = [f"https://host{i}.example.edu/" for i in range(6)]
        calls = []
        calls_lock = threading.Lock()
        tracker = _PeakTracker()

        def scrape_one(seed):
            with tracker.enter():
                with calls_lock:
                    calls.append(seed)
            return 1

        total = run_seeds(seeds, scrape_one, workers=1, per_host_workers=4)

        assert total == 6
        assert calls == seeds
        assert tracker.peak == 1


class TestRunSeedsSummary:
    """``run_seeds`` logs exactly one completion summary after the pool drains."""

    def _summary_records(self, caplog):
        return [
            r
            for r in caplog.records
            if "scrape phase complete" in r.getMessage().lower()
        ]

    def test_summary_logged_exactly_once_with_all_fields(self, caplog):
        """One summary line reports seed count, workers, per-host cap, elapsed time."""
        seeds = [f"https://host{i}.example.edu/" for i in range(5)]

        def scrape_one(seed):
            return 1

        with caplog.at_level(logging.INFO):
            run_seeds(seeds, scrape_one, workers=3, per_host_workers=2)

        records = self._summary_records(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        # seed count, worker count, per-host cap all present
        assert "5" in message
        assert "3" in message
        assert "2" in message
        # elapsed wall-clock reported in seconds
        assert "s" in message.lower()

    def test_summary_reports_effective_clamped_values(self, caplog):
        """The summary reflects the clamped effective workers / per-host cap."""
        seeds = ["https://only.example.edu/"]

        def scrape_one(seed):
            return 1

        with caplog.at_level(logging.INFO):
            run_seeds(seeds, scrape_one, workers=0, per_host_workers=-4)

        records = self._summary_records(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        # workers=0 and per_host_workers=-4 both clamp to 1
        assert "1 seed" in message
        assert "1 worker" in message

    def test_summary_emitted_even_when_all_seeds_fail(self, caplog):
        """The summary still fires once after a batch where every seed raised."""
        seeds = [f"https://host{i}.example.edu/" for i in range(3)]

        def scrape_one(seed):
            raise RuntimeError("nope")

        with caplog.at_level(logging.INFO):
            run_seeds(seeds, scrape_one, workers=3, per_host_workers=3)

        assert len(self._summary_records(caplog)) == 1
