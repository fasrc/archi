"""The Lynx section of ``compare_runs.py`` (design D8, D9).

For arm ``path@N`` the Lynx file is ``<parent of path>/<basename>-arm<N-1>.lynx.json``,
where ``basename`` is the file name without ``.json`` and then without an
optional ``.judged``, so the pending and the judged result find the same file.
An arm with no Lynx file gets no entry, and a report with no entry gets no Lynx
section. A file that does not match its arm is shown as refused, with the
reason; a refusal never changes the exit code. Lynx is descriptive only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from scripts.benchmarking.lynx.parse import VERDICTS, strict_loads
from scripts.benchmarking.lynx.score import (
    STATUSES,
    count_rows,
    faithfulness,
    lynx_file_name,
)


def result_basename(path: Path) -> str:
    """The result basename of a pending (``.json``) or judged (``.judged.json``) result."""
    name = path.name
    if name.endswith(".json"):
        name = name[: -len(".json")]
    if name.endswith(".judged"):
        name = name[: -len(".judged")]
    return name


def split_source(source: str) -> Tuple[Path, int]:
    """``compare_runs`` writes an arm's source as ``<path>@<1-based number>``."""
    path, _, number = source.rpartition("@")
    return Path(path), int(number)


def _row_problem(key: str, row: Any) -> Optional[str]:
    if not isinstance(row, dict) or row.get("status") not in STATUSES:
        return f"row {key!r} has no status of {', '.join(STATUSES)}"
    if row["status"] == "scored" and row.get("verdict") not in VERDICTS:
        return f"row {key!r} is scored but has verdict {row.get('verdict')!r}"
    if row["status"] != "scored" and row.get("verdict") is not None:
        return f"row {key!r} is {row['status']} but has verdict {row.get('verdict')!r}"
    return None


def _mismatch(
    document: Any, basename: str, arm_index: int, raw: Mapping[str, Any]
) -> Optional[str]:
    """Why *document* is not the Lynx file of this arm, or None when it is."""
    if not isinstance(document, dict):
        return "the Lynx file is not an object"
    if document.get("result_basename") != basename:
        return (
            f"result_basename {document.get('result_basename')!r} is not this "
            f"result's {basename!r}"
        )
    if document.get("arm_index") != arm_index or isinstance(
        document.get("arm_index"), bool
    ):
        return (
            f"arm_index {document.get('arm_index')!r} is not this arm's "
            f"index {arm_index}"
        )
    bundle = raw.get("judge_bundle")
    recorded = bundle.get("digest") if isinstance(bundle, dict) else None
    if recorded is not None and document.get("bundle_rows_sha256") != recorded:
        return (
            f"bundle_rows_sha256 {document.get('bundle_rows_sha256')!r} is not the "
            f"arm's judge_bundle digest {recorded!r}"
        )
    rows = document.get("rows")
    if not isinstance(rows, dict):
        return "the Lynx file has no rows object"
    for key, row in rows.items():
        problem = _row_problem(key, row)
        if problem:
            return problem
    counts = count_rows(rows)
    if document.get("counts") != counts:
        return f"the recorded counts {document.get('counts')!r} are not the counts of its rows {counts!r}"
    if document.get("lynx_faithfulness") != faithfulness(counts):
        return (
            f"lynx_faithfulness {document.get('lynx_faithfulness')!r} is not "
            f"lynx_pass / lynx_scored of its rows"
        )
    return None


