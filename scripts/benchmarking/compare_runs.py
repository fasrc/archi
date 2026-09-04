#!/usr/bin/env python
"""Compare two or more benchmark arms, paired per question and gated.

This is the tool ``docs/docs/interpreting_benchmark_results.md`` calls "Gap 1":
Procedure C used to be a copy-paste notebook snippet, so every comparison
re-derived the gates by hand and each one could get them wrong in a different
way. The gates are the whole point of the page, so they belong in one tested
program:

* **G4 — one question bank.** The arms must present the identical set of
  question texts. A mismatch is refused with exit 2 and there is deliberately no
  override flag: two different banks measure two different things, and no
  command-line switch makes them comparable.
* **G5 — paired on question text.** Rows join on ``question`` (the bank's
  ``user_input``, verbatim), never on the positional ``question_<n>`` key. The
  harness drops failed rows, so ``question_7`` in one run need not be the same
  question as ``question_7`` in the other. Pairing also removes the
  between-question spread — most of the variance in these scores — from the
  error bars, which is the single largest free win available.
* **G6 — intersect, then average.** Only rows that are ``status == "ok"`` *and*
  carry a finite value for the metric **in both arms** enter a delta.
* **G3 — one pinned corpus.** Unequal (or unrecorded) ``corpus_fingerprint``
  refuses with exit 2 unless ``--corpus-differs-by-design``, which prints both
  values and the Procedure-B warning rather than hiding the difference.
* **G2/G7 — a delta is SIGNIFICANT only past both thresholds.** ``|mean| > 2*SE``
  says it is not an accident of *which questions* were asked; ``|mean| > 2*sigma``
  says it is not an accident of *the day it ran*. Without a measured noise floor
  the tool prints "noise floor not measured" and never claims significance. This
  is the rule most hand-written comparisons break.
* **Procedure E — the config identity.** A non-empty
  ``divergence_from_selected_file`` means the run did not use the settings that
  were selected, so the comparison stops with its own exit code (3). A ``null``
  divergence is a *backfilled* artifact: an equal digest then means "these files
  recorded the same configuration file", never "these runs used the same
  settings", and the report says so.

Two facts about the real artifacts shape the rest of the tool.

**``<metric>_scored`` over-reports.** In
``bench_out/benchmarking-ragas-205-20260817_040939.json`` the harness writes
``context_precision_scored: "109 of 109"`` while only 108 of those cells hold a
finite number (issue #279). Every count here is recomputed from the values, and
a disagreement is printed as ``OVER-REPORTED`` instead of being trusted or
silently corrected.

**``anchor_type`` is not an anchor marker.** The FASRC bank carries an
``anchor_type`` on all 109 rows (reasoning 75 / easy_retrieve 31 /
should_refuse 3), so selecting on that field would pull the whole bank rather
than the five tripwires. Anchors are identified by **question text** against
``examples/benchmarking/anchor_questions.json``, and their type is read from
that file. They are reported in their own block and excluded from the bank
aggregates by default (Gap 3: averaging a tripwire into the score you are trying
to move both dilutes the signal and hides the tripwire).

Standard library only, plus two reuses from the project: ``normalize_bank`` for
the anchors file (so a legacy-dialect anchors file still matches) and
``derive_item_id`` for the optional ``archi eval qa`` join.

Exit codes: 0 ok, 1 usage/IO, 2 gate refusal, 3 config-divergence stop.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

METRICS: Tuple[str, ...] = (
    "answer_relevancy",
    "faithfulness",
    "context_precision",
    "context_recall",
    "answer_correctness",
)

#: Fields the bank may slice by. Reported only when the field is present in
#: every arm, because a slice that exists on one side is not a comparison.
SLICE_FIELDS: Tuple[str, ...] = ("anchor_type", "difficulty")

#: Below this many paired rows a slice mean is a direction, not a measurement.
SMALL_SLICE = 10

#: An easy-retrieve anchor drop this large is an alarm when no sigma is known.
ANCHOR_DROP_WITHOUT_SIGMA = 0.10

#: ``src/bin/service_benchmark.py`` writes this prefix instead of a value it
#: could not read; it is an absence, not an identity.
UNAVAILABLE_PREFIX = "<unavailable:"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANCHORS = REPO_ROOT / "examples" / "benchmarking" / "anchor_questions.json"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_GATE = 2
EXIT_DIVERGENCE = 3


class CompareError(Exception):
    """A refusal that carries the exit code the CLI must return."""

    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code


# --- primitives ---------------------------------------------------------------


def is_finite(value: Any) -> bool:
    """True only for a real, usable score.

    ``bool`` is excluded deliberately: it is an ``int`` subclass, so a stray
    ``true`` in an artifact would otherwise average in as 1.0.
    """
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Nearest-rank percentile: an observed value, never an interpolation."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = math.ceil(q / 100.0 * len(ordered))
    return ordered[min(len(ordered), max(1, rank)) - 1]


def _recorded(value: Any) -> Optional[str]:
    """A provenance string, or None when the harness recorded an absence."""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith(UNAVAILABLE_PREFIX):
        return None
    return value


# --- artifacts and arms -------------------------------------------------------


@dataclass
class Arm:
    """One ``benchmarking_results`` entry, indexed for pairing."""

    label: str
    source: str
    rows: Dict[str, dict]
    order: List[str]
    total_results: dict
    config_version: dict
    corpus_fingerprint: Optional[str]
    corpus_snapshot_id: Optional[str]
    code_version_digest: Optional[str]
    configuration_file: Optional[str]

    def value(self, question: str, metric: str) -> Any:
        return self.rows.get(question, {}).get(metric)

    def is_scorable(self, question: str, metric: str) -> bool:
        row = self.rows.get(question)
        if row is None or row.get("status", "ok") != "ok":
            return False
        return is_finite(row.get(metric))

    def has_metric(self, metric: str) -> bool:
        return any(metric in row for row in self.rows.values())


def load_artifact(path: Path) -> dict:
    """Parse a benchmark artifact. Bare ``NaN`` tokens are accepted (CPython
    reads them as ``float('nan')``); every consumer here tests finiteness, so a
    non-finite cell is treated as missing rather than propagating."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise CompareError(f"cannot read {path}: {exc}", EXIT_USAGE) from None
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise CompareError(f"{path} is not valid JSON: {exc}", EXIT_USAGE) from None
    if not isinstance(document, dict) or not isinstance(
        document.get("benchmarking_results"), list
    ):
        raise CompareError(
            f"{path} does not look like a benchmark artifact "
            "(no 'benchmarking_results' list)",
            EXIT_USAGE,
        )
    if not document["benchmarking_results"]:
        raise CompareError(f"{path} contains no arms", EXIT_USAGE)
    return document


