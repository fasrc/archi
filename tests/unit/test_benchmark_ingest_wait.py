"""Tests for wait_for_ingestion_completion stall-budget fix (issue #378).

The production bug: the timeout fired against total elapsed time rather than
time since the last successful status response.  A long-running ingest
reporting state=running was killed after one budget even though it was making
progress.

The fix (task 1.1): track last_progress_time separately; the stall clock only
fires when no successful response has been received within the budget window.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

import src.bin.service_benchmark as sb


class _SentinelError(Exception):
    """Raised by fakes to distinguish from TimeoutError."""


def _make_bench(monkeypatch, timeout="60", poll_interval="5"):
    monkeypatch.setenv("BENCH_INGEST_WAIT_TIMEOUT", timeout)
    monkeypatch.setenv("BENCH_INGEST_POLL_INTERVAL", poll_interval)
    bench = object.__new__(sb.Benchmarker)
    bench.config = {
        "services": {
            "data_manager": {
                "internal_port": 7871,
                "external_port": 7881,
            }
        }
    }
    return bench


class _FakeTime:
    def __init__(self):
        self._now = 0.0

    def monotonic(self):
        return self._now

    def sleep(self, n):
        self._now += n


class _ResponseCtx:
    """Context manager wrapping a JSON-serialisable payload dict."""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self._payload).encode()


def _make_request(handler):
    """Return a fake url_request module whose urlopen delegates to handler(url).

    handler(url) either returns a dict payload or raises.
    """

    class _FakeUrlRequest:
        def urlopen(self_, url, timeout=None):  # noqa: N805
            result = handler(url)
            return _ResponseCtx(result)

    return _FakeUrlRequest()


def test_healthy_ingest_not_killed_by_stall_budget(monkeypatch):
    """An ingest reporting state=running on every poll must not be killed by
    the stall budget — each successful response resets the progress clock.

    After 99 successful polls (495 simulated seconds, >8 budgets of 60s each)
    the 100th call raises the sentinel.  On the pre-fix code the method raises
    TimeoutError at iteration 13 (~60s) instead; the sentinel never fires.
    After the fix the sentinel propagates unchanged.
    """
    bench = _make_bench(monkeypatch)
    fake_time = _FakeTime()
    monkeypatch.setattr(sb, "time", fake_time)

    call_count = 0

    def handler(url):
        nonlocal call_count
        call_count += 1
        if call_count >= 100:
            raise _SentinelError("sentinel on call 100")
        return {"state": "running", "step": "Updating vectorstore"}

    monkeypatch.setattr(sb, "url_request", _make_request(handler))

    with pytest.raises(_SentinelError):
        bench.wait_for_ingestion_completion()


def test_url_error_on_all_urls_raises_timeout_within_two_budgets(monkeypatch):
    """All candidate URLs raising URLError must produce TimeoutError within ~one
    budget of the start, not linger forever.

    The stall clock starts at zero successful responses, so the first budget
    window expires after timeout_seconds without progress and the method raises.
    Two budgets (120s) is a generous upper bound.
    """
    bench = _make_bench(monkeypatch)
    fake_time = _FakeTime()
    monkeypatch.setattr(sb, "time", fake_time)

    def handler(url):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(sb, "url_request", _make_request(handler))

    with pytest.raises(TimeoutError):
        bench.wait_for_ingestion_completion()

    assert (
        fake_time.monotonic() < 120
    ), f"elapsed {fake_time.monotonic()}s should be under two budgets (120s)"


def test_error_state_raises_runtime_error_with_step_name(monkeypatch):
    """An error-state payload raises RuntimeError whose message contains the
    step name, and the opener is called at most four times (one per candidate
    URL) in the first round — a regression guard against swallowing the error
    back into the poll loop.
    """
    bench = _make_bench(monkeypatch)
    fake_time = _FakeTime()
    monkeypatch.setattr(sb, "time", fake_time)

    call_count = 0

    def handler(url):
        nonlocal call_count
        call_count += 1
        return {"state": "error", "step": "Embedding", "error": "cuda oom"}

    monkeypatch.setattr(sb, "url_request", _make_request(handler))

    with pytest.raises(RuntimeError, match="Embedding"):
        bench.wait_for_ingestion_completion()

    assert call_count <= 4, f"opener called {call_count} times; expected ≤4"


def test_completed_state_returns_none(monkeypatch):
    """An opener answering state=completed must return None and raise nothing."""
    bench = _make_bench(monkeypatch)
    fake_time = _FakeTime()
    monkeypatch.setattr(sb, "time", fake_time)

    def handler(url):
        return {"state": "completed"}

    monkeypatch.setattr(sb, "url_request", _make_request(handler))

    result = bench.wait_for_ingestion_completion()
    assert result is None


def test_stall_budget_reset_by_successful_polls(monkeypatch):
    """Successful polls push the stall deadline forward; TimeoutError fires only
    after a full budget of inactivity following the last success.

    Three rounds of state=running (each resetting last_progress_time to ~10s)
    are followed by URLError on every call.  On the current tree TimeoutError
    fires at ~70s (10s of progress + 60s stall); on pre-1.1 code it fires at
    ~60s (raw elapsed), so fake_time.monotonic() > 60 would fail there.
    This test passes on the current tree and serves as the regression guard
    for the stall-clock reset introduced in task 1.1.
    """
    bench = _make_bench(monkeypatch)
    fake_time = _FakeTime()
    monkeypatch.setattr(sb, "time", fake_time)

    call_count = 0

    def handler(url):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return {"state": "running", "step": "Embedding"}
        raise urllib.error.URLError("service gone")

    monkeypatch.setattr(sb, "url_request", _make_request(handler))

    with pytest.raises(TimeoutError):
        bench.wait_for_ingestion_completion()

    assert fake_time.monotonic() > 60, (
        f"clock at raise was {fake_time.monotonic()}s; should exceed one budget (60s) "
        "because successful polls reset last_progress_time"
    )


def test_unrecognised_payload_counts_as_progress(monkeypatch):
    """A payload with no 'state' key counts as a successful response and resets
    the stall clock — the method must not raise TimeoutError within a budget's
    worth of such responses.

    Budget=60s, poll=5s → 12 polls per budget.  We run 13 polls (one round more
    than a budget), all returning {"step": "warming up"} with no state key.
    The stall clock resets each round so TimeoutError never fires; the sentinel
    on the 14th call propagates unchanged.
    """
    bench = _make_bench(monkeypatch)
    fake_time = _FakeTime()
    monkeypatch.setattr(sb, "time", fake_time)

    rounds_per_budget = 60 // 5  # 12
    call_count = 0

    def handler(url):
        nonlocal call_count
        call_count += 1
        if call_count > rounds_per_budget + 1:
            raise _SentinelError("sentinel after one-budget-plus-one rounds")
        return {"step": "warming up"}

    monkeypatch.setattr(sb, "url_request", _make_request(handler))

    with pytest.raises(_SentinelError):
        bench.wait_for_ingestion_completion()
