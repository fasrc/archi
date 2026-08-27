"""Tool calling on the HUIT Bedrock proxy.

The provider is the only hand-rolled chat model in the set, so it gets none of the
tool plumbing a vendor model brings. These tests pin the plumbing this change adds,
and — just as importantly — pin that callers binding no tools are unaffected: this
provider is on the live agent path and carries the RAGAS benchmark's judge traffic.

Every test fakes the transport. The proxy's tool support was verified against the
live endpoint during design; the suite stays offline.
"""

import json

import pytest

from src.archi.providers import huit_bedrock_provider
from src.archi.providers.huit_bedrock_provider import HuitBedrockChat

PROBE_SCHEMA = {
    "title": "Probe",
    "description": "Record a probe result.",
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "why": {"type": "string"}},
    "required": ["ok", "why"],
}


class _Response:
    """Stand-in for a `requests` response carrying an Anthropic content list."""

    status_code = 200

    def __init__(self, content_blocks):
        self._payload = {
            "content": content_blocks,
            "usage": {"input_tokens": 3, "output_tokens": 5},
        }

    def json(self):
        return self._payload


def _capture(monkeypatch, content_blocks=None):
    """Patch the provider's `requests.post`; return a dict that collects the body."""
    seen = {}

    def fake_post(url, headers=None, data=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = json.loads(data or "{}")
        seen["timeout"] = timeout
        return _Response(
            content_blocks
            if content_blocks is not None
            else [{"type": "text", "text": "hello"}]
        )

    monkeypatch.setattr(huit_bedrock_provider.requests, "post", fake_post)
    return seen


def _model():
    return HuitBedrockChat(model_id="test-model", api_key="test-key")


def test_with_structured_output_is_available():
    """langchain-core refuses structured output unless `bind_tools` is overridden.

    Its default `with_structured_output` compares `type(self).bind_tools` against
    `BaseChatModel.bind_tools` and raises `NotImplementedError` when they match, so
    this asserts the override exists on the class — the whole reason the QA
    evaluation console could not use this provider as its judge.
    """
    runnable = _model().with_structured_output(PROBE_SCHEMA)
    assert runnable is not None


def test_bound_tools_reach_the_body_in_anthropic_shape(monkeypatch):
    seen = _capture(monkeypatch)

    _model().bind_tools([PROBE_SCHEMA]).invoke([("human", "go")])

    tools = seen["body"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "Probe"
    assert tools[0]["description"] == "Record a probe result."
    # Anthropic names the parameter schema `input_schema`; OpenAI calls it
    # `parameters`. Sending the OpenAI spelling silently drops the schema.
    assert tools[0]["input_schema"]["properties"]["ok"] == {"type": "boolean"}
    assert "parameters" not in tools[0]


def test_a_pydantic_tool_converts_the_same_way(monkeypatch):
    """Proves the conversion delegates to `convert_to_openai_tool`.

    A dict schema could be mapped by hand; a Pydantic class could not, so this is
    what shows every langchain tool form is handled rather than just the easy one.
    """
    pydantic = pytest.importorskip("pydantic")

    class Probe(pydantic.BaseModel):
        """Record a probe result."""

        ok: bool
        why: str

    seen = _capture(monkeypatch)

    _model().bind_tools([Probe]).invoke([("human", "go")])

    tool = seen["body"]["tools"][0]
    assert tool["name"] == "Probe"
    assert set(tool["input_schema"]["properties"]) == {"ok", "why"}


@pytest.mark.parametrize(
    "tool_choice, expected",
    [
        ("any", {"type": "any"}),
        ("auto", {"type": "auto"}),
        ("Probe", {"type": "tool", "name": "Probe"}),
        ({"type": "tool", "name": "Probe"}, {"type": "tool", "name": "Probe"}),
    ],
)
def test_tool_choice_is_encoded_for_anthropic(monkeypatch, tool_choice, expected):
    seen = _capture(monkeypatch)

    _model().bind_tools([PROBE_SCHEMA], tool_choice=tool_choice).invoke(
        [("human", "go")]
    )

    assert seen["body"]["tool_choice"] == expected


def test_tool_choice_is_absent_when_not_requested(monkeypatch):
    seen = _capture(monkeypatch)

    _model().bind_tools([PROBE_SCHEMA]).invoke([("human", "go")])

    assert "tool_choice" not in seen["body"]


def test_bind_tools_tolerates_langchains_extra_keyword(monkeypatch):
    """The exact call langchain-core's `with_structured_output` makes.

    It always passes `ls_structured_output_format` alongside `tool_choice="any"`,
    so a signature that rejected an unknown keyword would break structured output
    for every caller.
    """
    _capture(monkeypatch)

    bound = _model().bind_tools(
        [PROBE_SCHEMA],
        tool_choice="any",
        ls_structured_output_format={
            "kwargs": {"method": "function_calling"},
            "schema": PROBE_SCHEMA,
        },
    )

    assert bound.invoke([("human", "go")]) is not None


def test_a_tool_use_block_becomes_a_tool_call(monkeypatch):
    """`tool_calls` is where langchain reads structured output from.

    `JsonOutputKeyToolsParser` matches on the tool name in `AIMessage.tool_calls`;
    a response parsed into text alone yields nothing no matter what came back.
    """
    _capture(
        monkeypatch,
        [
            {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "Probe",
                "input": {"ok": True, "why": "probe"},
            }
        ],
    )

    message = _model().bind_tools([PROBE_SCHEMA]).invoke([("human", "go")])

    assert len(message.tool_calls) == 1
    call = message.tool_calls[0]
    assert call["name"] == "Probe"
    assert call["args"] == {"ok": True, "why": "probe"}
    assert call["id"] == "toolu_01"


def test_text_and_a_tool_call_both_survive(monkeypatch):
    _capture(
        monkeypatch,
        [
            {"type": "text", "text": "Let me record that."},
            {
                "type": "tool_use",
                "id": "toolu_02",
                "name": "Probe",
                "input": {"ok": False, "why": "mixed"},
            },
        ],
    )

    message = _model().bind_tools([PROBE_SCHEMA]).invoke([("human", "go")])

    assert message.content == "Let me record that."
    assert message.tool_calls[0]["args"] == {"ok": False, "why": "mixed"}


def test_callers_that_bind_no_tools_are_unaffected(monkeypatch):
    """Tool support must be inert for the agent path and the RAGAS judge."""
    seen = _capture(monkeypatch)

    message = _model().invoke([("human", "go")])

    assert "tools" not in seen["body"]
    assert "tool_choice" not in seen["body"]
    assert set(seen["body"]) == {
        "anthropic_version",
        "max_tokens",
        "temperature",
        "messages",
    }
    assert message.content == "hello"
    assert message.tool_calls == []


def test_structured_output_round_trip(monkeypatch):
    """Mirrors `src/evaluation/qa/runtime.py:223` — the call this change unblocks.

    The QA evaluation console scores every gold atom through
    `model.with_structured_output(schema)`, so this is the path that decides
    whether the HUIT Bedrock judge is usable at all.
    """
    seen = _capture(
        monkeypatch,
        [
            {
                "type": "tool_use",
                "id": "toolu_03",
                "name": "Probe",
                "input": {"ok": True, "why": "round trip"},
            }
        ],
    )

    result = _model().with_structured_output(PROBE_SCHEMA).invoke([("human", "go")])

    assert result == {"ok": True, "why": "round trip"}
    # langchain's default forces a tool; the request must say so.
    assert seen["body"]["tool_choice"] == {"type": "any"}


def test_the_model_catalog_does_not_over_promise():
    """`supports_tools` stays False, and that is deliberate.

    The obvious move after adding tool support is to flip this flag. It would be
    wrong. Nothing reads it to decide whether to bind — it is advertisement,
    serialized out of the provider API (`src/interfaces/chat_app/app.py:3954`)
    into the chat app's model picker. An operator reading it there is choosing a
    model *for the agent*, and the agent drives multi-turn tool loops: call a
    tool, feed the result back, continue. That still does not work, because
    `_convert_messages` renders an assistant turn as `str(msg.content)` and drops
    its `tool_use` blocks, so the following `tool_result` names a `tool_use_id`
    the proxy cannot match.

    What this module tests is single-shot bound-tool invocation, which is what
    structured output needs. The flag flips in the follow-up that fixes tool-call
    history serialization, not here.
    """
    for model in huit_bedrock_provider.DEFAULT_HUIT_BEDROCK_MODELS:
        assert model.supports_tools is False, model.id


def test_a_profile_timeout_reaches_the_transport():
    """An evaluator profile's timeout arrives as `timeout`, not `request_timeout`.

    `ModelDescriptor.provider_kwargs` (`src/evaluation/qa/profile.py`) passes it
    under that name, and the keyword whitelist in `get_chat_model` used to drop
    it — so a profile asking for a long judge call silently kept the 120s default
    while RAGAS judges the same model at 300s.
    """
    provider = huit_bedrock_provider.HuitBedrockProvider()
    model = provider.get_chat_model("test-model", timeout=300)
    assert model.request_timeout == 300


def test_an_explicit_request_timeout_wins_over_the_alias():
    provider = huit_bedrock_provider.HuitBedrockProvider()
    model = provider.get_chat_model("test-model", timeout=300, request_timeout=45)
    assert model.request_timeout == 45
