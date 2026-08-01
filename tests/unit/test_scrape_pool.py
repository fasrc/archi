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

import pytest

from src.data_manager.collectors.scrapers.scrape_pool import (
    HostLimiter,
    host_key,
    interleave_by_host,
    reset_shared_host_limiters,
    run_seeds,
    shared_host_limiter,
)


# The default limiter is process-wide (so overlapping batches contend), which is
# shared state between tests. Clear the registry around every test so a case that
# saturates a host cannot influence the next one.
@pytest.fixture(autouse=True)
def _isolate_shared_limiters():
    reset_shared_host_limiters()
    yield
    reset_shared_host_limiters()


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


class TestHostKeyCanonicalization:
    """Host slots key on a canonical hostname, not the raw ``netloc``.

    ``urlsplit(...).netloc`` preserves letter case, an explicitly written port, and
    any userinfo prefix, so ``https://EXAMPLE.com/a`` and ``https://example.com:443/b``
    would each take a *different* semaphore and jointly exceed the configured
    per-host cap against what is, to the remote server, one host.
    """

    def test_case_port_and_userinfo_collapse_to_one_key(self):
        variants = [
            "https://EXAMPLE.com/a",
            "https://example.com:443/b",
            "https://user:pw@example.com/c",
            "https://example.com/d",
        ]
        assert {host_key(url) for url in variants} == {"example.com"}

    def test_non_default_port_shares_the_host_slot(self):
        """Politeness is per-server: a port variant must not buy a second slot."""
        assert host_key("https://example.com:8443/x") == host_key(
            "https://example.com/y"
        )

    def test_distinct_hosts_keep_distinct_keys(self):
        assert host_key("https://a.example.edu/") != host_key("https://b.example.edu/")

    def test_seed_without_a_hostname_falls_back_to_the_raw_string(self):
        """A malformed seed still gets a stable, non-empty key of its own."""
        assert host_key("not-a-url") == "not-a-url"

    def test_spelling_variants_share_the_per_host_cap(self):
        """per_host_workers=1 over four spellings of one host: never 2 in flight."""
        seeds = [
            "https://EXAMPLE.com/a",
            "https://example.com:443/b",
            "https://user:pw@example.com/c",
            "https://example.com/d",
        ]
        tracker = _PeakTracker()

        def scrape_one(seed):
            with tracker.enter():
                pass
            return 1

        total = run_seeds(seeds, scrape_one, workers=4, per_host_workers=1)

        assert total == 4
        assert tracker.peak == 1