def parse_arm_spec(spec: str) -> Tuple[Path, Optional[int]]:
    """Split ``path[@N]``. ``N`` is 1-based, matching the order the arms are
    written in and the ``config_versions`` list in the metadata."""
    head, sep, tail = spec.rpartition("@")
    if sep and head and tail.isdigit():
        index = int(tail)
        if index < 1:
            raise CompareError(f"arm index in '{spec}' must be 1 or greater")
        return Path(head), index
    return Path(spec), None


def build_arm(document: dict, index: int, path: Path, label: str) -> Arm:
    """Index one arm by question text.

    Duplicate question text inside a single arm is refused: keying by text is
    what makes G5 possible, and silently keeping the last of two rows with the
    same question would compare a row against a different row's neighbour.
    """
    arms = document["benchmarking_results"]
    if index > len(arms):
        raise CompareError(
            f"{path} has {len(arms)} arm(s); '@{index}' is out of range", EXIT_USAGE
        )
    raw = arms[index - 1]
    metadata = document.get("metadata") or {}
    rows: Dict[str, dict] = {}
    order: List[str] = []
    for key, row in (raw.get("single_question_results") or {}).items():
        if not isinstance(row, dict):
            continue
        question = row.get("question")
        if not isinstance(question, str):
            raise CompareError(
                f"{path} arm {index}: row '{key}' has no question text", EXIT_USAGE
            )
        if question in rows:
            raise CompareError(
                f"{path} arm {index} contains two rows for the same question "
                f"({question!r}); pairing joins on question text (G5), so the "
                "bank must not hold duplicates",
                EXIT_GATE,
            )
        rows[question] = row
        order.append(question)
    config_version = raw.get("config_version") or {}
    fingerprint = _recorded(raw.get("corpus_fingerprint")) or _recorded(
        metadata.get("corpus_fingerprint")
    )
    code_version = metadata.get("code_version") or {}
    return Arm(
        label=label,
        source=f"{path}@{index}",
        rows=rows,
        order=order,
        total_results=raw.get("total_results") or {},
        config_version=config_version,
        corpus_fingerprint=fingerprint,
        corpus_snapshot_id=_recorded(metadata.get("corpus_snapshot_id")),
        code_version_digest=_recorded(code_version.get("digest")),
        configuration_file=raw.get("configuration_file"),
    )


