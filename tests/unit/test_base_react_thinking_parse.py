"""Unit tests for BaseReActAgent._parse_thinking_content orphan-tag handling.

Covers issue #84: some models (e.g. Qwen3 in certain streaming modes) emit
chain-of-thought reasoning terminated by an *orphan* ``</think>`` closing tag
with no matching opening ``<think>``. The original balanced-pair regex left that
reasoning (and the bare closing tag) in the visible answer. These tests pin the
contract in ``specs/react-thinking-sanitization/spec.md``: orphan-closed
reasoning is stripped into the thinking channel and never leaks into visible
output.
"""

from __future__ import annotations

from src.archi.pipelines.agents.base_react import BaseReActAgent


def _parse(text: str):
    """Call the parser with a minimal dummy ``self`` (the method uses no state)."""
    return BaseReActAgent._parse_thinking_content(object(), text)


def test_balanced_pair_regression():
    visible, thinking = _parse("<think>r</think>\n\nAns")
    assert visible == "Ans"
    assert "r" in thinking
    assert "</think>" not in visible and "<think>" not in visible


def test_single_orphan_closing_tag():
    visible, thinking = _parse("reasoning\n</think>\n\nAns")
    assert visible == "Ans"
    assert "reasoning" in thinking
    assert "</think>" not in visible and "<think>" not in visible


def test_multiple_orphan_closing_tags():
    visible, thinking = _parse("t1\n</think>\n\nt2\n</think>\n\nt3\n</think>\n\nAns")
    assert visible == "Ans"
    assert "</think>" not in visible and "<think>" not in visible


def test_no_tags_unchanged():
    visible, thinking = _parse("Just an answer.")
    assert visible == "Just an answer."
    assert thinking == ""


def test_empty_input():
    assert _parse("") == ("", "")
