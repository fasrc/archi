"""Persistence regression test for request-time LLM overrides (issue #86, D3).

Design D3 requires the reported model to be *request-local*: an overridden turn
must persist the **override's** ``provider/model`` into the conversation row's
``model_used`` column, while the shared ``self.current_model_used`` attribute is
never mutated. Both halves matter together — a test that only asserted the shared
field is untouched would pass while shipping a silent mislabelling regression
(every overridden turn persisted with the *default* model, corrupting exactly the
A/B comparison rows the override feature exists to collect). So this test asserts
the *persisted* value equals the override.

Today ``stream()`` implements the reported model with a swap-and-restore on the
shared attribute: it sets ``self.current_model_used = f"{provider}/{model}"``
*before* finalization (``app.py`` ~2076-2077) and restores it in ``finally``
(~2595-2596). So at the moment the row is persisted the shared attribute reads
the override — which means the shared field IS mutated mid-turn. This test fails
against that implementation (the shared attribute is not ``default/default`` when
``_finalize_result`` runs) and passes once D3 threads the override model through
``_finalize_result`` / ``insert_conversation`` as a parameter instead of writing
the shared attribute.

The test bypasses ``ChatWrapper.__init__`` (``object.__new__``) and stubs the
collaborators the override path does not exercise, so it stays a unit test while
running the real override block and the real ``self._finalize_result(...)`` call
site. It captures what ``_finalize_result`` would persist by replicating
``insert_conversation``'s documented default (``model_used`` if supplied, else
``self.current_model_used``) — the exact fallback D3 specifies.
"""

import sys
from types import SimpleNamespace

import pytest

import src.interfaces.chat_app.app as app_module
from src.interfaces.chat_app.app import ChatWrapper


