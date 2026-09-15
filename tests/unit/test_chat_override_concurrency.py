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
        self._static_middleware = None
        self.default_provider = "default"
        self.default_model = "default"
        self.refresh_agent(force=True)

    def refresh_agent(self, force=False, **_kwargs):
        # Rebuild the compiled agent from whatever LLM is currently bound.
        self.agent = ("agent", self.agent_llm)

    def adopt_request_local_model(self, provider, model, context_window):
        # The view answers for the model it is about to call, and its cached
        # in-loop bound is cleared for refresh_agent to rebuild.
        self.default_provider = provider
        self.default_model = model
        self._static_middleware = None


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


def _make_wrapper(
    default_llm, llm_by_key, observations, observe_lock, archi_factory=None
):
    """Build a ``ChatWrapper`` wired to fakes, running the real override path.

    ``archi_factory`` (optional) receives ``(pipeline, observe)`` and returns the
    fake orchestrator to install, so a test can supply its own interleaving. When
    omitted the default barrier-driven :class:`_FakeArchi` is used.
    """
    wrapper = object.__new__(ChatWrapper)

    def _observe(conversation_id, llm):
        with observe_lock:
            observations.setdefault(conversation_id, []).append(llm)

    pipeline = _FakePipeline(default_llm)
    archi = (
        archi_factory(pipeline, _observe)
        if archi_factory is not None
        else _FakeArchi(pipeline, _observe)
    )
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
    # Returns (chat_model, context_window); None means the provider reports no
    # window for this model, which is the common case for self-hosted IDs.
    wrapper._create_provider_llm = lambda provider, model, api_key=None: (
        llm_by_key[(provider, model)],
        None,
    )
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


class _CoordinatedArchi:
    """Fake orchestrator driving an *asymmetric* A/B interleaving.

    Request A yields its first output and then *blocks* until request B has
    yielded its own first output; request B is only started once A is suspended.
    So B makes real progress while A's turn is still open — the pair is not
    serialized. A serialized implementation (e.g. a lock spanning the whole turn)
    could not produce B's output while A is parked, so it would time out here.

    Every step also records ``used.agent_llm`` via ``observe``, so the same run
    proves each turn keeps its *own* override LLM throughout the overlap. Against
    the swap/restore implementation A observes B's LLM on its second step (B has
    mutated the shared pipeline and not yet restored it), so this test fails until
    ``stream()`` builds a request-local pipeline view.
    """

    def __init__(self, pipeline, observe, record, role_of, a_first, b_first, a_second):
        self.pipeline = pipeline
        self.pipeline_name = "FakeReAct"
        self._observe = observe
        self._record = record
        self._role_of = role_of
        self._a_first = a_first
        self._b_first = b_first
        self._a_second = a_second

    def supports_stream(self, pipeline=None):
        return True

    def stream(self, history=None, conversation_id=None, pipeline=None):
        used = pipeline if pipeline is not None else self.pipeline
        role = self._role_of(conversation_id)

        self._observe(conversation_id, used.agent_llm)  # observation #1
        self._record(conversation_id, "first")
        yield _FakeOutput("partial-1")

        if role == "A":
            self._a_first.set()
            # Suspend A mid-turn until B has produced its first output. A serialized
            # impl never lets B run here, so this wait would time out.
            assert self._b_first.wait(
                timeout=10
            ), "B produced no output while A was suspended (serialized?)"
            self._observe(conversation_id, used.agent_llm)  # observation #2
            yield _FakeOutput("partial-2")
            self._a_second.set()
        else:  # role B — its turn only started after A was already suspended.
            self._b_first.set()
            # Keep B's turn open (do not restore) until A has taken its second
            # observation, so the swap impl still has the shared pipeline on B's
            # LLM when A observes it.
            assert self._a_second.wait(timeout=10), "A never took its second step"
            self._observe(conversation_id, used.agent_llm)  # observation #2
            yield _FakeOutput("partial-2")


class _RaisingArchi:
    """Fake orchestrator whose overridden turn raises mid-stream.

    It yields one output (so the override path has already built and installed a
    request-local view) and then raises, exercising ``stream()``'s except/finally
    cleanup. The shared pipeline must be left untouched: the request-local view
    (issue #86) never mutates ``self.archi.pipeline.agent_llm``, so there is
    nothing to unwind in ``finally`` — this guards that the removal of the old
    swap/restore machinery did not reintroduce shared-pipeline mutation.
    """

    def __init__(self, pipeline, observe):
        self.pipeline = pipeline
        self.pipeline_name = "FakeReAct"
        self._observe = observe

    def supports_stream(self, pipeline=None):
        return True

    def stream(self, history=None, conversation_id=None, pipeline=None):
        used = pipeline if pipeline is not None else self.pipeline
        self._observe(conversation_id, used.agent_llm)
        yield _FakeOutput("partial")
        raise RuntimeError("boom mid-stream")