def load_arms(specs: Sequence[str]) -> List[Arm]:
    """Load every spec into arms.

    A bare path to a ``-cd`` sweep expands into all of its arms, because one
    sweep invocation *is* the comparison (Procedure B); a single-arm file keeps
    its plain filename as the label.
    """
    arms: List[Arm] = []
    used: Dict[str, int] = {}
    for spec in specs:
        path, index = parse_arm_spec(spec)
        document = load_artifact(path)
        count = len(document["benchmarking_results"])
        indices = [index] if index is not None else list(range(1, count + 1))
        for one in indices:
            stem = path.stem
            label = stem if (index is None and count == 1) else f"{stem}@{one}"
            seen = used.get(label, 0) + 1
            used[label] = seen
            if seen > 1:
                label = f"{label}#{seen}"
            arms.append(build_arm(document, one, path, label))
    return arms


# --- G4: the question sets --------------------------------------------------


def question_set_diff(base: Arm, other: Arm) -> Dict[str, List[str]]:
    """Questions each arm has that the other does not, in the arms' run order."""
    return {
        "only_in_base": [q for q in base.order if q not in other.rows],
        "only_in_other": [q for q in other.order if q not in base.rows],
    }


def require_same_question_sets(baseline: Arm, arms: Sequence[Arm]) -> None:
    """G4. No override exists on purpose."""
    for arm in arms:
        if arm is baseline:
            continue
        diff = question_set_diff(baseline, arm)
        if not diff["only_in_base"] and not diff["only_in_other"]:
            continue
        lines = [
            f"G4 refused: {baseline.label} and {arm.label} did not run the same "
            "question bank, so their scores measure different things. There is "
            "no override for this."
        ]
        for side, label in (
            ("only_in_base", baseline.label),
            ("only_in_other", arm.label),
        ):
            missing = diff[side]
            if not missing:
                continue
            shown = ", ".join(repr(q) for q in missing[:3])
            more = f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""
            lines.append(f"  only in {label} ({len(missing)}): {shown}{more}")
        raise CompareError("\n".join(lines), EXIT_GATE)


# --- G3 and Procedure E: the provenance gates -------------------------------


def provenance_rows(arms: Sequence[Arm]) -> List[dict]:
    """One row per provenance field, one column per arm."""
    fields = [
        ("source", lambda a: a.source),
        ("corpus_fingerprint", lambda a: a.corpus_fingerprint or "not recorded"),
        ("corpus_snapshot_id", lambda a: a.corpus_snapshot_id or "not recorded"),
        ("code_version.digest", lambda a: a.code_version_digest or "not recorded"),
        (
            "config_version.digest",
            lambda a: a.config_version.get("digest") or "not recorded",
        ),
        (
            "divergence_from_selected_file",
            lambda a: (
                "null (backfilled)"
                if a.config_version.get("divergence_from_selected_file") is None
                else json.dumps(a.config_version["divergence_from_selected_file"])
            ),
        ),
        ("configuration_file", lambda a: a.configuration_file or "not recorded"),
    ]
    return [
        {"field": name, "values": {arm.label: str(read(arm)) for arm in arms}}
        for name, read in fields
    ]