def load_arm_lynx(source: str, raw: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The Lynx file of one arm, checked; None when the arm has no Lynx file."""
    result_path, number = split_source(source)
    basename = result_basename(result_path)
    path = result_path.parent / lynx_file_name(basename, number - 1)
    if not path.exists():
        return None
    document: Any = None
    try:
        # A non-finite number would crash the report's allow_nan=False write.
        document = strict_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        reason: Optional[str] = f"the Lynx file does not load: {exc}"
    else:
        reason = _mismatch(document, basename, number - 1, raw)
    return {
        "path": str(path),
        "status": "refused" if reason else "ok",
        "reason": reason,
        "document": None if reason else document,
    }


def _context_tokens(document: Mapping[str, Any]) -> Optional[int]:
    """The token limit a Lynx file records (#773), or None when it records none."""
    budget = document.get("token_budget")
    value = budget.get("context_tokens") if isinstance(budget, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _block_entry(arm: Any, loaded: Mapping[str, Any]) -> Dict[str, Any]:
    document = loaded["document"] or {}
    counts = document.get("counts")
    return {
        "label": arm.label,
        "name": arm.name,
        "path": loaded["path"],
        "status": loaded["status"],
        "reason": loaded["reason"],
        "model_id": document.get("model_id"),
        "model_revision": document.get("model_revision"),
        "counts": counts,
        "lynx_faithfulness": faithfulness(counts) if counts else None,
        "context_tokens": _context_tokens(document),
    }


def _by_question(
    document: Mapping[str, Any], raw: Mapping[str, Any]
) -> Tuple[Dict[str, Mapping[str, Any]], List[str]]:
    """Lynx rows keyed by question text, and the keys that name no result row.

    A bundle key is the arm's positional ``question_<n>`` key, and the same key
    can name another question in another arm (gate G5), so pairs join on text.
    """
    results = raw.get("single_question_results") or {}
    by_question: Dict[str, Mapping[str, Any]] = {}
    missing: List[str] = []
    for key, row in document["rows"].items():
        result = results.get(key)
        question = result.get("question") if isinstance(result, dict) else None
        if isinstance(question, str):
            by_question[question] = row
        else:
            missing.append(key)
    return by_question, missing


def _side_reason(side: str, row: Optional[Mapping[str, Any]]) -> Optional[str]:
    if row is None:
        return f"{side} no Lynx row"
    if row["status"] != "scored":
        return f"{side} {row['status']}"
    return None


def paired_entry(
    baseline: Any,
    base_document: Mapping[str, Any],
    arm: Any,
    document: Mapping[str, Any],
) -> Dict[str, Any]:
    """Exact McNemar of PASS over the questions that both arms scored (D9).

    Each excluded question is listed under every reason that applies to it.
    """
    from scripts.benchmarking import paired_tests

    base_rows, base_missing = _by_question(base_document, baseline.raw)
    arm_rows, arm_missing = _by_question(document, arm.raw)
    order = [q for q in baseline.order if q in base_rows or q in arm_rows]
    order += sorted((set(base_rows) | set(arm_rows)) - set(order))
    excluded: Dict[str, List[str]] = {}
    base: Dict[str, bool] = {}
    other: Dict[str, bool] = {}
    for question in order:
        reasons = [
            reason
            for reason in (
                _side_reason("baseline", base_rows.get(question)),
                _side_reason("arm", arm_rows.get(question)),
            )
            if reason
        ]
        for reason in reasons:
            excluded.setdefault(reason, []).append(question)
        if not reasons:
            base[question] = base_rows[question]["verdict"] == "PASS"
            other[question] = arm_rows[question]["verdict"] == "PASS"
    if base_missing:
        excluded["baseline key not in result"] = base_missing
    if arm_missing:
        excluded["arm key not in result"] = arm_missing
    return {
        "label": arm.label,
        "name": arm.name,
        **paired_tests.paired_binary(base, other),
        "excluded": excluded,
    }


def lynx_section(baseline: Any, arms: Sequence[Any]) -> Optional[Dict[str, Any]]:
    """The ``lynx`` report key, or None when no arm has a Lynx file."""
    loaded = {arm.label: load_arm_lynx(arm.source, arm.raw) for arm in arms}
    found = [(arm, entry) for arm in arms if (entry := loaded[arm.label])]
    if not found:
        return None
    base = loaded[baseline.label]
    paired = [
        paired_entry(baseline, base["document"], arm, entry["document"])
        for arm, entry in found
        if arm is not baseline
        and entry["status"] == "ok"
        and base is not None
        and base["status"] == "ok"
    ]
    return {
        "arms": [_block_entry(arm, entry) for arm, entry in found],
        "paired": paired,
    }


def _fmt(value: Any) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def render_section(
    section: Optional[Mapping[str, Any]],
    table: Callable[[Sequence[str], Sequence[Sequence[str]]], List[str]],
) -> List[str]:
    """The markdown lines of the Lynx section; none when there is no section.

    *table* is the report's own table writer, which escapes each cell.
    """
    if not section:
        return []
    out = [
        "## Lynx faithfulness (descriptive)",
        "",
        "PatronusAI Lynx gives one PASS or FAIL per row. The rate is PASS over scored "
        "rows only: rows skipped because they do not fit in the arm's token limit, "
        "and rows whose output has no strict verdict, are in neither part. No gate "
        "reads it.",
        "",
    ]
    rows = []
    for entry in section["arms"]:
        arm = f"{entry['label']} ({entry['name']})"
        if entry["status"] != "ok":
            rows.append([arm, f"refused: {entry['reason']}"] + ["—"] * 6)
            continue
        counts = entry["counts"]
        rows.append(
            [
                arm,
                f"{entry['model_id']}@{entry['model_revision']}",
                _fmt(entry["lynx_faithfulness"]),
                str(counts["lynx_scored"]),
                str(counts["lynx_pass"]),
                str(counts["lynx_skipped_overflow"]),
                str(counts["lynx_parse_errors"]),
                (
                    "—"
                    if entry["context_tokens"] is None
                    else str(entry["context_tokens"])
                ),
            ]
        )
    out += table(
        [
            "arm",
            "model",
            "PASS rate",
            "scored",
            "pass",
            "skipped overflow",
            "parse errors",
            "token limit",
        ],
        rows,
    )
    out.append("")
    if section.get("paired"):
        out += [
            "### Lynx paired test against the baseline",
            "",
            "Exact two-sided McNemar of PASS over the questions that both arms "
            "scored, paired on question text. b = baseline PASS and arm FAIL, c = "
            "the reverse. Not Holm-adjusted and not a gate.",
            "",
        ]
        out += table(
            ["arm", "pairs", "b", "c", "p", "direction", "excluded"],
            [
                [
                    f"{entry['label']} ({entry['name']})",
                    str(entry["pairs"]),
                    str(entry["b"]),
                    str(entry["c"]),
                    _fmt(entry["p"]),
                    entry["direction"],
                    "; ".join(
                        f"{reason} {len(questions)}"
                        for reason, questions in entry["excluded"].items()
                    )
                    or "—",
                ]
                for entry in section["paired"]
            ],
        )
        out.append("")
    return out
