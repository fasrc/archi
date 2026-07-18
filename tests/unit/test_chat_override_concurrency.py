"""Concurrency regression tests for request-time LLM overrides (issue #86).

The core acceptance criterion for the fix: two overlapping overridden
``ChatWrapper.stream()`` turns must each observe their *own* override LLM for the
whole turn — regardless of interleaving — and a request that starts after both
complete must observe the configured default LLM (and its ``extra_kwargs``),
with no residue left on the shared pipeline.

Today the override path swaps ``self.archi.pipeline.agent_llm`` on the *shared*
pipeline for the duration of the turn (``app.py`` ~2039-2066) and restores it in
``finally``. Because the pipeline is shared across requests, two overlapping
overrides read the same mutated attribute, so one turn observes the other's LLM;
and the interleaved restore can leave the shared pipeline pinned to an override.
This test drives that interleaving deterministically with a ``threading.Barrier``
(no sleep-based timing) and therefore fails against the swap/restore
implementation and passes once ``stream()`` builds a request-local pipeline view.

The test bypasses ``ChatWrapper.__init__`` (``object.__new__``) and stubs the
collaborators the override path does not exercise, so it stays a unit test while
running the real override block, the real ``self.archi.stream(...)`` call site,
and the real ``finally`` cleanup.
"""

import threading
from types import SimpleNamespace

import pytest

from src.interfaces.chat_app.app import ChatWrapper


class _LLM:
    """A sentinel LLM carrying ``extra_kwargs`` so identity == config identity."""

    def __init__(self, name, extra_kwargs):
        self.name = name
        self.extra_kwargs = extra_kwargs

    def __repr__(self):  # pragma: no cover - debugging aid only
        return f"_LLM({self.name!r})"


class _FakePipeline:
    """Minimal ReAct-shaped pipeline: an ``agent_llm`` plus a rebuildable agent.

    Supports both the current swap path (``refresh_agent`` after rebinding
    ``agent_llm``) and the future request-local view path (``copy.copy`` + the
    attribute resets performed by ``_build_request_local_pipeline``).
    """

    def __init__(self, llm):
        self.agent_llm = llm
        self.agent = None
        self._active_tools = []
        self._active_middleware = []
        self._active_memory = None
        self._static_tools = None
        self.refresh_agent(force=True)

    def refresh_agent(self, force=False, **_kwargs):
        # Rebuild the compiled agent from whatever LLM is currently bound.
        self.agent = ("agent", self.agent_llm)


class _FakeArchi:
    """Stand-in orchestrator whose ``stream`` records the LLM actually in use.

    ``_barrier`` (when set) rendezvouses two concurrent turns so both have
    applied their override before either observes the LLM — the interleaving the
    swap bug needs to surface. ``observe`` receives ``(conversation_id, llm)``.
    """

    def __init__(self, pipeline, observe):
        self.pipeline = pipeline
        self.pipeline_name = "FakeReAct"
        self._barrier = None
        self._observe = observe

    def supports_stream(self, pipeline=None):
        return True

    def stream(self, history=None, conversation_id=None, pipeline=None):
        used = pipeline if pipeline is not None else self.pipeline
        # Observe twice across the turn; the barrier guarantees both concurrent
        # turns have installed their override before either observation.
        for _ in range(2):
            if self._barrier is not None:
                self._barrier.wait(timeout=10)
            self._observe(conversation_id, used.agent_llm)
            yield _FakeOutput("partial")


class _FakeOutput:
    def __init__(self, answer):
        self.answer = answer
        self.metadata = {"event_type": "text"}

    def get(self, key, default=None):
        return default


def _make_wrapper(default_llm, llm_by_key, observations, observe_lock):
    """Build a ``ChatWrapper`` wired to fakes, running the real override path."""
    wrapper = object.__new__(ChatWrapper)

    def _observe(conversation_id, llm):
        with observe_lock:
            observations.setdefault(conversation_id, []).append(llm)

    archi = _FakeArchi(_FakePipeline(default_llm), _observe)
    wrapper.archi = archi
    wrapper.current_model_used = "default/default"
    wrapper.number_of_queries = 0
    wrapper.cursor = None
    wrapper.conn = None

    wrapper._init_timestamps = lambda: {}
    wrapper._resolve_config_name = lambda name: name or "default"
    wrapper.update_config = lambda config_name=None: None
    wrapper.create_agent_trace = lambda **kwargs: None
    wrapper.update_agent_trace = lambda **kwargs: None
    wrapper.insert_timing = lambda *a, **k: None
    wrapper._finalize_result = lambda *a, **k: ("out", [])

    def _prepare(message, conversation_id, *a, **k):
        return (
            SimpleNamespace(conversation_id=conversation_id, history=[]),
            None,
        )

    wrapper._prepare_chat_context = _prepare
    wrapper._create_provider_llm = lambda provider, model, api_key=None: llm_by_key[
        (provider, model)
    ]
    return wrapper


def _drain(wrapper, conversation_id, provider, model, errors):
    try:
        list(
            wrapper.stream(
                message=["hi"],
                conversation_id=conversation_id,
                client_id="c",
                is_refresh=False,
                server_received_msg_ts=None,
                client_sent_msg_ts=0.0,
                client_timeout=0,
                config_name="default",
                provider=provider,
                model=model,
            )
        )
    except BaseException as exc:  # surface thread failures to the test body
        errors.append(exc)


@pytest.mark.xfail(
    strict=True,
    reason="Request-local view (task 3.3) not wired yet: stream() still swaps the "
    "shared pipeline's agent_llm, so overlapping overrides read each other's LLM",
)
def test_overlapping_overrides_keep_own_model_and_leave_no_residue():
    default_llm = _LLM("default", {"enable_thinking": False})
    llm_x = _LLM("X", {"temp": 0.1})
    llm_y = _LLM("Y", {"temp": 0.9})
    llm_by_key = {("provX", "modX"): llm_x, ("provY", "modY"): llm_y}

    observations: dict = {}
    observe_lock = threading.Lock()
    wrapper = _make_wrapper(default_llm, llm_by_key, observations, observe_lock)

    # --- Phase 1: two overlapping overridden turns, deterministically interleaved.
    wrapper.archi._barrier = threading.Barrier(2)
    errors: list = []
    ta = threading.Thread(target=_drain, args=(wrapper, 101, "provX", "modX", errors))
    tb = threading.Thread(target=_drain, args=(wrapper, 102, "provY", "modY", errors))
    ta.start()
    tb.start()
    ta.join(timeout=30)
    tb.join(timeout=30)
    assert not errors, f"stream turn raised: {errors!r}"
    assert not ta.is_alive() and not tb.is_alive()

    # Each turn must observe ONLY its own override LLM, for the whole turn.
    assert observations.get(101), "request A produced no observations"
    assert observations.get(102), "request B produced no observations"
    assert all(llm is llm_x for llm in observations[101]), observations[101]
    assert all(llm is llm_y for llm in observations[102]), observations[102]

    # --- Phase 2: a request started after both complete sees the default LLM.
    wrapper.archi._barrier = None
    _drain(wrapper, 103, None, None, errors)
    assert not errors, f"default turn raised: {errors!r}"
    assert observations.get(103), "default request produced no observations"
    assert all(llm is default_llm for llm in observations[103]), observations[103]
    # ...including the default's extra_kwargs — no override residue.
    assert all(
        llm.extra_kwargs == {"enable_thinking": False} for llm in observations[103]
    )