def corpus_gate(arms: Sequence[Arm], allow_differs: bool) -> dict:
    """G3: both arms must have run against one pinned corpus."""
    values = {arm.label: arm.corpus_fingerprint for arm in arms}
    shown = ", ".join(
        f"{label}={value or 'not recorded'}" for label, value in values.items()
    )
    unrecorded = [label for label, value in values.items() if value is None]
    distinct = {value for value in values.values() if value is not None}
    if not unrecorded and len(distinct) <= 1:
        return {
            "id": "G3",
            "name": "one pinned corpus",
            "status": "pass",
            "detail": f"corpus_fingerprint {shown}",
        }
    if unrecorded:
        reason = (
            "corpus_fingerprint was not recorded for "
            f"{', '.join(unrecorded)}; the artifacts cannot show the arms saw "
            "the same documents"
        )
    else:
        reason = f"the arms ran against different corpora: {shown}"
    if not allow_differs:
        raise CompareError(
            f"G3 refused: {reason}. Retrieval metrics move for free across "
            "corpora. Re-run both arms against one pinned corpus, or pass "
            "--corpus-differs-by-design if the corpora differ on purpose "
            "(Procedure B).",
            EXIT_GATE,
        )
    return {
        "id": "G3",
        "name": "one pinned corpus",
        "status": "OVERRIDDEN (--corpus-differs-by-design)",
        "detail": (
            f"{reason}. Procedure B: with the corpus unpinned, context_precision, "
            "context_recall and the source accuracies can move without any change "
            "to the system under test. Pin the ingestion inputs instead "
            "(identical config/lists/sources.list, ingested from the same source "
            "state) and record both corpus_snapshot_id values in the "
            "pre-registration."
        ),
    }


def divergence_gate(arms: Sequence[Arm], ignore: bool) -> dict:
    """Procedure E: refuse to compare arms that did not use the config selected."""
    diverged = {
        arm.label: arm.config_version.get("divergence_from_selected_file")
        for arm in arms
        if arm.config_version.get("divergence_from_selected_file")
    }
    if diverged:
        shown = "; ".join(
            f"{label}: {json.dumps(keys)}" for label, keys in diverged.items()
        )
        if not ignore:
            raise CompareError(
                "Procedure E stop: divergence_from_selected_file is non-empty, so "
                f"the run did not use the configuration that was selected ({shown}). "
                "Its scores cannot be attributed to either setting. Pass "
                "--ignore-config-divergence only if you know the diverging keys do "
                "not touch what you are measuring.",
                EXIT_DIVERGENCE,
            )
        return {
            "id": "Procedure E",
            "name": "config divergence",
            "status": "OVERRIDDEN (--ignore-config-divergence)",
            "detail": shown,
        }
    backfilled = [
        arm.label
        for arm in arms
        if arm.config_version.get("divergence_from_selected_file") is None
    ]
    if backfilled:
        return {
            "id": "Procedure E",
            "name": "config divergence",
            "status": "caveat",
            "detail": (
                "divergence_from_selected_file is null for "
                f"{', '.join(backfilled)}: these artifacts were backfilled, so an "
                "equal config_version.digest means they recorded the same "
                "configuration FILE, never that the runs used the same settings. "
                "Only an artifact stamped by the current code, where that field is "
                "a real list, supports the stronger claim."
            ),
        }
    return {
        "id": "Procedure E",
        "name": "config divergence",
        "status": "pass",
        "detail": "divergence_from_selected_file is empty for every arm",
    }


# --- pairing and statistics --------------------------------------------------


def paired_deltas(
    base: Arm, treat: Arm, metric: str, questions: Sequence[str]
) -> List[float]:
    """G5 + G6: treatment minus baseline, over rows clean in **both** arms."""
    deltas = []
    for question in questions:
        if base.is_scorable(question, metric) and treat.is_scorable(question, metric):
            deltas.append(
                float(treat.value(question, metric))
                - float(base.value(question, metric))
            )
    return deltas