class TestFairHostScheduling:
    """Dispatch order rotates across hosts so list grouping cannot starve a host.

    A worker that blocks in ``limiter.acquire`` still occupies a slot in the global
    pool. With a host-grouped seed list the first workers therefore all pile onto
    the same host, and seeds for an idle host sit queued behind them with no thread
    free to run them. Rotating the submission order bounds that pile-up to the
    per-host cap without a scheduler rewrite.
    """

    def test_grouped_seeds_dispatch_round_robin(self):
        seeds = [
            "https://a.example.edu/1",
            "https://a.example.edu/2",
            "https://a.example.edu/3",
            "https://b.example.edu/1",
        ]
        assert interleave_by_host(seeds) == [
            "https://a.example.edu/1",
            "https://b.example.edu/1",
            "https://a.example.edu/2",
            "https://a.example.edu/3",
        ]

    def test_relative_order_within_a_host_is_preserved(self):
        """Rotation is stable: a host's own seeds keep their input sequence."""
        seeds = [
            "https://a.example.edu/1",
            "https://b.example.edu/1",
            "https://a.example.edu/2",
            "https://a.example.edu/3",
        ]
        rotated = interleave_by_host(seeds)
        assert [s for s in rotated if "a.example.edu" in s] == [
            "https://a.example.edu/1",
            "https://a.example.edu/2",
            "https://a.example.edu/3",
        ]

    def test_rotation_keys_on_the_canonical_host(self):
        """Spelling variants of one host rotate as that host, not as three."""
        seeds = [
            "https://EXAMPLE.com/1",
            "https://example.com:443/2",
            "https://other.example.edu/1",
        ]
        assert interleave_by_host(seeds) == [
            "https://EXAMPLE.com/1",
            "https://other.example.edu/1",
            "https://example.com:443/2",
        ]

    def test_a_host_grouped_list_does_not_starve_a_later_host(self):
        """workers=2, per-host cap 1: host b starts while host a is still crawling.

        The barrier forces the overlap rather than hoping for it — without the
        rotation both pool threads are consumed by host ``a`` (one running, one
        blocked on its slot), host ``b`` never starts, and the barrier times out.
        """
        seeds = [
            "https://a.example.edu/1",
            "https://a.example.edu/2",
            "https://a.example.edu/3",
            "https://b.example.edu/1",
        ]
        two_hosts_live = threading.Barrier(2, timeout=5)
        tracker = _PeakTracker(hold_iters=0)

        def scrape_one(seed):
            with tracker.enter():
                if seed in ("https://a.example.edu/1", "https://b.example.edu/1"):
                    two_hosts_live.wait()
            return 1

        total = run_seeds(seeds, scrape_one, workers=2, per_host_workers=1)

        assert total == 4
        assert tracker.peak == 2

    def test_single_worker_preserves_exact_input_order(self):
        """workers=1 stays the sequential escape hatch — no rotation applied."""
        seeds = [
            "https://a.example.edu/1",
            "https://a.example.edu/2",
            "https://b.example.edu/1",
        ]
        calls = []

        def scrape_one(seed):
            calls.append(seed)
            return 1

        total = run_seeds(seeds, scrape_one, workers=1, per_host_workers=4)

        assert total == 3
        assert calls == seeds


class TestMalformedSeedIsolation:
    """A seed the URL parser rejects must fail alone, not abort the batch.

    ``urlsplit`` raises ``ValueError`` on a malformed authority such as
    ``http://[broken/path``. ``interleave_by_host`` keys *every* seed before any
    future is submitted, on the calling thread — outside the per-seed
    try/except — so an unguarded parse turns one bad list entry into a dead
    ingest.
    """

    MALFORMED = "http://[broken/path"

    def test_host_key_falls_back_to_the_raw_seed(self):
        assert host_key(self.MALFORMED) == self.MALFORMED

    def test_interleave_tolerates_a_malformed_seed(self):
        seeds = [
            "https://a.example.edu/1",
            self.MALFORMED,
            "https://a.example.edu/2",
        ]
        assert sorted(interleave_by_host(seeds)) == sorted(seeds)

    def test_malformed_seeds_contend_only_with_themselves(self):
        # Two distinct malformed seeds must not collapse into one shared slot.
        assert host_key(self.MALFORMED) != host_key("http://[also-broken/x")

    def test_one_malformed_seed_does_not_abort_the_batch(self):
        seeds = [
            "https://a.example.edu/1",
            self.MALFORMED,
            "https://b.example.edu/1",
        ]
        scraped = []
        lock = threading.Lock()

        def scrape_one(seed):
            with lock:
                scraped.append(seed)
            return 1

        total = run_seeds(seeds, scrape_one, workers=4, per_host_workers=2)

        assert total == 3
        assert sorted(scraped) == sorted(seeds)


