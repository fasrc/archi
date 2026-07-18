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

from types import SimpleNamespace

import pytest

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


@pytest.mark.xfail(
    strict=True,
    reason="Request-local reported model (task 3.5) not wired yet: stream() still "
    "swaps self.current_model_used, so the shared attribute is mutated mid-turn "
    "instead of threading the override's model through _finalize_result",
)
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