def test_shared_pipeline_llm_unchanged_when_overridden_turn_raises():
    default_llm = _LLM("default", {"enable_thinking": False})
    llm_x = _LLM("X", {"temp": 0.1})
    llm_by_key = {("provX", "modX"): llm_x}

    observations: dict = {}
    observe_lock = threading.Lock()
    wrapper = _make_wrapper(
        default_llm,
        llm_by_key,
        observations,
        observe_lock,
        archi_factory=lambda pipeline, observe: _RaisingArchi(pipeline, observe),
    )

    shared_pipeline = wrapper.archi.pipeline
    assert shared_pipeline.agent_llm is default_llm

    errors: list = []
    # The mid-stream raise is caught by stream()'s except clause and surfaced as a
    # 500 event, so draining does not propagate the RuntimeError.
    outputs = list(
        wrapper.stream(
            message=["hi"],
            conversation_id=301,
            client_id="c",
            is_refresh=False,
            server_received_msg_ts=None,
            client_sent_msg_ts=0.0,
            client_timeout=0,
            config_name="default",
            provider="provX",
            model="modX",
        )
    )
    assert not errors

    # The turn actually ran on the override LLM (request-local view)...
    assert observations.get(301) == [llm_x]
    # ...it did raise mid-stream, surfaced as a 500 error event...
    assert any(o.get("type") == "error" and o.get("status") == 500 for o in outputs)
    # ...and the shared pipeline's LLM is the *identical* object it was before.
    assert shared_pipeline.agent_llm is default_llm


def test_overlapping_overrides_are_not_serialized():
    default_llm = _LLM("default", {"enable_thinking": False})
    llm_x = _LLM("X", {"temp": 0.1})
    llm_y = _LLM("Y", {"temp": 0.9})
    llm_by_key = {("provX", "modX"): llm_x, ("provY", "modY"): llm_y}

    observations: dict = {}
    observe_lock = threading.Lock()

    a_first = threading.Event()  # A produced its first output
    b_first = threading.Event()  # B produced its first output
    a_second = threading.Event()  # A took its second observation

    timeline: list = []
    timeline_lock = threading.Lock()

    def _record(conversation_id, phase):
        with timeline_lock:
            timeline.append((conversation_id, phase))

    def _role_of(conversation_id):
        return "A" if conversation_id == 201 else "B"

    def _factory(pipeline, observe):
        return _CoordinatedArchi(
            pipeline, observe, _record, _role_of, a_first, b_first, a_second
        )

    wrapper = _make_wrapper(
        default_llm, llm_by_key, observations, observe_lock, archi_factory=_factory
    )

    errors: list = []

    def _run(conversation_id, provider, model, start_gate):
        if start_gate is not None:
            assert start_gate.wait(timeout=10), "start gate never opened"
        try:
            _drain(wrapper, conversation_id, provider, model, errors)
        finally:
            _record(conversation_id, "complete")

    # A starts immediately; B only starts once A has produced its first output and
    # is suspended — so B's override applies strictly after A's, i.e. genuine
    # overlap rather than one turn running fully before the other.
    ta = threading.Thread(target=_run, args=(201, "provX", "modX", None))
    tb = threading.Thread(target=_run, args=(202, "provY", "modY", a_first))
    ta.start()
    tb.start()
    ta.join(timeout=30)
    tb.join(timeout=30)

    assert not errors, f"stream turn raised: {errors!r}"
    assert not ta.is_alive() and not tb.is_alive(), "a turn hung (serialized?)"

    # Not serialized: B produced its first output before A's turn completed.
    assert (201, "first") in timeline and (202, "first") in timeline
    assert (201, "complete") in timeline
    assert timeline.index((202, "first")) < timeline.index((201, "complete")), timeline

    # ...and the overlap did not cross the streams: each turn kept its own LLM.
    assert observations.get(201), "request A produced no observations"
    assert observations.get(202), "request B produced no observations"
    assert all(llm is llm_x for llm in observations[201]), observations[201]
    assert all(llm is llm_y for llm in observations[202]), observations[202]


def test_overridden_and_default_concurrent_keep_own_model():
    """An overridden turn and a default turn running concurrently keep their own LLM.

    This is the core guard for issue #86 in the mixed scenario: one request
    carries a provider/model override (→ request-local pipeline view), the other
    uses the shared pipeline's configured default LLM.  The barrier forces genuine
    overlap so both turns are in-flight simultaneously.  The overridden turn must
    observe only the override LLM; the default turn must observe only the default
    LLM — no cross-contamination in either direction.
    """
    default_llm = _LLM("default", {"enable_thinking": False})
    llm_x = _LLM("X", {"temp": 0.1})
    llm_by_key = {("provX", "modX"): llm_x}

    observations: dict = {}
    observe_lock = threading.Lock()
    wrapper = _make_wrapper(default_llm, llm_by_key, observations, observe_lock)

    wrapper.archi._barrier = threading.Barrier(2)
    errors: list = []

    # Thread A: overridden turn (provider + model specified → request-local view)
    ta = threading.Thread(target=_drain, args=(wrapper, 301, "provX", "modX", errors))
    # Thread B: default turn (no provider/model → shared pipeline's default LLM)
    tb = threading.Thread(target=_drain, args=(wrapper, 302, None, None, errors))
    ta.start()
    tb.start()
    ta.join(timeout=30)
    tb.join(timeout=30)

    assert not errors, f"stream turn raised: {errors!r}"
    assert not ta.is_alive() and not tb.is_alive(), "a turn hung"

    # Overridden request must see only the override LLM throughout the turn.
    assert observations.get(301), "overridden request produced no observations"
    assert all(llm is llm_x for llm in observations[301]), observations[301]

    # Default request must see only the default LLM throughout the turn.
    assert observations.get(302), "default request produced no observations"
    assert all(llm is default_llm for llm in observations[302]), observations[302]
    assert all(
        llm.extra_kwargs == {"enable_thinking": False} for llm in observations[302]
    )