class TestSharedLimiterAcrossBatches:
    """The per-host cap is a property of the process, not of one ``run_seeds`` call.

    ``ScraperManager`` is a single instance shared by the cron ingest thread and
    the uploader's ``/document_index/upload_url`` handler, so two batches can be
    in flight at once. A limiter built per call lets each batch spend the full
    per-host budget independently.
    """

    def test_overlapping_batches_contend_for_the_same_host_slot(self):
        entered_first = threading.Event()
        entered_second = threading.Event()
        release_first = threading.Event()
        seen = []
        lock = threading.Lock()

        def scrape_one(seed):
            with lock:
                is_first = not seen
                seen.append(seed)
            if is_first:
                entered_first.set()
                assert release_first.wait(10)
            else:
                entered_second.set()
            return 1

        results = {}

        def batch(name, seed):
            results[name] = run_seeds([seed], scrape_one, workers=2, per_host_workers=1)

        first = threading.Thread(target=batch, args=("a", "https://same.example/a"))
        first.start()
        assert entered_first.wait(10), "first batch never started its seed"

        second = threading.Thread(target=batch, args=("b", "https://same.example/b"))
        second.start()
        # With one shared slot for `same.example`, the second batch must wait.
        assert not entered_second.wait(
            0.5
        ), "second batch bypassed the per-host cap of the first"

        release_first.set()
        first.join(10)
        second.join(10)
        assert entered_second.is_set()
        assert results == {"a": 1, "b": 1}

    def test_same_cap_returns_the_same_limiter(self):
        assert shared_host_limiter(4) is shared_host_limiter(4)

    def test_reset_drops_the_registry(self):
        before = shared_host_limiter(4)
        reset_shared_host_limiters()
        assert shared_host_limiter(4) is not before

    def test_an_injected_limiter_overrides_the_shared_one(self):
        # The injected limiter's own cap is what binds, not ``per_host_workers``.
        injected = HostLimiter(1)
        tracker = _PeakTracker()

        def scrape_one(seed):
            with tracker.enter():
                return 1

        seeds = [f"https://x.example/{i}" for i in range(6)]
        total = run_seeds(
            seeds, scrape_one, workers=6, per_host_workers=8, limiter=injected
        )

        assert total == 6
        assert tracker.peak == 1


class TestRedirectedCrawlsFollowTheDestinationHost:
    """A crawl that redirects off its seed host must move to the destination's slot.

    ``requests`` follows redirects and ``LinkScraper`` resolves the rest of the
    crawl against the response's final URL, so a seed on ``a.example`` that lands
    on ``dest.example`` spends its whole life requesting ``dest.example`` while
    holding ``a.example``'s slot.
    """

    def test_rekey_frees_the_original_host_slot(self):
        limiter = HostLimiter(1)
        moved = threading.Event()
        release = threading.Event()

        def hold_then_move():
            with limiter.acquire("a.example"):
                limiter.rekey_current("dest.example")
                moved.set()
                assert release.wait(10)

        holder = threading.Thread(target=hold_then_move)
        holder.start()
        assert moved.wait(10)

        # `a.example` was handed back, so a second acquirer gets it immediately.
        got_original = threading.Event()

        def take_original():
            with limiter.acquire("a.example"):
                got_original.set()

        taker = threading.Thread(target=take_original)
        taker.start()
        assert got_original.wait(5), "rekey did not release the original host slot"

        release.set()
        holder.join(10)
        taker.join(10)

    def test_rekey_to_the_same_host_keeps_the_slot(self):
        limiter = HostLimiter(1)
        with limiter.acquire("a.example"):
            limiter.rekey_current("a.example")
            assert not limiter._semaphore_for("a.example").acquire(blocking=False)

    def test_rekey_without_a_held_slot_is_a_noop(self):
        limiter = HostLimiter(1)
        limiter.rekey_current("a.example")
        # Nothing was taken, so the slot is still free.
        assert limiter._semaphore_for("a.example").acquire(blocking=False)

    def test_two_seeds_redirecting_to_one_host_share_its_cap(self):
        limiter = HostLimiter(1)
        entered_first = threading.Event()
        entered_second = threading.Event()
        release_first = threading.Event()
        seen = []
        lock = threading.Lock()

        def scrape_one(seed):
            # Stands in for the crawl learning its response's final URL.
            limiter.rekey_current(host_key("https://dest.example/landing"))
            with lock:
                is_first = not seen
                seen.append(seed)
            if is_first:
                entered_first.set()
                assert release_first.wait(10)
            else:
                entered_second.set()
            return 1

        seeds = ["https://a.example/", "https://b.example/"]
        totals = []
        runner = threading.Thread(
            target=lambda: totals.append(
                run_seeds(
                    seeds, scrape_one, workers=4, per_host_workers=1, limiter=limiter
                )
            )
        )
        runner.start()
        assert entered_first.wait(10)
        assert not entered_second.wait(
            0.5
        ), "both crawls hit dest.example at once despite a per-host cap of 1"

        release_first.set()
        runner.join(10)
        assert totals == [2]
