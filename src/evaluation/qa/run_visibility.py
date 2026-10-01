"""Hide run-detail answer-key fields from VIEW-only callers (issue #563).

``answer``/``answer_sha256``, gold atoms, oracle evidence, and judgment
``rationale`` text all let a viewer reconstruct the approved answer to a
question they are meant to be evaluated against, so they are stripped from
the payload unless the caller has explicitly asked for the full run.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

VIEWER_HIDDEN_FIELDS = {
    "preparation": (
        "answer",
        "answer_sha256",
        "gold_atoms",
        "oracle",
        "oracle_metadata",
        "oracle_calls",
    ),
    "prepared_items": (
        "answer",
        "answer_sha256",
        "gold_atoms",
        "oracle",
        "oracle_metadata",
        "oracle_calls",
    ),
    "live_checks": ("answer", "answer_sha256", "metadata", "calls"),
    "answers": ("answer",),
    "evaluation_results": ("answer",),
}
VIEWER_HIDDEN_JUDGMENT_FIELDS = ("rationale",)


def redact_run_for_viewer(payload: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    for section, fields in VIEWER_HIDDEN_FIELDS.items():
        rows = result.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for field in fields:
                row.pop(field, None)
    for row in result.get("evaluation_results") or []:
        if not isinstance(row, dict):
            continue
        judgments = row.get("judgments")
        if not isinstance(judgments, list):
            continue
        for judgment in judgments:
            if not isinstance(judgment, dict):
                continue
            for field in VIEWER_HIDDEN_JUDGMENT_FIELDS:
                judgment.pop(field, None)
    return result
