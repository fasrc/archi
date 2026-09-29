"""LLM token usage recorder — a lock-safe langchain callback handler (D1-D3, D6)."""

from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult


def _valid_count(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def message_usage(message: Any) -> Optional[Tuple[int, int]]:
    """Return (input_tokens, output_tokens) from a message, or None if unreported."""
    if isinstance(message, AIMessage):
        meta = message.usage_metadata
        if meta:
            i = meta.get("input_tokens")
            o = meta.get("output_tokens")
            if _valid_count(i) and _valid_count(o):
                return (i, o)

        resp = getattr(message, "response_metadata", {}) or {}
        for key, i_key, o_key in [
            ("usage", "input_tokens", "output_tokens"),
            ("token_usage", "prompt_tokens", "completion_tokens"),
        ]:
            block = resp.get(key)
            if isinstance(block, dict):
                i = block.get(i_key)
                o = block.get(o_key)
                if _valid_count(i) and _valid_count(o):
                    return (i, o)

    return None


class UsageRecorder(BaseCallbackHandler):
    """Accumulates token usage from LLM calls via on_llm_end."""

    def __init__(self, provider: str, model: str) -> None:
        super().__init__()
        self._provider = provider
        self._default_model = model
        self._lock = threading.Lock()
        self._called = False
        self._by_model: Dict[Tuple[str, str], Dict[str, int]] = {}

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        from langchain_core.outputs import ChatGeneration

        with self._lock:
            self._called = True
            for gen_list in response.generations:
                for gen in gen_list:
                    if not isinstance(gen, ChatGeneration):
                        self._bump(self._provider, self._default_model, None)
                        continue
                    msg = gen.message
                    model = self._default_model
                    if isinstance(msg, AIMessage):
                        resp_meta = getattr(msg, "response_metadata", {}) or {}
                        model = (
                            resp_meta.get("model_name")
                            or resp_meta.get("model")
                            or self._default_model
                        )
                    counts = message_usage(msg)
                    self._bump(self._provider, model, counts)

    def _bump(
        self,
        provider: str,
        model: str,
        counts: Optional[Tuple[int, int]],
    ) -> None:
        key = (provider, model)
        if key not in self._by_model:
            self._by_model[key] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "calls": 0,
                "unreported_calls": 0,
            }
        entry = self._by_model[key]
        entry["calls"] += 1
        if counts is not None:
            entry["input_tokens"] += counts[0]
            entry["output_tokens"] += counts[1]
        else:
            entry["unreported_calls"] += 1

    def snapshot(self) -> Optional[Dict[str, Any]]:
        """Return the usage summary, or None if no LLM call finished."""
        with self._lock:
            if not self._called:
                return None
            by_model: List[Dict[str, Any]] = []
            for (provider, model), counts in sorted(self._by_model.items()):
                by_model.append(
                    {
                        "provider": provider,
                        "model": model,
                        "input_tokens": counts["input_tokens"],
                        "output_tokens": counts["output_tokens"],
                        "calls": counts["calls"],
                        "unreported_calls": counts["unreported_calls"],
                    }
                )
            return {
                "input_tokens": sum(e["input_tokens"] for e in by_model),
                "output_tokens": sum(e["output_tokens"] for e in by_model),
                "calls": sum(e["calls"] for e in by_model),
                "unreported_calls": sum(e["unreported_calls"] for e in by_model),
                "by_model": by_model,
            }


def sum_usage(
    usages: Iterable[Optional[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Merge a list of usage snapshots; returns None if all inputs are None."""
    by_model: Dict[Tuple[str, str], Dict[str, int]] = {}
    any_real = False

    for u in usages:
        if u is None:
            continue
        if not isinstance(u, dict):
            raise ValueError(f"sum_usage expects dicts or None, got {type(u)!r}")
        any_real = True
        for entry in u.get("by_model", []):
            key = (entry["provider"], entry["model"])
            if key not in by_model:
                by_model[key] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "calls": 0,
                    "unreported_calls": 0,
                }
            acc = by_model[key]
            acc["input_tokens"] += entry["input_tokens"]
            acc["output_tokens"] += entry["output_tokens"]
            acc["calls"] += entry["calls"]
            acc["unreported_calls"] += entry["unreported_calls"]

    if not any_real:
        return None

    entries: List[Dict[str, Any]] = []
    for (provider, model), counts in sorted(by_model.items()):
        entries.append(
            {
                "provider": provider,
                "model": model,
                "input_tokens": counts["input_tokens"],
                "output_tokens": counts["output_tokens"],
                "calls": counts["calls"],
                "unreported_calls": counts["unreported_calls"],
            }
        )

    return {
        "input_tokens": sum(e["input_tokens"] for e in entries),
        "output_tokens": sum(e["output_tokens"] for e in entries),
        "calls": sum(e["calls"] for e in entries),
        "unreported_calls": sum(e["unreported_calls"] for e in entries),
        "by_model": entries,
    }


def phase_usage_totals(
    prep_rows: Iterable[Dict[str, Any]],
    answer_rows: Iterable[Dict[str, Any]],
    result_rows: Iterable[Dict[str, Any]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Return {"prepare", "run", "score"} usage sums from the three row sets (D6)."""

    def _phase_sum(rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return sum_usage(row.get("usage") for row in rows)

    return {
        "prepare": _phase_sum(prep_rows),
        "run": _phase_sum(answer_rows),
        "score": _phase_sum(result_rows),
    }