class _LLM:
    """A sentinel LLM standing in for a provider-built model."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):  # pragma: no cover - debugging aid only
        return f"_LLM({self.name!r})"


class _FakePipeline:
    """Minimal ReAct-shaped pipeline that survives ``copy.copy`` + reset."""

    def __init__(self, llm):
        self.agent_llm = llm
        self.agent = None
        self._active_tools = []
        self._active_middleware = []
        self._active_memory = None
        self._static_tools = None
        self.refresh_agent(force=True)

    def refresh_agent(self, force=False, **_kwargs):
        self.agent = ("agent", self.agent_llm)


class _FakeOutput:
    def __init__(self, answer):
        self.answer = answer
        self.metadata = {"event_type": "text"}

    def get(self, key, default=None):
        return default


class _FakeArchi:
    """Stand-in orchestrator whose ``stream`` yields a single text output."""

    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.pipeline_name = "FakeReAct"

    def supports_stream(self, pipeline=None):
        return True

    def stream(self, history=None, conversation_id=None, pipeline=None):
        yield _FakeOutput("partial")


def test_overridden_turn_persists_override_model_without_touching_shared():
    default_model = "default/default"
    default_llm = _LLM("default")
    override_llm = _LLM("X")

    wrapper = object.__new__(ChatWrapper)
    pipeline = _FakePipeline(default_llm)
    wrapper.archi = _FakeArchi(pipeline)
    wrapper.current_model_used = default_model
    wrapper.number_of_queries = 0
    wrapper.cursor = None
    wrapper.conn = None

    wrapper._init_timestamps = lambda: {}
    wrapper._resolve_config_name = lambda name: name or "default"
    wrapper.update_config = lambda config_name=None: None
    wrapper.create_agent_trace = lambda **kwargs: None
    wrapper.update_agent_trace = lambda **kwargs: None
    wrapper.insert_timing = lambda *a, **k: None
    wrapper._create_provider_llm = lambda provider, model, api_key=None: override_llm

    def _prepare(message, conversation_id, *a, **k):
        return (
            SimpleNamespace(conversation_id=conversation_id, history=[]),
            None,
        )

    wrapper._prepare_chat_context = _prepare

    captured = {}

    def _finalize(result, *, model_used=None, **kwargs):
        # Record the shared attribute at the moment of persistence, and the
        # value that WOULD be persisted — replicating insert_conversation's
        # documented default (D3): the passed model_used, else the shared field.
        captured["shared_at_finalize"] = wrapper.current_model_used
        captured["persisted"] = (
            model_used if model_used is not None else wrapper.current_model_used
        )
        return "out", [10, 11]

    wrapper._finalize_result = _finalize

    list(
        wrapper.stream(
            message=["hi"],
            conversation_id=101,
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

    # The row must be persisted with the OVERRIDE's provider/model.
    assert captured.get("persisted") == "provX/modX", captured

    # ...and the shared attribute must never be mutated to carry it: it stays the
    # configured default throughout finalization (D3's request-local invariant).
    assert captured.get("shared_at_finalize") == default_model, captured

    # After the turn the shared attribute is still the configured default.
    assert wrapper.current_model_used == default_model


def test_config_switch_without_override_reports_new_config_model():
    """Codex finding (PR #124): ``reported_model`` must be snapshotted AFTER
    ``update_config()``. A request that switches ``config_name`` without an
    override otherwise persists the PREVIOUS config's model into the row."""
    wrapper = object.__new__(ChatWrapper)
    wrapper.archi = _FakeArchi(_FakePipeline(_LLM("default")))
    wrapper.current_model_used = "old/model"
    wrapper.number_of_queries = 0
    wrapper.cursor = None
    wrapper.conn = None

    wrapper._init_timestamps = lambda: {}
    wrapper._resolve_config_name = lambda name: name or "default"

    def _update_config(config_name=None):
        # Switching config changes the active model on the shared attribute.
        wrapper.current_model_used = "new/model"

    wrapper.update_config = _update_config
    wrapper.create_agent_trace = lambda **kwargs: None
    wrapper.update_agent_trace = lambda **kwargs: None
    wrapper.insert_timing = lambda *a, **k: None
    wrapper._prepare_chat_context = lambda message, conversation_id, *a, **k: (
        SimpleNamespace(conversation_id=conversation_id, history=[]),
        None,
    )

    captured = {}

    def _finalize(result, *, model_used=None, **kwargs):
        captured["persisted"] = (
            model_used if model_used is not None else wrapper.current_model_used
        )
        return "out", [10, 11]

    wrapper._finalize_result = _finalize

    list(
        wrapper.stream(
            message=["hi"],
            conversation_id=202,
            client_id="c",
            is_refresh=False,
            server_received_msg_ts=None,
            client_sent_msg_ts=0.0,
            client_timeout=0,
            config_name="other",
            provider=None,
            model=None,
        )
    )

    # The row must carry the NEW config's model, not the stale pre-update snapshot.
    assert captured.get("persisted") == "new/model", captured


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def commit(self):
        pass

    def close(self):
        pass


def _make_insert_wrapper(monkeypatch, shared_model):
    """Build a ChatWrapper wired to run the REAL insert_conversation with a
    stubbed psycopg2, capturing the tuples handed to execute_values."""
    wrapper = object.__new__(ChatWrapper)
    wrapper.current_model_used = shared_model
    wrapper.current_pipeline_used = "PipeA"
    wrapper.pg_config = {}

    captured = {}

    def _fake_execute_values(cursor, sql, tups):
        captured["tups"] = tups

    monkeypatch.setattr(
        app_module.psycopg2, "connect", lambda **kw: _FakeConn([(1,), (2,)])
    )
    monkeypatch.setattr(
        app_module.psycopg2.extras, "execute_values", _fake_execute_values
    )
    return wrapper, captured


def test_insert_conversation_persists_supplied_override_model(monkeypatch):
    """The override path passes model_used, and every persisted row carries it —
    without touching the shared attribute (D3, real insert_conversation path)."""
    wrapper, captured = _make_insert_wrapper(monkeypatch, "default/default")

    ids = wrapper.insert_conversation(
        101,
        ("user", "hi", 0.0),
        ("archi", "hello", 1.0),
        "link",
        "ctx",
        is_refresh=False,
        model_used="provX/modX",
    )

    assert ids == [1, 2]
    # model_used is the 8th field (index 7) in each insert tuple.
    persisted_models = {tup[7] for tup in captured["tups"]}
    assert persisted_models == {"provX/modX"}
    # The shared attribute was never read for the row and stays untouched.
    assert wrapper.current_model_used == "default/default"


def test_insert_conversation_defaults_model_used_to_shared(monkeypatch):
    """With no model_used argument the non-override path persists the shared
    self.current_model_used unchanged (byte-for-byte default behaviour)."""
    wrapper, captured = _make_insert_wrapper(monkeypatch, "default/default")

    wrapper.insert_conversation(
        101,
        ("user", "hi", 0.0),
        ("archi", "hello", 1.0),
        "link",
        "ctx",
        is_refresh=True,
    )

    persisted_models = {tup[7] for tup in captured["tups"]}
    assert persisted_models == {"default/default"}


def test_finalize_result_threads_model_used_to_insert():
    """_finalize_result forwards its request-local model_used into
    insert_conversation rather than falling back to the shared attribute."""
    wrapper = object.__new__(ChatWrapper)
    wrapper.current_model_used = "default/default"
    wrapper.get_top_sources = lambda documents, scores: []
    wrapper.prepare_context_for_storage = lambda documents, scores: "ctx"
    wrapper.format_links_markdown = lambda top_sources: ""

    captured = {}

    def _fake_insert(
        conversation_id, user_msg, archi_msg, link, ctx, is_refresh, *, model_used=None
    ):
        captured["model_used"] = model_used
        return [7, 8]

    wrapper.insert_conversation = _fake_insert

    context = SimpleNamespace(
        conversation_id=5,
        sender="user",
        content="hi",
        is_refresh=False,
        history=[],
    )
    result = {"answer": "hello", "source_documents": [], "metadata": {}}

    output, ids = wrapper._finalize_result(
        result,
        context=context,
        server_received_msg_ts=0.0,
        timestamps={},
        render_markdown=False,
        model_used="provX/modX",
    )

    assert ids == [7, 8]
    assert captured["model_used"] == "provX/modX"


def _make_stream_wrapper(create_provider_llm):
    """Wire a ChatWrapper for driving the real ``stream()`` override block, with
    a pluggable ``_create_provider_llm`` so tests can exercise design D5's error
    contract. ``_finalize_result`` is stubbed; the fallback paths reach it via the
    shared pipeline, so the final event carries the request-local reported model."""
    default_llm = _LLM("default")
    wrapper = object.__new__(ChatWrapper)
    wrapper.archi = _FakeArchi(_FakePipeline(default_llm))
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
    wrapper._create_provider_llm = create_provider_llm
    wrapper._prepare_chat_context = lambda message, conversation_id, *a, **k: (
        SimpleNamespace(conversation_id=conversation_id, history=[]),
        None,
    )
    wrapper._finalize_result = lambda *a, model_used=None, **k: ("out", [1])
    return wrapper


def _drive_stream(wrapper):
    return list(
        wrapper.stream(
            message=["hi"],
            conversation_id=101,
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


def test_override_value_error_yields_http_400_and_stops():
    """A ValueError from provider construction is a hard 400 and terminates the
    turn (design D5): the client gets exactly one error event, no final."""

    def _raise(provider, model, api_key=None):
        raise ValueError("provider disabled")

    outputs = _drive_stream(_make_stream_wrapper(_raise))

    assert outputs == [{"type": "error", "status": 400, "message": "provider disabled"}]


def test_override_generic_error_warns_and_falls_back_to_default():
    """A non-ValueError during provider construction warns and falls back to the
    shared pipeline (design D5), so the turn still completes on the default model."""

    def _raise(provider, model, api_key=None):
        raise RuntimeError("boom")

    outputs = _drive_stream(_make_stream_wrapper(_raise))

    warnings = [o for o in outputs if o.get("type") == "warning"]
    finals = [o for o in outputs if o.get("type") == "final"]
    assert warnings and "Using default model" in warnings[0]["message"]
    # Fell back to the shared pipeline: the reported model stays the default.
    assert finals and finals[0]["model_used"] == "default/default"


def test_override_view_build_failure_warns_and_falls_back(monkeypatch):
    """A failure while *building the view* (copy/refresh_agent) mutates nothing
    shared, so it warns and falls back to the default without an unwind (D5)."""

    def _build_raises(pipeline, override_llm):
        raise RuntimeError("copy failed")

    monkeypatch.setattr(app_module, "_build_request_local_pipeline", _build_raises)

    wrapper = _make_stream_wrapper(lambda provider, model, api_key=None: _LLM("X"))
    outputs = _drive_stream(wrapper)

    warnings = [o for o in outputs if o.get("type") == "warning"]
    finals = [o for o in outputs if o.get("type") == "final"]
    assert warnings and "Using default model" in warnings[0]["message"]
    assert finals and finals[0]["model_used"] == "default/default"


def test_create_provider_llm_propagates_import_error(monkeypatch):
    """Direct test of the real `_create_provider_llm` body (design D3): forcing
    the lazy `from src.archi.providers import get_provider` (app.py:1623) to
    raise ImportError must propagate, not return None. This is the
    reproduction; test_override_import_error_warns_and_falls_back_to_default
    below is the observable-contract guard."""
    monkeypatch.setitem(sys.modules, "src.archi.providers", None)

    wrapper = object.__new__(ChatWrapper)
    wrapper.config = {}

    with pytest.raises(ImportError):
        wrapper._create_provider_llm("provX", "modX")


def test_override_import_error_warns_and_falls_back_to_default():
    """An ImportError during provider construction warns and falls back to the
    shared pipeline, exactly like any other construction failure (design D3).
    Unlike the direct test above, this substitutes `_create_provider_llm` with
    a raiser and so exercises only the caller's existing exception handling —
    it passes both before and after the task 3 fix."""

    def _raise(provider, model, api_key=None):
        raise ImportError("No module named 'anthropic'")

    outputs = _drive_stream(_make_stream_wrapper(_raise))

    warnings = [o for o in outputs if o.get("type") == "warning"]
    finals = [o for o in outputs if o.get("type") == "final"]
    assert warnings and "Using default model" in warnings[0]["message"]
    assert finals and finals[0]["model_used"] == "default/default"
