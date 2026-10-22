"""Hide run-detail answer-key fields from VIEW-only callers (issue #563).

``answer``/``answer_sha256``, gold atoms, oracle evidence, and judgment
``rationale`` text all let a viewer reconstruct the approved answer to a
question they are meant to be evaluated against, so they are stripped from
the payload unless the caller has explicitly asked for the full run. The
manifest's ``artifacts`` digests go too: rows are written with sorted keys, so
a viewer who sees every other field of a row can confirm a guessed answer by
hashing it against the file digest. Diagnostic text (``error`` and
``live_validation.detail``) goes as well: a failed comparison, extraction, or
oracle call can quote the model output or the oracle result in its exception.
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
        "error",
    ),
    "prepared_items": (
        "answer",
        "answer_sha256",
        "gold_atoms",
        "oracle",
        "oracle_metadata",
        "oracle_calls",
        "error",
    ),
    "live_checks": ("answer", "answer_sha256", "metadata", "calls"),
    "answers": ("answer", "error"),
    "evaluation_results": ("answer", "error"),
}
VIEWER_HIDDEN_JUDGMENT_FIELDS = ("rationale",)
VIEWER_HIDDEN_MANIFEST_FIELDS = ("artifacts",)
VIEWER_HIDDEN_LIVE_VALIDATION_FIELDS = ("detail",)


def redact_run_for_viewer(payload: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    manifest = result.get("manifest")
    if isinstance(manifest, dict):
        for field in VIEWER_HIDDEN_MANIFEST_FIELDS:
            manifest.pop(field, None)
    for section, fields in VIEWER_HIDDEN_FIELDS.items():
        rows = result.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for field in fields:
                row.pop(field, None)
            validation = row.get("live_validation")
            if isinstance(validation, dict):
                for field in VIEWER_HIDDEN_LIVE_VALIDATION_FIELDS:
                    validation.pop(field, None)
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
