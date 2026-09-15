from __future__ import annotations

from typing import Any

"""Normalise a LangChain message's ``.content`` to a printable answer string.

Providers disagree on the shape of ``BaseMessage.content``:

- OpenAI / vLLM (``ChatOpenAI``) -> a plain ``str``.
- Anthropic (``ChatAnthropic``) -> a ``list`` of content-block dicts, e.g.
  ``[{"type": "text", "text": "..."}]``.

For the list case, stringifying each block (``str(part)``) renders its Python
``repr``, so the user sees ``{'text': "..."}`` instead of prose (issue #41).
``flatten_message_content`` collapses both shapes to clean text: it extracts the
``text`` of each text block, passes through bare string parts, and drops non-text
blocks (e.g. ``tool_use``) so the answer never contains a dict literal.
"""


def flatten_message_content(content: Any) -> str:
    """Return a printable string for a message's ``.content``.

    A list is flattened block-by-block: a ``dict`` block contributes its
    ``"text"`` value, a ``str`` block contributes itself, and any other block
    (e.g. ``tool_use``) is dropped. Pieces are joined with a single space — the
    same separator the previous implementation used, so multi-block text reads
    naturally. Non-list content (``str``, ``None``, scalars) is returned via
    ``str()`` unchanged, preserving the OpenAI/vLLM path byte-for-byte.
    """
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if "text" in part:
                    parts.append(str(part["text"]))
            elif isinstance(part, str):
                parts.append(part)
            # non-text / non-string blocks have no prose to show — drop them.
        return " ".join(parts)
    return str(content)


class MessageContentMixin:
    """Normalise a ReAct agent's message content via ``flatten_message_content``.

    ``BaseReActAgent._message_content`` lives in a large module that the format
    gate cannot touch without unrelated churn, so concrete agents opt into the
    issue-#41 fix by mixing this in **before** ``BaseReActAgent`` in their bases,
    where it wins the method-resolution order:

        class FASRCDocsAgent(MessageContentMixin, BaseReActAgent): ...
    """

    def _message_content(self, message: Any) -> str:
        """Return a printable answer string for ``message.content``."""
        content = getattr(message, "content", "")
        return flatten_message_content(content)