def summarize_deltas(deltas: Sequence[float]) -> dict:
    """n, mean and the standard error of the mean. SE needs at least two rows."""
    n = len(deltas)
    if n == 0:
        return {"n": 0, "mean": None, "se": None}
    mean = statistics.fmean(deltas)
    se = statistics.stdev(deltas) / math.sqrt(n) if n >= 2 else None
    return {"n": n, "mean": mean, "se": se}


def verdict(summary: dict, sigma: Optional[float]) -> str:
    """G7. Both thresholds, or no claim.

    ``2*SE`` asks "is this an accident of *which questions* I asked?"; ``2*sigma``
    asks "is this an accident of *the day I ran it*?". They fail in different
    ways, so passing one is not evidence.
    """
    mean, se = summary.get("mean"), summary.get("se")
    if mean is None or se is None:
        return "too few paired rows"
    if sigma is None:
        return "noise floor not measured"
    if abs(mean) > 2 * se and abs(mean) > 2 * sigma:
        return "SIGNIFICANT"
    return "not distinguishable"


def parse_noise_floor(text: str) -> Dict[str, float]:
    """Parse ``metric=sigma,metric=sigma`` from Procedure A."""
    sigmas: Dict[str, float] = {}
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, value = chunk.partition("=")
        name = name.strip()
        if not sep:
            raise CompareError(
                f"--noise-floor expects 'metric=sigma' pairs, got {chunk!r}"
            )
        if name not in METRICS:
            raise CompareError(
                f"--noise-floor names an unknown metric {name!r}; "
                f"choose from {', '.join(METRICS)}"
            )
        try:
            sigmas[name] = float(value)
        except ValueError:
            raise CompareError(
                f"--noise-floor value for {name} is not a number: {value!r}"
            ) from None
    if not sigmas:
        raise CompareError("--noise-floor was empty")
    return sigmas


def recomputed_aggregate(
    arm: Arm, metric: str, questions: Optional[Sequence[str]] = None
) -> dict:
    """Mean over the finite cells, with the denominator that produced it."""
    keys = list(arm.order) if questions is None else list(questions)
    values = [
        arm.rows[q].get(metric) for q in keys if q in arm.rows and metric in arm.rows[q]
    ]
    finite = [float(v) for v in values if is_finite(v)]
    return {
        "mean": statistics.fmean(finite) if finite else None,
        "finite": len(finite),
        "total": len(values),
    }


def noise_floor_from_runs(paths: Sequence[str]) -> Dict[str, float]:
    """sigma per metric from same-code replicates (Procedure A).

    Every arm of every file is one replicate, so a ``-cd`` sweep of the same
    config repeated N times works as well as N separate invocations. The means
    are **recomputed** rather than read from ``aggregate_<metric>``, because the
    recorded aggregate shares the denominator defect that ``<metric>_scored``
    exposes.
    """
    per_metric: Dict[str, List[float]] = {metric: [] for metric in METRICS}
    replicates = 0
    for spec in paths:
        for arm in load_arms([spec]):
            replicates += 1
            for metric in METRICS:
                mean = recomputed_aggregate(arm, metric)["mean"]
                if mean is not None:
                    per_metric[metric].append(mean)
    if replicates < 2:
        raise CompareError(
            "--noise-runs needs at least two replicate arms to estimate sigma; "
            f"found {replicates}",
            EXIT_USAGE,
        )
    return {
        metric: statistics.stdev(means)
        for metric, means in per_metric.items()
        if len(means) >= 2
    }


