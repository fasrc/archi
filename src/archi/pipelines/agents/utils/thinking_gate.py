"""Decide when a streaming agent may emit visible text (issue #122).

A Qwen-style provider with ``enable_thinking`` turned on has its ``<think>``
opener pre-filled by the chat template, so the model starts generating *inside*
its reasoning block and emits a closing ``</think>`` with no opener. Until that
closing tag arrives, the accumulated text is pure reasoning, but it is
byte-identical to a plain answer: the same characters in the same order. No
content rule can separate the two.

So the discriminator is configuration, never content. ``provider_emits_thinking``
reads the provider's own
``extra_kwargs.extra_body.chat_template_kwargs.enable_thinking``, and
``hold_visible`` holds emission only while that provider's answer is still
undecided. A provider that does not declare thinking streams exactly as before.

The two functions live here rather than inline in ``base_react.py`` so both the
sync and the async stream paths share one decision and cannot drift.

See ``openspec/changes/fix-issue-122-streaming-think-leak/design.md``.
"""

from __future__ import annotations

from typing import Any, Optional

#: The literal tag that ends a reasoning block. The gate keys on this tag alone,
#: never on the word "think", so an answer that discusses thinking is unaffected.
THINK_CLOSE_TAG = "</think>"

#: Path from a provider block down to the thinking flag. ``chat_template_kwargs``
#: is spread verbatim into the request body for every model on that provider,
#: which is why the gate is provider-granular and not model-granular.
_THINKING_PATH = (
    "extra_kwargs",
    "extra_body",
    "chat_template_kwargs",
    "enable_thinking",
)


def provider_emits_thinking(config: Any, provider: Optional[str]) -> bool:
    """Report whether ``provider`` is configured to emit reasoning.

    True only when
    ``services.chat_app.providers.<provider>.extra_kwargs.extra_body.chat_template_kwargs.enable_thinking``
    is exactly ``True``. A truthy stand-in such as ``1`` or the string ``"true"``
    does not count: the value is passed verbatim to the backend, so only a real
    boolean means the chat template pre-fills the opener.

    The provider name is taken as an argument rather than read from
    ``services.chat_app.default_provider``, because a request-local view can
    rebind the provider before the stream runs.

    Every level is checked with ``isinstance`` and a malformed config returns
    ``False`` — stream as before. This runs on a streaming path and must never
    raise on a config typo.
    """
    if not isinstance(provider, str):
        return False
    node: Any = config
    for key in ("services", "chat_app", "providers", provider, *_THINKING_PATH):
        if not isinstance(node, dict):
            return False
        node = node.get(key)
    return node is True


def hold_visible(thinking_possible: bool, accumulated_content: Optional[str]) -> bool:
    """Report whether visible text must be withheld for now.

    Holds only while all three hold: the provider can emit reasoning, some text
    has accumulated, and no ``</think>`` has arrived yet to decide what that text
    is. Once the closing tag appears the answer is decided and the gate opens for
    the rest of the stream.
    """
    if not thinking_possible:
        return False
    if not isinstance(accumulated_content, str) or not accumulated_content:
        return False
    return THINK_CLOSE_TAG not in accumulated_content
