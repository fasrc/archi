from __future__ import annotations

from types import SimpleNamespace

from src.archi.pipelines.agents.message_content import (
    MessageContentMixin,
    flatten_message_content,
)


def test_anthropic_text_block_list_extracts_text():
    # ChatAnthropic returns .content as a list of content-block dicts; the user
    # must see the prose, never the dict repr (issue #41).
    content = [{"type": "text", "text": "I found the relevant FASRC documentation."}]
    result = flatten_message_content(content)
    assert result == "I found the relevant FASRC documentation."
    assert "{'text'" not in result
    assert "{'type'" not in result


def test_multiple_text_blocks_joined_with_single_space():
    content = [
        {"type": "text", "text": "hi"},
        {"type": "text", "text": "there"},
    ]
    assert flatten_message_content(content) == "hi there"


def test_plain_string_content_unchanged():
    # OpenAI/vLLM path: .content is already a plain string — must pass through.
    assert flatten_message_content("plain answer") == "plain answer"


def test_mixed_text_and_non_text_blocks_drop_non_text():
    content = [
        {"type": "text", "text": "the answer"},
        {"type": "tool_use", "name": "search_knowledge_base", "input": {"q": "x"}},
    ]
    result = flatten_message_content(content)
    assert result == "the answer"
    assert "tool_use" not in result
    assert "{" not in result


def test_bare_string_parts_in_list_pass_through():
    content = ["lead-in", {"type": "text", "text": "block"}]
    assert flatten_message_content(content) == "lead-in block"


def test_list_of_only_non_text_blocks_yields_empty_string():
    content = [{"type": "tool_use", "name": "search"}]
    assert flatten_message_content(content) == ""


def test_mixin_flattens_anthropic_message_content():
    class _Agent(MessageContentMixin):
        pass

    msg = SimpleNamespace(
        content=[
            {"type": "text", "text": "the answer"},
            {"type": "tool_use", "name": "search"},
        ]
    )
    result = _Agent()._message_content(msg)
    assert result == "the answer"
    assert "{" not in result


def test_mixin_passes_through_plain_string_content():
    class _Agent(MessageContentMixin):
        pass

    assert _Agent()._message_content(SimpleNamespace(content="plain")) == "plain"


def test_mixin_missing_content_attribute_defaults_to_empty_string():
    class _Agent(MessageContentMixin):
        pass

    assert _Agent()._message_content(object()) == ""