def parse_scored(text: Any) -> Optional[Tuple[int, int]]:
    """Read the harness's ``"N of M"`` string, or None when it is not one."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*(\d+)\s+of\s+(\d+)\s*", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def scored_counts(arm: Arm) -> List[dict]:
    """Recomputed scored counts, flagged against what the harness reported.

    The flag is the point: ``<metric>_scored`` counts eligible rows, not scored
    ones, so a NaN cell is reported as scored (#279). The recomputed number is
    the truth; the reported one is kept beside it so a stale artifact is
    readable rather than quietly rewritten.
    """
    rows = []
    for metric in METRICS:
        if not arm.has_metric(metric):
            continue
        counted = recomputed_aggregate(arm, metric)
        reported = arm.total_results.get(f"{metric}_scored")
        parsed = parse_scored(reported)
        flag = "ok"
        if parsed is None:
            flag = "not reported"
        elif parsed[0] > counted["finite"]:
            flag = "OVER-REPORTED"
        elif parsed[0] < counted["finite"]:
            flag = "UNDER-REPORTED"
        rows.append(
            {
                "metric": metric,
                "reported": reported if isinstance(reported, str) else None,
                "finite": counted["finite"],
                "total": counted["total"],
                "recomputed_mean": counted["mean"],
                "reported_aggregate": (
                    arm.total_results.get(f"aggregate_{metric}")
                    if is_finite(arm.total_results.get(f"aggregate_{metric}"))
                    else None
                ),
                "flag": flag,
            }
        )
    return rows


def recomputed_source_accuracy(arm: Arm) -> dict:
    """Strict source accuracy over rows that declare at least one source.

    A row declaring no sources is excluded from both numerator and denominator:
    ``all([])`` is vacuously true, which is how the ``should_refuse`` anchor used
    to book a free hit.
    """
    scored = [
        row
        for row in arm.rows.values()
        if isinstance(row.get("reference_sources_metadata"), list)
        and row["reference_sources_metadata"]
    ]
    hits = sum(
        1
        for row in scored
        if all(entry.get("matched") for entry in row["reference_sources_metadata"])
    )
    return {
        "scored": len(scored),
        "hits": hits,
        "accuracy": hits / len(scored) if scored else None,
    }


def source_block(arms: Sequence[Arm]) -> List[dict]:
    """Reported source accuracy beside a recomputation from the rows."""
    block = []
    for arm in arms:
        recomputed = recomputed_source_accuracy(arm)
        totals = arm.total_results
        block.append(
            {
                "arm": arm.label,
                "source_accuracy": (
                    totals.get("source_accuracy")
                    if is_finite(totals.get("source_accuracy"))
                    else None
                ),
                "relative_source_accuracy": (
                    totals.get("relative_source_accuracy")
                    if is_finite(totals.get("relative_source_accuracy"))
                    else None
                ),
                "source_scored_count": totals.get("source_scored_count"),
                "recomputed_accuracy": recomputed["accuracy"],
                "recomputed_scored": recomputed["scored"],
                "recomputed_hits": recomputed["hits"],
            }
        )
    return block


def timing_block(arms: Sequence[Arm]) -> List[dict]:
    """Wall-clock per question, cold and warm.

    The harness is sequential (``_PARALLEL_SAFE_MAX_WORKERS = 1``) and writes
    ``question_<n>`` in run order, so the first row absorbs whatever start-up
    the arm paid. The warm variants drop it.
    """
    block = []
    for arm in arms:
        ordered = [
            float(arm.rows[q]["time_elapsed"])
            for q in arm.order
            if is_finite(arm.rows[q].get("time_elapsed"))
        ]
        warm = ordered[1:]
        block.append(
            {
                "arm": arm.label,
                "n": len(ordered),
                "mean": statistics.fmean(ordered) if ordered else None,
                "p90": percentile(ordered, 90),
                "warm_n": len(warm),
                "warm_mean": statistics.fmean(warm) if warm else None,
                "warm_p90": percentile(warm, 90),
            }
        )
    return block


# --- report ------------------------------------------------------------------


def paired_block(
    baseline: Arm,
    arms: Sequence[Arm],
    questions: Sequence[str],
    sigmas: Dict[str, float],
) -> List[dict]:
    """The paired table: one row per treatment arm per metric."""
    block = []
    for arm in arms:
        if arm is baseline:
            continue
        for metric in METRICS:
            if not (baseline.has_metric(metric) and arm.has_metric(metric)):
                continue
            summary = summarize_deltas(paired_deltas(baseline, arm, metric, questions))
            sigma = sigmas.get(metric)
            block.append(
                {
                    "arm": arm.label,
                    "baseline": baseline.label,
                    "metric": metric,
                    "n": summary["n"],
                    "mean": summary["mean"],
                    "se": summary["se"],
                    "sigma": sigma,
                    "baseline_mean": recomputed_aggregate(baseline, metric, questions)[
                        "mean"
                    ],
                    "arm_mean": recomputed_aggregate(arm, metric, questions)["mean"],
                    "verdict": verdict(summary, sigma),
                }
            )
    return block


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _signed(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> List[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render_markdown(report: dict) -> str:
    """The human-readable report. ``--json`` writes the same data."""
    out: List[str] = ["# Benchmark comparison", ""]
    labels = [arm["label"] for arm in report["arms"]]
    out.append(
        f"Baseline: `{report['baseline']}`. Arms: "
        + ", ".join(f"`{label}`" for label in labels)
        + f". Paired on {report['paired_question_count']} bank questions "
        f"(anchors {'included' if report['anchors_in_bank'] else 'excluded'})."
    )
    out.append("")

    out += ["## Provenance", ""]
    out += _table(
        ["field"] + labels,
        [
            [row["field"]] + [row["values"].get(label, "") for label in labels]
            for row in report["provenance"]
        ],
    )
    out.append("")

    out += ["## Gates", ""]
    out += _table(
        ["gate", "status", "detail"],
        [
            [f"{gate['id']} {gate['name']}", gate["status"], gate["detail"]]
            for gate in report["gates"]
        ],
    )
    out.append("")

    out += ["## Paired metrics", ""]
    if report["noise_floor"]:
        out.append(
            "Noise floor (sigma): "
            + ", ".join(
                f"`{metric}`={_fmt(value)}"
                for metric, value in sorted(report["noise_floor"].items())
            )
        )
    else:
        out.append(
            "Noise floor: **not measured** (Procedure A). No delta can be called "
            "SIGNIFICANT without it (G2)."
        )
    out.append("")
    out += _table(
        ["arm", "metric", "n", "baseline", "arm", "delta", "SE", "sigma", "verdict"],
        [
            [
                row["arm"],
                row["metric"],
                str(row["n"]),
                _fmt(row["baseline_mean"]),
                _fmt(row["arm_mean"]),
                _signed(row["mean"]),
                _fmt(row["se"]),
                _fmt(row["sigma"]),
                row["verdict"],
            ]
            for row in report["paired"]
        ],
    )
    out.append("")

    out += ["## Scored counts", ""]
    out.append(
        "Recomputed from finite values. `<metric>_scored` counts *eligible* rows, "
        "not scored ones (#279), so a disagreement is flagged rather than trusted."
    )
    out.append("")
    out += _table(
        ["arm", "metric", "reported", "finite", "total", "recomputed mean", "flag"],
        [
            [
                label,
                row["metric"],
                row["reported"] or "not reported",
                str(row["finite"]),
                str(row["total"]),
                _fmt(row["recomputed_mean"]),
                row["flag"],
            ]
            for label, rows in report["scored_counts"].items()
            for row in rows
        ],
    )
    out.append("")

    out += ["## Sources", ""]
    out += _table(
        [
            "arm",
            "source_accuracy",
            "relative",
            "source_scored_count",
            "recomputed",
            "recomputed hits/scored",
        ],
        [
            [
                row["arm"],
                _fmt(row["source_accuracy"]),
                _fmt(row["relative_source_accuracy"]),
                _fmt(row["source_scored_count"]),
                _fmt(row["recomputed_accuracy"]),
                f"{row['recomputed_hits']}/{row['recomputed_scored']}",
            ]
            for row in report["sources"]
        ],
    )
    out.append("")

    out += ["## Timing", ""]
    out += _table(
        ["arm", "n", "mean s", "p90 s", "warm n", "warm mean s", "warm p90 s"],
        [
            [
                row["arm"],
                str(row["n"]),
                _fmt(row["mean"], 2),
                _fmt(row["p90"], 2),
                str(row["warm_n"]),
                _fmt(row["warm_mean"], 2),
                _fmt(row["warm_p90"], 2),
            ]
            for row in report["timing"]
        ],
    )
    out.append("")
    return "\n".join(out)


def build_report(
    arms: Sequence[Arm],
    baseline: Arm,
    *,
    gates: Sequence[dict],
    sigmas: Dict[str, float],
    questions: Sequence[str],
    anchors_in_bank: bool,
) -> dict:
    return {
        "baseline": baseline.label,
        "arms": [
            {"label": arm.label, "source": arm.source, "questions": len(arm.rows)}
            for arm in arms
        ],
        "paired_question_count": len(questions),
        "anchors_in_bank": anchors_in_bank,
        "provenance": provenance_rows(arms),
        "gates": list(gates),
        "noise_floor": dict(sigmas),
        "paired": paired_block(baseline, arms, questions, sigmas),
        "scored_counts": {arm.label: scored_counts(arm) for arm in arms},
        "sources": source_block(arms),
        "timing": timing_block(arms),
    }


# --- CLI ---------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error; this tool reserves 2 for a gate."""

    def error(self, message: str):  # pragma: no cover - argparse plumbing
        raise CompareError(f"{self.prog}: {message}", EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="compare_runs.py",
        description=(
            "Paired, gated comparison of benchmark artifacts "
            "(docs/docs/interpreting_benchmark_results.md, Procedure C)."
        ),
    )
    parser.add_argument(
        "specs",
        nargs="+",
        metavar="ARTIFACT[@N]",
        help=(
            "benchmark artifacts to compare. A bare path to a -cd sweep expands "
            "into all of its arms; @N (1-based) picks one."
        ),
    )
    parser.add_argument(
        "--baseline", help="label of the reference arm (default: the first)"
    )
    parser.add_argument(
        "--noise-floor",
        metavar="METRIC=SIGMA,...",
        help="per-metric noise floor from Procedure A",
    )
    parser.add_argument(
        "--noise-runs",
        nargs="+",
        metavar="ARTIFACT",
        default=[],
        help="same-code replicate artifacts; sigma is the stdev of their recomputed means",
    )
    parser.add_argument(
        "--corpus-differs-by-design",
        action="store_true",
        help="allow unequal or unrecorded corpus fingerprints (Procedure B)",
    )
    parser.add_argument(
        "--ignore-config-divergence",
        action="store_true",
        help="continue despite a non-empty divergence_from_selected_file",
    )
    parser.add_argument(
        "--anchors",
        default=str(DEFAULT_ANCHORS),
        help="anchor questions file (default: examples/benchmarking/anchor_questions.json)",
    )
    parser.add_argument(
        "--include-anchors-in-bank",
        action="store_true",
        help="average the anchors into the bank aggregates (Gap 3: do not)",
    )
    parser.add_argument("--json", metavar="PATH", help="write the report as JSON")
    return parser


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    arms = load_arms(args.specs)
    if len(arms) < 2:
        raise CompareError(
            "need at least two arms to compare; give two artifacts, or one -cd "
            "sweep that holds more than one arm",
            EXIT_USAGE,
        )
    if args.baseline is None:
        baseline = arms[0]
    else:
        matches = [arm for arm in arms if arm.label == args.baseline]
        if not matches:
            raise CompareError(
                f"--baseline {args.baseline!r} matches no arm; "
                f"labels are {', '.join(arm.label for arm in arms)}",
                EXIT_USAGE,
            )
        baseline = matches[0]

    require_same_question_sets(baseline, arms)
    gates = [
        {
            "id": "G4",
            "name": "identical question bank",
            "status": "pass",
            "detail": f"{len(baseline.rows)} questions present in every arm",
        },
        corpus_gate(arms, args.corpus_differs_by_design),
        divergence_gate(arms, args.ignore_config_divergence),
    ]

    sigmas: Dict[str, float] = {}
    if args.noise_runs:
        sigmas.update(noise_floor_from_runs(args.noise_runs))
    if args.noise_floor:
        sigmas.update(parse_noise_floor(args.noise_floor))

    questions = list(baseline.order)
    report = build_report(
        arms,
        baseline,
        gates=gates,
        sigmas=sigmas,
        questions=questions,
        anchors_in_bank=args.include_anchors_in_bank,
    )
    print(render_markdown(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, allow_nan=False))
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        return run(argv)
    except CompareError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
