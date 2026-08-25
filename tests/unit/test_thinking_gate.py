"""Unit tests for the streamed-reasoning gate (issue #122).

The gate decides whether ``stream()``/``astream()`` may emit visible text before a
``</think>`` tag has arrived. Before that tag, reasoning bytes and a plain answer
are byte-identical, so the discriminator is configuration and never content: the
provider's ``extra_kwargs.extra_body.chat_template_kwargs.enable_thinking``.

See decisions 2 and 3 in
``openspec/changes/fix-issue-122-streaming-think-leak/design.md``.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from src.archi.pipelines.agents.utils.thinking_gate import (
    hold_visible,
    provider_emits_thinking,
)


def _config_with_block(provider: str, block: Any) -> Dict[str, Any]:
    """A config whose ``providers`` map holds ``block`` under ``provider``."""
    return {"services": {"chat_app": {"providers": {provider: block}}}}


def _config_with_thinking(provider: str, value: Any) -> Dict[str, Any]:
    """A well-formed provider block whose ``enable_thinking`` is ``value``."""
    return _config_with_block(
        provider,
        {
            "extra_kwargs": {
                "extra_body": {"chat_template_kwargs": {"enable_thinking": value}}
            }
        },
    )


# --- provider_emits_thinking: true only on an exact True (1.1) --------------


def test_true_only_when_key_is_exactly_true():
    assert (
        provider_emits_thinking(_config_with_thinking("local", True), "local") is True
    )


@pytest.mark.parametrize("value", [False, None, "true", "True", 1, 0, [], {}])
def test_non_true_values_do_not_enable_the_gate(value):
    """A truthy string or a truthy ``1`` must not read as enabled."""
    assert (
        provider_emits_thinking(_config_with_thinking("local", value), "local") is False
    )


def test_absent_provider_is_false():
    config = _config_with_thinking("local", True)
    assert provider_emits_thinking(config, "other") is False


def test_absent_provider_name_is_false():
    config = _config_with_thinking("local", True)
    assert provider_emits_thinking(config, None) is False


@pytest.mark.parametrize(
    "block",
    [
        {},
        {"extra_kwargs": {}},
        {"extra_kwargs": {"extra_body": {}}},
        {"extra_kwargs": {"extra_body": {"chat_template_kwargs": {}}}},
    ],
)
def test_absent_level_is_false(block):
    assert provider_emits_thinking(_config_with_block("local", block), "local") is False


@pytest.mark.parametrize(
    "block",
    [
        None,
        "not-a-mapping",
        [],
        {"extra_kwargs": None},
        {"extra_kwargs": "not-a-mapping"},
        {"extra_kwargs": {"extra_body": None}},
        {"extra_kwargs": {"extra_body": ["not-a-mapping"]}},
        {"extra_kwargs": {"extra_body": {"chat_template_kwargs": None}}},
        {"extra_kwargs": {"extra_body": {"chat_template_kwargs": "no"}}},
    ],
)
def test_non_mapping_at_any_level_is_false(block):
    """A streaming path must never crash on a config typo."""
    assert provider_emits_thinking(_config_with_block("local", block), "local") is False


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        "not-a-mapping",
        {"services": None},
        {"services": {"chat_app": None}},
        {"services": {"chat_app": {"providers": None}}},
        {"services": {"chat_app": {"providers": "not-a-mapping"}}},
    ],
)
def test_malformed_config_is_false(config):
    assert provider_emits_thinking(config, "local") is False


def test_gate_is_provider_granular():
    """Two providers in one config resolve independently (decision 3)."""
    config = {
        "services": {
            "chat_app": {
                "providers": {
                    "thinker": {
                        "extra_kwargs": {
                            "extra_body": {
                                "chat_template_kwargs": {"enable_thinking": True}
                            }
                        }
                    },
                    "plain": {"extra_kwargs": {}},
                }
            }
        }
    }
    assert provider_emits_thinking(config, "thinker") is True
    assert provider_emits_thinking(config, "plain") is False


# --- hold_visible: hold only while the answer is undecided (1.2) ------------


@pytest.mark.parametrize(
    "content",
    ["some reasoning", "reasoning</think>the answer", "", None],
)
def test_never_holds_when_thinking_is_impossible(content):
    """A non-thinking provider streams chunk-by-chunk exactly as before."""
    assert hold_visible(False, content) is False


def test_holds_while_no_closing_tag_has_arrived():
    assert hold_visible(True, "some reasoning") is True


def test_releases_once_a_closing_tag_is_present():
    assert hold_visible(True, "some reasoning</think>\n\nThe answer") is False


def test_releases_on_a_bare_closing_tag():
    assert hold_visible(True, "</think>") is False


@pytest.mark.parametrize("content", ["", None])
def test_empty_content_is_never_held(content):
    """There is nothing to hold, so the gate stays open."""
    assert hold_visible(True, content) is False


def test_the_word_think_alone_does_not_release_the_gate():
    """The predicate keys on the literal tag, never on the word."""
    assert hold_visible(True, "let me think about that") is True
