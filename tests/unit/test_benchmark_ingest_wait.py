"""Unit tests for `Benchmarker.wait_for_ingestion_completion` (issue #378).

The benchmark harness blocks on the data-manager's `/api/ingestion/status`
endpoint before it scores anything. Two defects made that wait hostile:

1. the deadline was absolute, so an ingest that was demonstrably advancing was
   killed at exactly `BENCH_INGEST_WAIT_TIMEOUT` seconds (observed 2026-08-27:
   1433 consecutive successful polls, all `state=running`, then `TimeoutError`
   at 7200s -- and the ingest finished two minutes later);
2. `last_error` was never cleared when a later candidate URL succeeded, so the
   timeout quoted a connection failure from a URL the loop had already fallen
   past, sending the operator hunting a network fault that did not exist.

These tests drive the real function through injected `fetch`/`clock`/`sleep`
seams: no live data-manager, no patching of the shared `time` module, and no
test that sleeps for real.
"""

from __future__ import annotations

import io
import json
from urllib import error as url_error

import pytest

from src.bin import service_benchmark
from src.bin.service_benchmark import Benchmarker

DM_INTERNAL = "http://data-manager:7871/api/ingestion/status"
LOCAL_INTERNAL = "http://localhost:7871/api/ingestion/status"


class FakeClock:
    """A monotonic clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _bench():
    """A Benchmarker with just enough state for the ingest wait.

    `object.__new__` skips `__init__` (which would build a live chain); the wait
    only reads `self.config`. Same pattern as test_benchmark_ragas_dialect.py.
    """
    bench = object.__new__(Benchmarker)
    bench.config = {
        "services": {"data_manager": {"internal_port": 7871, "external_port": 7881}}
    }
    return bench


def _budget_env(monkeypatch, *, stall="30", max_wait="0", poll="5"):
    monkeypatch.setenv("BENCH_INGEST_WAIT_TIMEOUT", stall)
    monkeypatch.setenv("BENCH_INGEST_MAX_WAIT", max_wait)
    monkeypatch.setenv("BENCH_INGEST_POLL_INTERVAL", poll)


def _running(step="Updating vectorstore"):
    return {"state": "running", "step": step, "error": None}


def _scripted(responses):
    """A fetch that walks `responses`, sticking on the last entry forever.

    Each entry is either a payload dict (returned for any URL) or a callable
    taking the URL, so a test can make one candidate URL fail and another
    succeed.
    """
    calls = []

    def fetch(url):
        calls.append(url)
        entry = responses[min(len(calls) - 1, len(responses) - 1)]
        if callable(entry):
            return entry(url)
        return entry

    fetch.calls = calls
    return fetch


def test_healthy_running_ingest_outlives_the_stall_budget(monkeypatch):
    """Defect 1: polls that keep succeeding must not be killed by the clock."""
    _budget_env(monkeypatch, stall="30", max_wait="0", poll="5")
    clock = FakeClock()
    # 20 healthy polls == 100 simulated seconds, well past the 30s budget.
    fetch = _scripted([_running()] * 20 + [{"state": "completed", "step": "done"}])

    _bench().wait_for_ingestion_completion(fetch=fetch, clock=clock, sleep=clock.sleep)

    assert clock.now - 1000.0 > 30, "the fake clock must have passed the budget"
    assert len(fetch.calls) == 21


def test_endpoint_that_never_answers_times_out_within_the_budget(monkeypatch):
    """Fast-fail is preserved: nothing reachable still ends inside the budget."""
    _budget_env(monkeypatch, stall="30", max_wait="0", poll="5")
    clock = FakeClock()

    def fetch(url):
        raise url_error.URLError("[Errno 111] Connection refused")

    with pytest.raises(TimeoutError) as excinfo:
        _bench().wait_for_ingestion_completion(
            fetch=fetch, clock=clock, sleep=clock.sleep
        )

    assert clock.now - 1000.0 <= 35, "must not run far past the stall budget"
    message = str(excinfo.value)
    assert "Connection refused" in message
    assert "ever answered" in message


def test_endpoint_that_goes_silent_after_progress_times_out_on_stall(monkeypatch):
    """The stall clock starts at the last good poll, not at the first."""
    _budget_env(monkeypatch, stall="30", max_wait="0", poll="5")
    clock = FakeClock()
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        if calls["n"] <= 3:
            return _running("Splitting documents")
        raise url_error.URLError("[Errno 111] Connection refused")

    with pytest.raises(TimeoutError) as excinfo:
        _bench().wait_for_ingestion_completion(
            fetch=fetch, clock=clock, sleep=clock.sleep
        )

    # Three good polls (10s of sleeps) then 30s of silence -- so the total is
    # past the budget, which an absolute deadline would have caught earlier.
    assert clock.now - 1000.0 > 30
    message = str(excinfo.value)
    assert "Splitting documents" in message
    assert "state=running" in message


def test_state_error_raises_runtime_error_naming_the_step(monkeypatch):
    """A real ingest failure must still fail loudly and immediately."""
    _budget_env(monkeypatch)
    clock = FakeClock()
    fetch = _scripted(
        [{"state": "error", "step": "Updating vectorstore", "error": "CUDA OOM"}]
    )

    with pytest.raises(RuntimeError) as excinfo:
        _bench().wait_for_ingestion_completion(
            fetch=fetch, clock=clock, sleep=clock.sleep
        )

    message = str(excinfo.value)
    assert "Updating vectorstore" in message
    assert "CUDA OOM" in message
    assert not isinstance(excinfo.value, TimeoutError)


def test_timeout_message_does_not_quote_a_fallen_through_url(monkeypatch):
    """Defect 2: an error from a candidate URL the loop fell past is not news."""
    # Stall never trips (every poll succeeds); the absolute ceiling ends it.
    _budget_env(monkeypatch, stall="3600", max_wait="60", poll="5")
    clock = FakeClock()

    def fetch(url):
        if "data-manager" in url:
            raise url_error.URLError("[Errno 111] Connection refused")
        return _running()

    with pytest.raises(TimeoutError) as excinfo:
        _bench().wait_for_ingestion_completion(
            fetch=fetch, clock=clock, sleep=clock.sleep
        )

    message = str(excinfo.value)
    assert "Connection refused" not in message
    # The unreachable candidate must not be named at all -- "data-manager" on
    # its own appears legitimately in the phrase "data-manager ingestion".
    assert DM_INTERNAL not in message
    assert "data-manager:7871" not in message
    assert LOCAL_INTERNAL in message
    assert "state=running" in message
    assert "Updating vectorstore" in message


def test_absolute_ceiling_stops_an_ingest_that_never_completes(monkeypatch):
    """ "Alive but stuck" still ends -- BENCH_INGEST_MAX_WAIT is the backstop."""
    _budget_env(monkeypatch, stall="3600", max_wait="100", poll="5")
    clock = FakeClock()
    fetch = _scripted([_running()])

    with pytest.raises(TimeoutError) as excinfo:
        _bench().wait_for_ingestion_completion(
            fetch=fetch, clock=clock, sleep=clock.sleep
        )

    assert clock.now - 1000.0 >= 100
    message = str(excinfo.value)
    assert "BENCH_INGEST_MAX_WAIT" in message
    assert "state=running" in message


def test_pending_forever_still_trips_the_stall_budget(monkeypatch):
    """An endpoint that answers but never starts is not "progress".

    `ingestion_status.py:29-33` initializes the endpoint to `state="pending"`
    and only the ingestion thread advances it. If that thread dies before its
    first line, the endpoint answers `pending` forever -- and treating "the URL
    replied" as progress would hold the benchmark until the absolute ceiling,
    or forever with `BENCH_INGEST_MAX_WAIT=0`.
    """
    # Ceiling is far away, so only the stall budget can end this.
    _budget_env(monkeypatch, stall="30", max_wait="600", poll="5")
    clock = FakeClock()
    fetch = _scripted([{"state": "pending", "step": None, "error": None}])

    with pytest.raises(TimeoutError) as excinfo:
        _bench().wait_for_ingestion_completion(
            fetch=fetch, clock=clock, sleep=clock.sleep
        )

    assert clock.now - 1000.0 <= 35, "the stall budget must end this, not the ceiling"
    message = str(excinfo.value)
    assert "BENCH_INGEST_WAIT_TIMEOUT" in message
    assert "state=pending" in message


def test_queued_behind_the_ingestion_lock_still_trips_the_stall_budget(monkeypatch):
    """`running` + `initializing` means the ingest has NOT started yet.

    `ingestion_status.py:46-48` publishes `state=running step=initializing`
    *before* acquiring `ingestion_lock`, and other paths hold that same lock
    without touching this status dict (`service_data_manager.py:70-83`,
    `run_locked` / `trigger_update`). So a benchmark queued behind a scheduled
    task sees `running` forever while nothing of its own is happening -- the
    one `running` payload that must not restart the stall budget.
    """
    _budget_env(monkeypatch, stall="30", max_wait="600", poll="5")
    clock = FakeClock()
    fetch = _scripted([_running("initializing")])

    with pytest.raises(TimeoutError) as excinfo:
        _bench().wait_for_ingestion_completion(
            fetch=fetch, clock=clock, sleep=clock.sleep
        )

    assert clock.now - 1000.0 <= 35, "the stall budget must end this, not the ceiling"
    assert "step=initializing" in str(excinfo.value)


def test_a_step_past_initializing_is_progress(monkeypatch):
    """Every step after `initializing` is emitted from inside the lock."""
    _budget_env(monkeypatch, stall="30", max_wait="600", poll="5")
    clock = FakeClock()
    # 3 polls queued, then work starts and stays on one step for a long time.
    fetch = _scripted(
        [_running("initializing")] * 3
        + [_running("Fetching ticket data onto filesystem")] * 20
        + [{"state": "completed", "step": "done"}]
    )

    _bench().wait_for_ingestion_completion(fetch=fetch, clock=clock, sleep=clock.sleep)

    assert clock.now - 1000.0 > 30, "the post-lock steps must have reset the budget"


def test_unknown_state_does_not_reset_the_stall_budget(monkeypatch):
    """A state the harness does not recognize is not evidence of progress."""
    _budget_env(monkeypatch, stall="30", max_wait="600", poll="5")
    clock = FakeClock()
    fetch = _scripted([{"state": "quiescing", "step": "who knows", "error": None}])

    with pytest.raises(TimeoutError) as excinfo:
        _bench().wait_for_ingestion_completion(
            fetch=fetch, clock=clock, sleep=clock.sleep
        )

    assert clock.now - 1000.0 <= 35
    assert "state=quiescing" in str(excinfo.value)


def test_disabling_the_ceiling_is_warned_about_loudly(monkeypatch, caplog):
    """`BENCH_INGEST_MAX_WAIT=0` is an escape hatch, not a default worth hiding.

    With the ceiling off, an ingest wedged inside `update_vectorstore()` keeps
    answering `state=running` and nothing bounds the wait. That is a deliberate
    operator choice for a corpus larger than six hours -- but an unattended run
    that hangs silently burns an allocation, so say so at the top of the wait.
    """
    _budget_env(monkeypatch, stall="30", max_wait="0", poll="5")
    clock = FakeClock()
    fetch = _scripted([_running(), {"state": "completed", "step": "done"}])

    with caplog.at_level("WARNING", logger="src.bin.service_benchmark"):
        _bench().wait_for_ingestion_completion(
            fetch=fetch, clock=clock, sleep=clock.sleep
        )

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("BENCH_INGEST_MAX_WAIT" in w for w in warnings), warnings


def test_an_enabled_ceiling_is_not_warned_about(monkeypatch, caplog):
    _budget_env(monkeypatch, stall="30", max_wait="600", poll="5")
    clock = FakeClock()
    fetch = _scripted([{"state": "completed", "step": "done"}])

    with caplog.at_level("WARNING", logger="src.bin.service_benchmark"):
        _bench().wait_for_ingestion_completion(
            fetch=fetch, clock=clock, sleep=clock.sleep
        )

    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == []


def test_budgets_come_from_the_environment(monkeypatch):
    for name in (
        "BENCH_INGEST_WAIT_TIMEOUT",
        "BENCH_INGEST_MAX_WAIT",
        "BENCH_INGEST_POLL_INTERVAL",
    ):
        monkeypatch.delenv(name, raising=False)

    defaults = service_benchmark._ingest_wait_budgets()
    assert defaults.stall_seconds == 7200
    assert defaults.max_wait_seconds == 21600
    assert defaults.poll_interval_seconds == 5

    _budget_env(monkeypatch, stall="45", max_wait="900", poll="2")
    tuned = service_benchmark._ingest_wait_budgets()
    assert tuned.stall_seconds == 45
    assert tuned.max_wait_seconds == 900
    assert tuned.poll_interval_seconds == 2

    monkeypatch.setenv("BENCH_INGEST_MAX_WAIT", "0")
    assert service_benchmark._ingest_wait_budgets().max_wait_seconds == 0


def test_default_fetch_parses_the_status_payload(monkeypatch):
    """The real fetch decodes the endpoint's JSON body into a dict."""
    captured = {}

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    def fake_urlopen(url, timeout=None):
        captured["url"] = url
        captured["timeout"] = timeout
        return _Response(
            json.dumps({"state": "running", "step": "Flushing indices"}).encode("utf-8")
        )

    monkeypatch.setattr(service_benchmark.url_request, "urlopen", fake_urlopen)

    payload = service_benchmark._fetch_ingestion_status(LOCAL_INTERNAL)

    assert payload == {"state": "running", "step": "Flushing indices"}
    assert captured["url"] == LOCAL_INTERNAL
    assert captured["timeout"] == 5
