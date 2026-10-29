"""Compare each candidate judge's scores with the inline Sonnet scores of the same rows
(fasrc/archi#737, step 5.3).

    python -m scripts.benchmarking.judge_trial.compare --package <root> [--out report.md]
        [--json report.json]

Reads ``reference/<basename>.sonnet.jsonl`` and ``scores/<judge>/<bundle>.judge_scores.json``
of the package. For each judge and metric over the real bundles: the number of rows both
judges scored, Spearman and Pearson correlation, the mean of each judge, the mean
difference (judge minus Sonnet), the mean absolute difference, agreement at the 0.5 cut,
and the rows each judge left unscored (NaN). For the control bundle: the mean and the
share below 0.5 of each answer-reading metric, beside the originals' Sonnet mean.
Throughput and token usage come from each scores file's ``judge_execution`` and
``judge_usage``. No third-party dependency: rank correlation is computed here.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

CONTROL_PREFIX = "control-unrelated-"
ANSWER_METRICS = ("faithfulness", "answer_relevancy", "answer_correctness")
CUT = 0.5


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _as_float(value: Any) -> Optional[float]:
    """The value as a float when it is a finite number, else None (unscored)."""
    return float(value) if _finite(value) else None


def _ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 3:
        return None
    return pearson(_ranks(xs), _ranks(ys))


def pair_metric(
    reference: Dict[str, Dict[str, Any]],
    scores: Dict[str, Dict[str, Any]],
    metric: str,
) -> Dict[str, Any]:
    """Pairs of (sonnet, judge) for one metric, plus who left which rows unscored."""
    keys = sorted(set(reference) & set(scores))
    pairs: List[Tuple[float, float]] = []
    ref_nan, judge_nan = 0, 0
    for key in keys:
        r = _as_float(reference[key].get(metric))
        j = _as_float(scores[key].get(metric))
        if r is None:
            ref_nan += 1
        if j is None:
            judge_nan += 1
        if r is not None and j is not None:
            pairs.append((r, j))
    return {
        "metric": metric,
        "rows_in_both_files": len(keys),
        "pairs": pairs,
        "sonnet_unscored": ref_nan,
        "judge_unscored": judge_nan,
    }


def summarize(paired: Dict[str, Any]) -> Dict[str, Any]:
    pairs = paired["pairs"]
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    n = len(pairs)
    out = {
        "metric": paired["metric"],
        "n": n,
        "rows_in_both_files": paired["rows_in_both_files"],
        "sonnet_unscored": paired["sonnet_unscored"],
        "judge_unscored": paired["judge_unscored"],
    }
    if n == 0:
        return out
    out.update(
        {
            "sonnet_mean": sum(xs) / n,
            "judge_mean": sum(ys) / n,
            "mean_diff": sum(y - x for x, y in pairs) / n,
            "mean_abs_diff": sum(abs(y - x) for x, y in pairs) / n,
            "spearman": spearman(xs, ys),
            "pearson": pearson(xs, ys),
            "agree_at_cut": sum((x >= CUT) == (y >= CUT) for x, y in pairs) / n,
        }
    )
    return out


def control_summary(
    scores: Dict[str, Dict[str, Any]], ledger: Sequence[Dict[str, Any]]
):
    by_key = {entry["key"]: entry for entry in ledger}
    rows = []
    for metric in ANSWER_METRICS:
        judge_vals = [
            float(s[metric])
            for k, s in scores.items()
            if k in by_key and _finite(s.get(metric))
        ]
        orig_vals = [
            float(by_key[k]["original_sonnet"][metric])
            for k in scores
            if k in by_key and _finite(by_key[k].get("original_sonnet", {}).get(metric))
        ]
        rows.append(
            {
                "metric": metric,
                "n": len(judge_vals),
                "judge_mean_on_swapped": (
                    (sum(judge_vals) / len(judge_vals)) if judge_vals else None
                ),
                "judge_share_below_cut": (
                    sum(v < CUT for v in judge_vals) / len(judge_vals)
                    if judge_vals
                    else None
                ),
                "sonnet_mean_on_originals": (
                    (sum(orig_vals) / len(orig_vals)) if orig_vals else None
                ),
                "unscored": sum(
                    1
                    for k in scores
                    if k in by_key and not _finite(scores[k].get(metric))
                ),
            }
        )
    return rows


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_reference(package: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """``{basename: {key: {metric: value}}}`` from the reference files."""
    out = {}
    for path in sorted((package / "reference").glob("*.sonnet.jsonl")):
        basename = path.name[: -len(".sonnet.jsonl")]
        out[basename] = {r["key"]: r for r in _read_jsonl(path)}
    return out


def load_scores(package: Path, judge: str) -> Dict[str, Dict[str, Any]]:
    """``{bundle folder name: scores document}`` for one judge."""
    out = {}
    for path in sorted((package / "scores" / judge).glob("*.judge_scores.json")):
        doc = json.loads(path.read_text())
        out[doc["bundle"]] = doc
    return out


def judges_in(package: Path) -> List[str]:
    root = package / "scores"
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def compare_judge(package: Path, judge: str, reference, ledger) -> Dict[str, Any]:
    docs = load_scores(package, judge)
    metrics_seen: List[str] = []
    per_metric: Dict[str, Dict[str, Any]] = {}
    execution = []
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    control = None
    for bundle_name, doc in docs.items():
        basename = doc.get("result_basename") or bundle_name.rsplit("-arm", 1)[0]
        ex = doc.get("judge_execution") or {}
        execution.append(
            {
                "bundle": bundle_name,
                "rows": doc.get("row_count"),
                "elapsed_seconds": ex.get("elapsed_seconds"),
                "pairs_per_minute": ex.get("pairs_per_minute"),
                "job_id": ex.get("job_id"),
                "node": ex.get("node"),
            }
        )
        for k in usage:
            usage[k] += (doc.get("judge_usage") or {}).get(k) or 0
        if bundle_name.startswith(CONTROL_PREFIX):
            control = control_summary(doc["scores"], ledger)
            continue
        ref = reference.get(basename)
        if ref is None:
            continue
        for metric in doc.get("metrics") or []:
            if metric not in metrics_seen:
                metrics_seen.append(metric)
            paired = pair_metric(ref, doc["scores"], metric)
            acc = per_metric.setdefault(
                metric,
                {
                    "metric": metric,
                    "pairs": [],
                    "rows_in_both_files": 0,
                    "sonnet_unscored": 0,
                    "judge_unscored": 0,
                },
            )
            acc["pairs"].extend(paired["pairs"])
            for k in ("rows_in_both_files", "sonnet_unscored", "judge_unscored"):
                acc[k] += paired[k]
    return {
        "judge": judge,
        "identity": (
            next(iter(docs.values()))["ragas_judge_identity_effective"]
            if docs
            else None
        ),
        "bundles_scored": sorted(docs),
        "metrics": [summarize(per_metric[m]) for m in metrics_seen],
        "control": control,
        "execution": execution,
        "usage": usage,
    }


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(report: Dict[str, Any]) -> str:
    lines = ["# Judge trial: candidate judges vs inline Sonnet 4.5", ""]
    lines.append(
        "What is measured: each candidate judge re-scored the same answers and retrieved "
        "passages that Sonnet 4.5 scored during the October runs. Rows are paired by "
        "question; correlation is over rows both judges scored."
    )
    for entry in report["judges"]:
        lines += ["", f"## {entry['judge']}", ""]
        ident = entry.get("identity") or {}
        lines.append(
            f"Model `{ident.get('model')}` @ `{ident.get('model_revision')}`, precision "
            f"`{ident.get('precision')}`; bundles scored: {len(entry['bundles_scored'])}."
        )
        lines += [
            "",
            "| metric | rows paired | Spearman | Pearson | Sonnet mean | judge mean | judge − Sonnet | mean abs diff | agree @0.5 | Sonnet NaN | judge NaN |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for m in entry["metrics"]:
            lines.append(
                f"| {m['metric']} | {m['n']} | {_fmt(m.get('spearman'))} | {_fmt(m.get('pearson'))} | "
                f"{_fmt(m.get('sonnet_mean'))} | {_fmt(m.get('judge_mean'))} | {_fmt(m.get('mean_diff'))} | "
                f"{_fmt(m.get('mean_abs_diff'))} | {_fmt(m.get('agree_at_cut'))} | {m['sonnet_unscored']} | {m['judge_unscored']} |"
            )
        if entry.get("control"):
            lines += [
                "",
                "Unrelated-answer control (the answer belongs to another question): a judge "
                "should score these low.",
                "",
                "| metric | n | judge mean on swapped | share below 0.5 | Sonnet mean on originals | unscored |",
                "|---|---|---|---|---|---|",
            ]
            for c in entry["control"]:
                lines.append(
                    f"| {c['metric']} | {c['n']} | {_fmt(c['judge_mean_on_swapped'])} | "
                    f"{_fmt(c['judge_share_below_cut'])} | {_fmt(c['sonnet_mean_on_originals'])} | {c['unscored']} |"
                )
        u = entry["usage"]
        total_elapsed = sum((e.get("elapsed_seconds") or 0) for e in entry["execution"])
        lines += [
            "",
            f"Throughput: {_fmt(total_elapsed / 60, 1)} min of scoring over "
            f"{sum((e.get('rows') or 0) for e in entry['execution'])} rows; "
            f"{u['calls']} judge calls, {u['input_tokens']:,} input / {u['output_tokens']:,} output tokens.",
        ]
    return "\n".join(lines) + "\n"


def build_report(
    package: Path, judges: Optional[Sequence[str]] = None
) -> Dict[str, Any]:
    reference = load_reference(package)
    ledger_path = package / "reference" / "control.jsonl"
    ledger = _read_jsonl(ledger_path) if ledger_path.is_file() else []
    names = list(judges) if judges else judges_in(package)
    return {
        "package": str(package),
        "runs": sorted(reference),
        "judges": [compare_judge(package, j, reference, ledger) for j in names],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare trial judges with inline Sonnet."
    )
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--judge", action="append", dest="judges")
    parser.add_argument(
        "--out", type=Path, help="markdown report path (default: stdout)"
    )
    parser.add_argument("--json", type=Path, help="also write the full report as JSON")
    args = parser.parse_args(argv)
    report = build_report(args.package, args.judges)
    if not report["judges"]:
        print("compare: no scores under <package>/scores/<judge>/", file=sys.stderr)
        return 1
    text = render_markdown(report)
    if args.out:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    if args.json:
        slim = json.loads(json.dumps(report))
        for entry in slim["judges"]:
            for m in entry["metrics"]:
                m.pop("pairs", None)
        args.json.write_text(json.dumps(slim, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
