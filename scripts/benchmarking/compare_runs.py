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
import importlib.util
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

#: How much of a refusal-anchor answer the report quotes.
REFUSAL_SNIPPET_CHARS = 160

#: An answer at least this long that never declines is a confident answer to a
#: question the system has no documentation for — the failure the anchor exists
#: to catch. Shorter non-declining answers go to REVIEW rather than being judged
#: by length alone.
CONFIDENT_ANSWER_CHARS = 200

#: Phrases that mark a decline-and-refer. Deliberately a small, readable list:
#: the verdict it produces is a *proposal for a human*, printed beside the first
#: 160 characters of the answer so the reader can overrule it, and a `--qa-run`
#: item pass overrides it outright.
DECLINE_PATTERNS = (
    r"i (?:do not|don't) have",
    r"i (?:do not|don't) know",
    r"i (?:can not|cannot|can't) (?:help|answer|provide|find)",
    r"i(?:'m| am) (?:not able|unable)",
    r"(?:outside|beyond) (?:the |my )?(?:scope|documentation)",
    r"not (?:covered|documented) (?:by|in)",
    # Impersonal declines. The 2026-08-17 runs answered this anchor with "The
    # indexed FASRC documentation does not appear to cover ..." — a correct
    # refusal that never says "I", which a first-person-only rule scored FAIL.
    r"do(?:es)? not (?:appear to |seem to )?(?:cover|include|mention|contain|discuss|have|address)",
    r"(?:is|are) not (?:covered|documented|included|available)",
    r"no (?:fasrc )?documentation",
    r"(?:a )?(?:separate|different) institution",
    r"(?:refer|reach out|contact) (?:you )?to",
)
_DECLINE_RE = re.compile("|".join(DECLINE_PATTERNS), re.IGNORECASE)

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
    #: ``corpus_unchanged_at_endpoints`` as recorded: True/False, or None when
    #: the artifact predates the field (unknowable, not unstable).
    corpus_unchanged: Optional[bool] = None

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


def arm_identity(arm: "Arm") -> str:
    """A replicate's identity by resolved file, not by how it was spelled.

    ``arm.source`` keeps the caller's path so the report echoes what was typed;
    the same file reached through a relative path or a symlink would otherwise
    slip past the duplicate-replicate guard and be counted twice.
    """
    path, _, index = arm.source.rpartition("@")
    try:
        resolved = str(Path(path).resolve())
    except OSError:  # pragma: no cover - unresolvable path
        resolved = path
    return f"{resolved}@{index}"


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
        corpus_unchanged=(
            bool(raw["corpus_unchanged_at_endpoints"])
            if isinstance(raw.get("corpus_unchanged_at_endpoints"), bool)
            else (None if "corpus_unchanged_at_endpoints" not in raw else False)
        ),
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


def declared_sources(row: dict) -> Tuple[str, ...]:
    """The URLs a row declares as its expected sources, in order."""
    entries = row.get("reference_sources_metadata")
    if not isinstance(entries, list):
        return ()
    return tuple(
        str(entry.get("url"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("url") is not None
    )


def grading_input_diff(baseline: Arm, arm: Arm) -> List[str]:
    """Questions the two arms graded against different ground truth.

    G4 asks whether the arms used the identical question bank, and a bank is
    more than its question texts: ``reference_answer`` is the ground truth that
    ``answer_correctness`` and both context metrics are scored against, and the
    declared sources are the ground truth for source accuracy. Two runs of the
    same 109 texts against edited references measure different things, and the
    edit would otherwise read as a system delta.
    """
    problems = []
    for question in baseline.order:
        other = arm.rows.get(question)
        if other is None:
            continue
        mine = baseline.rows[question]
        if mine.get("reference_answer") != other.get("reference_answer"):
            problems.append(
                f"{question!r}: reference_answer differs "
                f"({str(mine.get('reference_answer'))[:60]!r} vs "
                f"{str(other.get('reference_answer'))[:60]!r})"
            )
        elif declared_sources(mine) != declared_sources(other):
            problems.append(
                f"{question!r}: declared sources differ "
                f"({list(declared_sources(mine))} vs "
                f"{list(declared_sources(other))})"
            )
    return problems


def require_same_question_sets(baseline: Arm, arms: Sequence[Arm]) -> None:
    """G4. No override exists on purpose."""
    for arm in arms:
        if arm is baseline:
            continue
        graded = grading_input_diff(baseline, arm)
        if graded:
            shown = "\n".join(f"  {line}" for line in graded[:3])
            more = f"\n  (+{len(graded) - 3} more)" if len(graded) > 3 else ""
            raise CompareError(
                f"G4 refused: {baseline.label} and {arm.label} asked the same "
                "questions but graded them against different ground truth, so a "
                "bank edit would read as a system delta. There is no override "
                f"for this.\n{shown}{more}",
                EXIT_GATE,
            )
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


def provenance_rows(
    arms: Sequence[Arm], anchors: Optional[Dict[str, dict]] = None
) -> List[dict]:
    """One row per provenance field, one column per arm.

    The three question counts are provenance too: "delta +0.03" means nothing
    without the denominator it was averaged over, and the anchors split is the
    difference between the bank's score and the bank's score with five tripwires
    stirred into it.
    """
    counts = anchors if anchors is not None else {}
    fields = [
        ("source", lambda a: a.source),
        ("questions asked", lambda a: question_counts(a, counts)["asked"]),
        ("anchors", lambda a: question_counts(a, counts)["anchors"]),
        ("bank rows", lambda a: question_counts(a, counts)["bank_rows"]),
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
    # Equal end-state fingerprints do not prove one corpus. Ingestion runs
    # continuously here, so an arm can straddle a re-ingest and score its
    # questions against two corpora while starting and finishing on the same
    # one; the harness records `corpus_unchanged_at_endpoints` for exactly that.
    # Absent (a legacy artifact) is unknowable, not unstable, and is allowed.
    unstable = [arm.label for arm in arms if arm.corpus_unchanged is False]
    if not unrecorded and len(distinct) <= 1 and not unstable:
        return {
            "id": "G3",
            "name": "one pinned corpus",
            "status": "pass",
            "detail": f"corpus_fingerprint {shown}",
        }
    if unstable:
        reason = (
            f"{', '.join(unstable)} recorded corpus_unchanged_at_endpoints as "
            "false, so the arm straddled a re-ingest and its questions were not "
            "all scored against the same documents"
        )
    elif unrecorded:
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
    # A negative sigma would make |mean| > 2*sigma trivially true and a NaN one
    # would make it never true. Both are rejected at parse; this is the second
    # lock, because the failure mode is a false SIGNIFICANT.
    if sigma is None or not is_finite(sigma) or sigma < 0:
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
            parsed = float(value)
        except ValueError:
            raise CompareError(
                f"--noise-floor value for {name} is not a number: {value!r}"
            ) from None
        # A standard deviation is finite and non-negative. Accepting anything
        # else hands the G7 threshold to the caller: a negative sigma makes
        # |mean| > 2*sigma always true (every delta past 2*SE becomes
        # SIGNIFICANT), a NaN one makes it never true.
        if not math.isfinite(parsed) or parsed < 0:
            raise CompareError(
                f"--noise-floor value for {name} must be a finite, non-negative "
                f"standard deviation; got {value!r}"
            )
        if name in sigmas:
            raise CompareError(
                f"--noise-floor names {name} more than once "
                f"({sigmas[name]:g} then {parsed:g}). The later value would "
                "silently become the G7 threshold; give it once."
            )
        sigmas[name] = parsed
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


def noise_gate_row(
    replicates: Sequence[Arm], arms: Sequence[Arm], questions: Sequence[str]
) -> dict:
    """Say what was actually checked about the replicates, and what was not.

    An earlier version of this row claimed the replicates were held to the same
    code and config identity "as the arms". They are not: identity is compared
    among the replicates only (a floor measured on the baseline's config is
    legitimately a different digest from the treatment arm's), and an unrecorded
    digest cannot be compared at all. A provenance line that overstates its own
    coverage is worse than none.
    """
    parts = [
        f"{len(replicates)} replicate arm(s) over the same {len(questions)} "
        "questions as the paired table; same bank, one corpus and no config "
        "divergence enforced"
    ]
    for field, read in (
        ("code_version.digest", lambda a: a.code_version_digest),
        ("config_version.digest", lambda a: _recorded(a.config_version.get("digest"))),
    ):
        recorded = sorted({read(arm) for arm in replicates if read(arm) is not None})
        missing = sum(1 for arm in replicates if read(arm) is None)
        if recorded and not missing:
            parts.append(f"one {field} across the replicates ({recorded[0]})")
        elif recorded:
            parts.append(
                f"{field}: {recorded[0]} where recorded, {missing} replicate(s) "
                "record none, so identity is unverified there"
            )
        else:
            parts.append(
                f"{field}: not recorded by any replicate, so it was not checked"
            )
    parts.append(
        "identity is compared among the replicates only, not against the "
        "comparison arms"
    )
    return {
        "id": "G2",
        "name": "noise floor",
        "status": "measured from replicates",
        "detail": ". ".join(parts),
    }


def check_noise_replicates(
    replicates: Sequence[Arm],
    baseline: Optional[Arm],
    *,
    allow_corpus_differs: bool,
    ignore_divergence: bool,
) -> None:
    """Hold the replicates to the same bar as the arms they will judge.

    sigma is not a number the report merely quotes: it *is* the G7 threshold.
    Pooling a stale or foreign run into it moves the bar, so a real regression
    can be talked down to "not distinguishable" (or a wobble talked up) by
    choosing the wrong replicate set. The floor is defined for **this bank on
    this corpus** (Procedure A), so that is what is checked.
    """
    if baseline is not None:
        expected = set(baseline.rows)
        for arm in replicates:
            extra = sorted(set(arm.rows) - expected)
            missing = sorted(expected - set(arm.rows))
            if not extra and not missing:
                continue
            detail = []
            if extra:
                detail.append(f"{len(extra)} not in the bank, first {extra[0]!r}")
            if missing:
                detail.append(f"{len(missing)} missing, first {missing[0]!r}")
            raise CompareError(
                f"noise replicate {arm.source} asked a different question set "
                f"than {baseline.label} ({'; '.join(detail)}). A noise floor is "
                "measured for one bank (Procedure A); sigma from another bank "
                "is not this comparison's threshold.",
                EXIT_GATE,
            )
    # Two copies of one run agree perfectly, so sigma comes out 0 and the
    # |mean| > 2*sigma half of G7 becomes vacuous. Distinct arms of one sweep
    # are still fine: they carry distinct `path@N` sources.
    seen: Dict[str, List[str]] = {}
    for arm in replicates:
        seen.setdefault(arm_identity(arm), []).append(arm.source)
    repeated = sorted(
        (spellings for spellings in seen.values() if len(spellings) > 1),
        key=lambda spellings: spellings[0],
    )
    if repeated:
        raise CompareError(
            "--noise-runs was given the same run twice ("
            + ", ".join(repeated[0])
            + "). One run counted twice is not two replicates: it would report "
            "sigma=0 and make the noise floor vacuous.",
            EXIT_USAGE,
        )
    scope = list(replicates) + ([baseline] if baseline is not None else [])
    unstable = [arm.source for arm in scope if arm.corpus_unchanged is False]
    if unstable and not allow_corpus_differs:
        raise CompareError(
            "G3 refused for the noise replicates: "
            f"{', '.join(unstable)} recorded corpus_unchanged_at_endpoints as "
            "false, so their questions were not all scored against one corpus. "
            "Pass --corpus-differs-by-design to accept them as an estimate.",
            EXIT_GATE,
        )
    values = {arm.source: arm.corpus_fingerprint for arm in scope}
    unrecorded = [source for source, value in values.items() if value is None]
    distinct = {value for value in values.values() if value is not None}
    if (unrecorded or len(distinct) > 1) and not allow_corpus_differs:
        shown = ", ".join(
            f"{source}={value or 'not recorded'}" for source, value in values.items()
        )
        raise CompareError(
            "G3 refused for the noise replicates: they do not share one pinned "
            f"corpus ({shown}). A noise floor measured on another corpus is not "
            "this corpus's floor. Pass --corpus-differs-by-design to accept it "
            "as an estimate (Procedure B).",
            EXIT_GATE,
        )
    # Replicates must be replicates of EACH OTHER. Two runs that provably
    # executed different code, or read a different configuration, describe two
    # different noise floors, and pooling them yields a threshold that describes
    # neither. Only a *recorded* disagreement is a refusal: every artifact
    # written before code-version stamping says `null` here, which is unknowable
    # rather than mismatched, and the gate row says so. The baseline is
    # deliberately outside this check — a floor measured on the baseline's config
    # is legitimately a different digest from the treatment arm's.
    for field, read in (
        ("code_version.digest", lambda a: a.code_version_digest),
        ("config_version.digest", lambda a: _recorded(a.config_version.get("digest"))),
    ):
        recorded = {read(arm) for arm in replicates if read(arm) is not None}
        if len(recorded) > 1:
            shown = ", ".join(
                f"{arm.source}={read(arm) or 'not recorded'}" for arm in replicates
            )
            raise CompareError(
                f"the noise replicates recorded more than one {field} "
                f"({shown}). Replicates of different {field.split('.')[0]}s are "
                "not replicates; measure the floor from repeats of one run "
                "(Procedure A).",
                EXIT_GATE,
            )
    diverged = {
        arm.source: arm.config_version.get("divergence_from_selected_file")
        for arm in replicates
        if arm.config_version.get("divergence_from_selected_file")
    }
    if diverged and not ignore_divergence:
        shown = "; ".join(
            f"{source}: {json.dumps(keys)}" for source, keys in diverged.items()
        )
        raise CompareError(
            "Procedure E stop for the noise replicates: a run that did not use "
            "the configuration it selected is not a replicate of anything "
            f"({shown}). Pass --ignore-config-divergence to include it anyway.",
            EXIT_DIVERGENCE,
        )


def noise_floor_from_runs(
    paths: Sequence[str],
    *,
    baseline: Optional[Arm] = None,
    questions: Optional[Sequence[str]] = None,
    allow_corpus_differs: bool = False,
    ignore_divergence: bool = False,
) -> Dict[str, float]:
    """sigma per metric from same-code replicates (Procedure A).

    Every arm of every file is one replicate, so a ``-cd`` sweep of the same
    config repeated N times works as well as N separate invocations. The means
    are **recomputed** rather than read from ``aggregate_<metric>``, because the
    recorded aggregate shares the denominator defect that ``<metric>_scored``
    exposes. ``check_noise_replicates`` gates the inputs first.

    ``questions`` is the population the deltas will be measured over — the bank
    rows, with the anchors excluded unless the caller asked for them. sigma has
    to describe the *same* population: a floor that still carries anchor-row
    variance would widen the threshold for a bank comparison that never included
    those rows, and a real bank regression could hide behind it.
    """
    replicates = load_noise_replicates(paths)
    check_noise_replicates(
        replicates,
        baseline,
        allow_corpus_differs=allow_corpus_differs,
        ignore_divergence=ignore_divergence,
    )
    return noise_floor_from_arms(
        replicates, questions, [baseline] if baseline is not None else []
    )


def load_noise_replicates(paths: Sequence[str]) -> List[Arm]:
    """Every arm of every ``--noise-runs`` file, as one replicate list."""
    replicates: List[Arm] = []
    for spec in paths:
        replicates.extend(load_arms([spec]))
    if len(replicates) < 2:
        raise CompareError(
            "--noise-runs needs at least two replicate arms to estimate sigma; "
            f"found {len(replicates)}",
            EXIT_USAGE,
        )
    return replicates


def sigma_population(
    arms: Sequence[Arm],
    replicates: Sequence[Arm],
    metric: str,
    questions: Sequence[str],
) -> List[str]:
    """The rows sigma may be averaged over for one metric.

    A replicate mean taken over its own finite subset describes a different
    quantity from the delta it is the threshold for. Restricting to rows every
    comparison arm *and* every replicate could score for this metric puts both
    on one population, and does so conservatively: it is a subset of every
    paired population, so no row that never entered a delta can widen or narrow
    the bar.
    """
    scope = list(arms) + list(replicates)
    return [
        question
        for question in questions
        if all(arm.is_scorable(question, metric) for arm in scope)
    ]


def noise_floor_from_arms(
    replicates: Sequence[Arm],
    questions: Optional[Sequence[str]] = None,
    arms: Sequence[Arm] = (),
) -> Dict[str, float]:
    """sigma per metric over already-gated replicates."""
    sigmas: Dict[str, float] = {}
    for metric in METRICS:
        if questions is None:
            population: Optional[Sequence[str]] = None
        else:
            population = sigma_population(arms, replicates, metric, questions)
            if not population:
                continue
        means = [
            mean
            for mean in (
                recomputed_aggregate(arm, metric, population)["mean"]
                for arm in replicates
            )
            if mean is not None
        ]
        if len(means) < 2:
            continue
        sigma = statistics.stdev(means)
        if sigma == 0:
            # Identical replicate means are not evidence of zero run-to-run
            # noise; they make the |mean| > 2*sigma half of G7 vacuous, so any
            # delta clearing 2*SE would be announced as SIGNIFICANT.
            raise CompareError(
                f"the --noise-runs replicates give sigma = 0 for {metric}: "
                "their recomputed means are identical, which measures no noise "
                "floor at all rather than a floor of zero. Use replicates from "
                "separate runs, or declare a floor with --noise-floor.",
                EXIT_GATE,
            )
        sigmas[metric] = sigma
    return sigmas


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
        problems: List[str] = []
        if parsed is None:
            problems.append("not reported")
        else:
            if parsed[0] > counted["finite"]:
                problems.append("OVER-REPORTED")
            elif parsed[0] < counted["finite"]:
                problems.append("UNDER-REPORTED")
            # The denominator says how much of the bank was ever eligible, so a
            # wrong one misstates the coverage even when the numerator agrees.
            if parsed[1] != counted["total"]:
                problems.append("DENOMINATOR MISMATCH")
        flag = "; ".join(problems) or "ok"
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
    # Every row that DECLARES a source is in the denominator, whatever its
    # status. This mirrors the producer: `Benchmarker._source_scorable_count`
    # counts bank rows with `sources` regardless of outcome ("a failed retrieval
    # still registers as a miss rather than quietly vanishing from the
    # average"), while `source_hits(None, ...)` contributes zero hits for a
    # degraded row. A degraded row therefore carries no `matched` keys, and
    # `all(...)` scores it as the miss the artifact already counted. Only a
    # zero-source row (the should_refuse anchor) is excluded from both sides.
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

        def timings(questions: Sequence[str]) -> List[float]:
            return [
                float(arm.rows[q]["time_elapsed"])
                for q in questions
                if is_finite(arm.rows[q].get("time_elapsed"))
            ]

        ordered = timings(arm.order)
        # Slice the RUN ORDER first, then keep the finite timings. A failed
        # first question carries no time_elapsed, so filtering first would drop
        # question 2 — the first genuinely warm request — instead of question 1.
        warm = timings(arm.order[1:])
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


# --- G8: the anchors ---------------------------------------------------------


def _project_root_on_path() -> None:
    """Make ``src`` importable from a plain ``python scripts/...`` run.

    The two project imports below happen *inside* their functions on purpose:
    a module-level import would be sorted above this shim by isort and the
    script would then only work when it was already run from the repo root.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def _normalize_bank():
    """The harness's own bank normalizer, without the package side effects.

    ``src/utils/__init__.py`` imports the config service, which imports
    ``psycopg2`` — so the ordinary ``from src.utils.benchmark_schema import ...``
    needs a database driver on a host that is only reading finished artifacts.
    ``benchmark_schema.py`` is itself pure stdlib, so it is loaded straight from
    its own file when the package import is unavailable. Same file, same
    function: this is not a copy of the dialect rules.
    """
    _project_root_on_path()
    try:
        from src.utils.benchmark_schema import normalize_bank

        return normalize_bank
    except ImportError:
        pass
    source = REPO_ROOT / "src" / "utils" / "benchmark_schema.py"
    spec = importlib.util.spec_from_file_location("archi_benchmark_schema", source)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
        raise CompareError(f"cannot load the bank normalizer from {source}", EXIT_USAGE)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - environment, not logic
        raise CompareError(
            f"cannot load the bank normalizer from {source}: {exc}", EXIT_USAGE
        ) from None
    return module.normalize_bank


def anchor_questions(path: str, *, required: bool = True) -> Dict[str, dict]:
    """The anchors, keyed by question text.

    Read through the harness's own ``normalize_bank`` so an anchors file written
    in the legacy dialect (``question``/``answer``) matches the same rows the
    harness would have asked. The ``anchor_type`` used everywhere downstream
    comes from **this file**, never from the artifact row: the FASRC bank sets
    ``anchor_type`` on all 109 rows, so the field cannot tell an anchor from a
    bank question.
    """
    normalize_bank = _normalize_bank()
    file = Path(path)
    if not file.exists():
        if required:
            raise CompareError(f"anchors file not found: {file}", EXIT_USAGE)
        return {}
    try:
        rows = json.loads(file.read_text())
    except (OSError, ValueError) as exc:
        raise CompareError(
            f"cannot read anchors file {file}: {exc}", EXIT_USAGE
        ) from None
    normalized = normalize_bank(rows)
    if not isinstance(normalized, list):
        raise CompareError(f"{file} is not a list of anchor rows", EXIT_USAGE)
    anchors: Dict[str, dict] = {}
    for row in normalized:
        if isinstance(row, dict) and isinstance(row.get("user_input"), str):
            anchors[row["user_input"]] = row
    return anchors


def bank_questions(
    arm: Arm, anchors: Dict[str, dict], *, include_anchors: bool
) -> List[str]:
    """The questions the bank aggregates cover, in the arm's run order.

    Anchors are excluded by default (Gap 3). An anchor whose text also appears in
    the bank is asked once and stays an anchor: the harness dedupes on exact
    ``user_input`` and keeps the bank row, so the artifact holds one row for it —
    counting that row in the bank would put a tripwire back into the number the
    tripwire is supposed to guard.
    """
    if include_anchors:
        return list(arm.order)
    return [question for question in arm.order if question not in anchors]


def question_counts(arm: Arm, anchors: Dict[str, dict]) -> dict:
    """asked / anchors / bank rows — the denominators, printed with the report."""
    matched = sum(1 for question in arm.order if question in anchors)
    return {
        "asked": len(arm.order),
        "anchors": matched,
        "bank_rows": len(arm.order) - matched,
    }


def refusal_verdict(answer: Any) -> Tuple[str, str]:
    """Judge a ``should_refuse`` answer, and quote what was judged.

    A heuristic, and labelled as one everywhere it is printed: PASS when the
    answer declines, FAIL when it is long and confident about a system there is
    no documentation for, REVIEW otherwise. The snippet is returned so the reader
    can overrule it without opening the artifact.
    """
    if not isinstance(answer, str) or not answer.strip():
        return "REVIEW", ""
    snippet = answer[:REFUSAL_SNIPPET_CHARS]
    if _DECLINE_RE.search(answer):
        return "PASS", snippet
    if len(answer) >= CONFIDENT_ANSWER_CHARS:
        return "FAIL", snippet
    return "REVIEW", snippet


def _qa_item_pass(qa_run: Optional[dict], item_id: Optional[str]) -> Optional[float]:
    if not qa_run or not item_id:
        return None
    item = qa_run["items"].get(item_id)
    return None if item is None else item.get("item_pass_rate")


def anchor_block(
    baseline: Arm,
    arms: Sequence[Arm],
    anchors: Dict[str, dict],
    sigmas: Dict[str, float],
    qa_runs: Dict[str, dict],
) -> List[dict]:
    """The tripwire track: per anchor, per arm, read by anchor type.

    ``easy_retrieve`` raises an ALARM on a drop bigger than the metric's noise
    floor (or than 0.10 when no floor was measured) — a fall there means
    retrieval broke, not that the change is subtle. ``reasoning`` is reported as
    a trend and never alarms; two questions cannot establish anything.
    ``should_refuse`` is a binary assertion judged from the answer text, or from
    the QA item's pass when a ``--qa-run`` covers it — the QA run actually checks
    the required atoms, so it outranks the phrase heuristic.
    """
    block: List[dict] = []
    for question, anchor in anchors.items():
        if question not in baseline.rows:
            continue
        anchor_type = anchor.get("anchor_type") or "unassigned"
        entry = {"question": question, "anchor_type": anchor_type, "arms": {}}
        for arm in arms:
            row = arm.rows.get(question)
            if row is None:
                continue
            metrics = {
                metric: float(row[metric])
                for metric in METRICS
                if is_finite(row.get(metric))
            }
            deltas: Dict[str, float] = {}
            alarms: List[str] = []
            thresholds: Dict[str, float] = {}
            if arm is not baseline:
                for metric, value in metrics.items():
                    if not is_finite(baseline.value(question, metric)):
                        continue
                    delta = value - float(baseline.value(question, metric))
                    deltas[metric] = delta
                    if anchor_type != "easy_retrieve" or delta >= 0:
                        continue
                    sigma = sigmas.get(metric)
                    threshold = (
                        sigma if sigma is not None else ANCHOR_DROP_WITHOUT_SIGMA
                    )
                    if -delta > threshold:
                        alarms.append(metric)
                        thresholds[metric] = threshold
            arm_entry = {
                "metrics": metrics,
                "deltas": deltas,
                "alarms": alarms,
                # A tripwire that could not be scored is not a tripwire that
                # held: a degraded or all-non-finite anchor row raises no alarm
                # simply because there was nothing to compare, and G8 would
                # otherwise report the anchors as holding.
                "unscored": row.get("status", "ok") != "ok"
                or (
                    anchor_type != "should_refuse"
                    and not metrics
                    and arm is not baseline
                ),
                # The threshold that fired is printed with the alarm. sigma is a
                # RUN-mean noise floor and this is ONE question, whose own
                # spread is several times larger, so the bar is tight and an
                # alarm is a prompt to look, not a verdict.
                "alarm_thresholds": thresholds,
                "refusal": None,
                "refusal_source": None,
            }
            if anchor_type == "should_refuse":
                heuristic, snippet = refusal_verdict(row.get("answer"))
                arm_entry.update(
                    {
                        "refusal": heuristic,
                        "refusal_source": "heuristic",
                        "answer_snippet": snippet,
                    }
                )
                # Reach for the QA id only when a run could answer with it:
                # deriving it imports the QA dataset module, which pulls the
                # whole evaluation stack (mcp, ijson). A plain comparison must
                # not need those installed.
                run = qa_runs.get(arm.label)
                pass_rate = _qa_item_pass(run, qa_item_id(row)) if run else None
                if pass_rate is not None:
                    arm_entry["refusal"] = (
                        "PASS"
                        if pass_rate >= 1.0
                        else "FAIL" if pass_rate <= 0.0 else "REVIEW"
                    )
                    arm_entry["refusal_source"] = "qa"
                    arm_entry["qa_item_pass_rate"] = pass_rate
            entry["arms"][arm.label] = arm_entry
        block.append(entry)
    return block


# --- slices ------------------------------------------------------------------


def slice_block(
    baseline: Arm,
    arms: Sequence[Arm],
    questions: Sequence[str],
    sigmas: Dict[str, float],
) -> List[dict]:
    """Paired deltas cut by a bank field, for fields every arm actually carries.

    A field present on one side only is not a slice, it is a difference between
    artifacts, so it is skipped rather than reported against a missing column.
    Slices below ``SMALL_SLICE`` rows are marked directional: at n=6 a single
    question swinging moves the mean by 0.17.

    Membership needs **every** arm to give the question the same value. G4
    compares question text, so a bank edit that re-labelled a question's
    ``difficulty`` between the two runs passes the gate; grouping by the
    baseline's label alone would then file the treatment's ``hard`` row under
    ``easy``. Disagreeing questions are dropped from every slice of that field
    and counted, so the loss is visible rather than silent.
    """
    block: List[dict] = []
    for field in SLICE_FIELDS:
        if not all(arm.has_metric(field) for arm in arms):
            continue
        groups: Dict[str, List[str]] = {}
        mismatched = 0
        for question in questions:
            value = baseline.rows.get(question, {}).get(field)
            if not (isinstance(value, str) and value):
                continue
            if any(arm.rows.get(question, {}).get(field) != value for arm in arms):
                mismatched += 1
                continue
            groups.setdefault(value, []).append(question)
        for value, members in sorted(groups.items()):
            for arm in arms:
                if arm is baseline:
                    continue
                for metric in METRICS:
                    if not (baseline.has_metric(metric) and arm.has_metric(metric)):
                        continue
                    summary = summarize_deltas(
                        paired_deltas(baseline, arm, metric, members)
                    )
                    if summary["n"] == 0:
                        continue
                    sigma = sigmas.get(metric)
                    directional = summary["n"] < SMALL_SLICE
                    block.append(
                        {
                            "field": field,
                            "value": value,
                            "metric": metric,
                            "arm": arm.label,
                            "n": summary["n"],
                            "mean": summary["mean"],
                            "se": summary["se"],
                            "sigma": sigma,
                            # A small slice never claims significance. G7's
                            # 2*SE test is arithmetic, and at n=2 the stdev of
                            # two numbers will clear it whenever they happen to
                            # agree — two same-code runs printed SIGNIFICANT on
                            # the 2-row should_refuse slice. Direction is all a
                            # slice this size can honestly report.
                            "verdict": (
                                f"directional (n={summary['n']} < {SMALL_SLICE})"
                                if directional
                                else verdict(summary, sigma)
                            ),
                            "directional": directional,
                            "excluded_mismatched": mismatched,
                        }
                    )
    return block


# --- the optional QA join -----------------------------------------------------


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def qa_item_id(row: dict) -> Optional[str]:
    """The ``archi eval qa`` item id for a benchmark row, or None.

    The id is content-derived from the newline-normalized question and reference
    answer — the same derivation the RAGAS-bank converter uses — so the two
    stacks agree without either of them storing a mapping. A row whose reference
    the harness wrote as the placeholder ``"N/A"`` has no ground truth and
    therefore no QA item.
    """
    question = row.get("question")
    reference = row.get("reference_answer")
    if not isinstance(question, str) or not isinstance(reference, str):
        return None
    if reference.strip() in {"", "N/A"}:
        return None
    _project_root_on_path()
    try:
        from src.evaluation.qa.dataset import derive_item_id
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise CompareError(
            f"the QA join needs the project importable: {exc}", EXIT_USAGE
        ) from None
    return derive_item_id(_normalize_newlines(question), _normalize_newlines(reference))


def _read_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise CompareError(f"{path}:{number} is not valid JSON: {exc}", EXIT_USAGE)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_qa_run(directory: str) -> dict:
    """Read one ``archi eval qa`` run directory into an item-keyed index."""
    base = Path(directory)
    summary_path = base / "summary.json"
    if not summary_path.exists():
        raise CompareError(f"{base} has no summary.json", EXIT_USAGE)
    try:
        summary = json.loads(summary_path.read_text())
    except (OSError, ValueError) as exc:
        raise CompareError(f"cannot read {summary_path}: {exc}", EXIT_USAGE) from None
    items = {
        item["item_id"]: item
        for item in summary.get("items") or []
        if isinstance(item, dict) and isinstance(item.get("item_id"), str)
    }
    durations: Dict[str, List[float]] = {}
    for row in _read_jsonl(base / "answers.jsonl"):
        if isinstance(row.get("item_id"), str) and is_finite(row.get("duration_ms")):
            durations.setdefault(row["item_id"], []).append(float(row["duration_ms"]))
    evaluations: Dict[str, List[dict]] = {}
    for row in _read_jsonl(base / "evaluation_results.jsonl"):
        if isinstance(row.get("item_id"), str):
            evaluations.setdefault(row["item_id"], []).append(row)
    return {
        "path": str(base),
        "overall_attempt_pass_rate": summary.get("overall_attempt_pass_rate"),
        "macro_mean_item_pass_rate": summary.get("macro_mean_item_pass_rate"),
        "macro_mean_scored_attempt_atom_score": summary.get(
            "macro_mean_scored_attempt_atom_score"
        ),
        "items": items,
        "durations": durations,
        "evaluations": evaluations,
    }


def qa_block(
    baseline: Arm, arms: Sequence[Arm], qa_runs: Dict[str, dict]
) -> Optional[dict]:
    """Per-arm QA rates and latencies, joined to the bank by derived item id."""
    if not qa_runs:
        return None
    # question text -> the QA facts for it, per arm. Pairing stays on question
    # text (G5); the item id is only how the QA artifacts are looked up.
    joined: Dict[str, Dict[str, dict]] = {}
    rows = []
    for arm in arms:
        run = qa_runs.get(arm.label)
        if run is None:
            continue
        matched: Dict[str, dict] = {}
        for question in arm.order:
            item_id = qa_item_id(arm.rows[question])
            if not item_id or item_id not in run["items"]:
                continue
            scores = [
                float(result["atom_score"])
                for result in run["evaluations"].get(item_id, [])
                if is_finite(result.get("atom_score"))
            ]
            matched[question] = {
                "item_id": item_id,
                "item_pass_rate": run["items"][item_id].get("item_pass_rate"),
                "atom_score": statistics.fmean(scores) if scores else None,
                "durations": run["durations"].get(item_id, []),
            }
        joined[arm.label] = matched
        durations = [d for facts in matched.values() for d in facts["durations"]]
        scores = [
            facts["atom_score"]
            for facts in matched.values()
            if facts["atom_score"] is not None
        ]
        item_rates = [
            float(facts["item_pass_rate"])
            for facts in matched.values()
            if is_finite(facts["item_pass_rate"])
        ]
        rows.append(
            {
                "arm": arm.label,
                "path": run["path"],
                "joined": len(matched),
                "asked": len(arm.order),
                # Joined subset: these describe the questions this arm asked.
                "joined_mean_item_pass_rate": (
                    statistics.fmean(item_rates) if item_rates else None
                ),
                "mean_atom_score": statistics.fmean(scores) if scores else None,
                "mean_duration_ms": statistics.fmean(durations) if durations else None,
                "p90_duration_ms": percentile(durations, 90),
                # Whole run, straight from summary.json. Kept because it is the
                # run's own headline, and prefixed because a QA run may cover
                # items this benchmark never asked — reading these as the joined
                # questions' rates would attribute unrelated scores to the arm.
                "run_overall_attempt_pass_rate": run["overall_attempt_pass_rate"],
                "run_macro_mean_item_pass_rate": run["macro_mean_item_pass_rate"],
                "run_macro_mean_scored_attempt_atom_score": run[
                    "macro_mean_scored_attempt_atom_score"
                ],
            }
        )

    paired: List[dict] = []
    base_items = joined.get(baseline.label, {})
    for arm in arms:
        if arm is baseline or not joined.get(arm.label):
            continue
        arm_items = joined[arm.label]
        shared = [question for question in base_items if question in arm_items]
        for name in ("item_pass_rate", "atom_score"):
            deltas = [
                float(arm_items[question][name]) - float(base_items[question][name])
                for question in shared
                if is_finite(base_items[question][name])
                and is_finite(arm_items[question][name])
            ]
            summary = summarize_deltas(deltas)
            if summary["n"]:
                paired.append(
                    {
                        "arm": arm.label,
                        "baseline": baseline.label,
                        "metric": name,
                        "n": summary["n"],
                        "mean": summary["mean"],
                        "se": summary["se"],
                    }
                )
    return {"arms": rows, "paired": paired}


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
            # The two arm means sit beside the delta, so they must share its
            # denominator: averaged over rows the other arm could not score they
            # would not reconcile with it, and can point the other way.
            paired = [
                question
                for question in questions
                if baseline.is_scorable(question, metric)
                and arm.is_scorable(question, metric)
            ]
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
                    "baseline_mean": recomputed_aggregate(baseline, metric, paired)[
                        "mean"
                    ],
                    "arm_mean": recomputed_aggregate(arm, metric, paired)["mean"],
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


def _cell(value: str) -> str:
    """One table cell that cannot break out of its row.

    A question may legitimately contain a pipe (``a | b`` in a submit script) or
    a newline — the committed artifacts hold multi-line questions — and either
    one silently split the row into extra columns or extra physical lines,
    corrupting the report a decision is read from.
    """
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> List[str]:
    lines = ["| " + " | ".join(_cell(h) for h in headers) + " |"]
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
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

    out += ["## Anchors", ""]
    out.append(
        f"Tripwires, matched by question text against `{report['anchors_path']}`. "
        "The artifact's `anchor_type` is the bank's own field and is set on every "
        "row, so it cannot identify an anchor; the type below comes from the "
        "anchors file. "
        + (
            "These rows are averaged into the bank above (--include-anchors-in-bank)."
            if report["anchors_in_bank"]
            else "These rows are excluded from the bank aggregates above (Gap 3)."
        )
    )
    out.append("")
    out.append(
        "An `easy_retrieve` ALARM fires when one question drops further than the "
        "metric's noise floor (0.10 when none was measured). That floor is a "
        "*run-mean* sigma and this is *one question*, whose own spread is several "
        "times larger — so the alarm is a prompt to open the answer, not a verdict. "
        "`reasoning` anchors are a trend line and never alarm; two questions "
        "establish nothing. `should_refuse` is a phrase heuristic unless a "
        "`--qa-run` covers the item, in which case the QA item pass decides."
    )
    out.append("")
    if report["anchors"]:
        anchor_rows = []
        for entry in report["anchors"]:
            for label in labels:
                arm_entry = entry["arms"].get(label)
                if arm_entry is None:
                    continue
                deltas = (
                    ", ".join(
                        f"{metric} {_signed(value, 3)}"
                        for metric, value in sorted(arm_entry["deltas"].items())
                    )
                    or "baseline"
                )
                refusal = arm_entry.get("refusal") or ""
                if refusal:
                    refusal += f" ({arm_entry.get('refusal_source')})"
                anchor_rows.append(
                    [
                        entry["question"][:70],
                        entry["anchor_type"],
                        label,
                        deltas,
                        refusal,
                        ", ".join(
                            f"ALARM {metric} ({_signed(arm_entry['deltas'][metric], 3)}"
                            f" past {_fmt(arm_entry['alarm_thresholds'][metric], 3)})"
                            for metric in arm_entry["alarms"]
                        ),
                    ]
                )
        out += _table(
            ["anchor", "type", "arm", "deltas vs baseline", "should_refuse", "alarms"],
            anchor_rows,
        )
        snippets = [
            f"- `{label}` on {entry['question'][:60]!r}: "
            f"{entry['arms'][label].get('answer_snippet', '')!r}"
            for entry in report["anchors"]
            for label in labels
            if entry["anchor_type"] == "should_refuse"
            and label in entry["arms"]
            and entry["arms"][label].get("answer_snippet")
        ]
        if snippets:
            out += ["", "Answers judged (first 160 characters):"] + snippets
    else:
        out.append("No anchor question from that file appears in these arms.")
    out.append("")

    out += ["## Slices", ""]
    if report["slices"]:
        out += _table(
            ["field", "value", "metric", "arm", "n", "delta", "SE", "verdict", "note"],
            [
                [
                    row["field"],
                    row["value"],
                    row["metric"],
                    row["arm"],
                    str(row["n"]),
                    _signed(row["mean"]),
                    _fmt(row["se"]),
                    row["verdict"],
                    "directional (small slice)" if row["directional"] else "",
                ]
                for row in report["slices"]
            ],
        )
        dropped = {
            row["field"]: row["excluded_mismatched"]
            for row in report["slices"]
            if row["excluded_mismatched"]
        }
        for name, count in sorted(dropped.items()):
            out.append("")
            out.append(
                f"{count} question(s) carry a different `{name}` in different "
                "arms and were dropped from every slice of that field — the arms "
                "were scored against different labels for the same question."
            )
    else:
        out.append(
            "No slice field (`"
            + "`, `".join(SLICE_FIELDS)
            + "`) is present in every arm."
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

    if report.get("qa"):
        out += ["## QA runs", ""]
        out.append(
            "Joined to the bank by derived item id (`qa-<sha256 of the "
            "newline-normalized question + reference answer>`); rows whose "
            "reference the harness wrote as `N/A` carry no ground truth and are "
            "skipped."
        )
        for row in report["qa"]["arms"]:
            out.append(
                f"- `{row['arm']}`: {row['joined']} joined of {row['asked']} "
                f"questions, from `{row['path']}`"
            )
        out.append("")
        out.append(
            "The **joined** columns cover only the questions this arm asked; the "
            "**whole-run** columns are `summary.json`'s own headline over every "
            "item in the run, which may include items this benchmark never asked."
        )
        out.append("")
        out += _table(
            [
                "arm",
                "joined item pass rate",
                "joined atom score",
                "joined mean ms",
                "joined p90 ms",
                "whole-run attempt pass rate",
                "whole-run item pass rate",
                "whole-run atom score",
            ],
            [
                [
                    row["arm"],
                    _fmt(row["joined_mean_item_pass_rate"]),
                    _fmt(row["mean_atom_score"]),
                    _fmt(row["mean_duration_ms"], 0),
                    _fmt(row["p90_duration_ms"], 0),
                    _fmt(row["run_overall_attempt_pass_rate"]),
                    _fmt(row["run_macro_mean_item_pass_rate"]),
                    _fmt(row["run_macro_mean_scored_attempt_atom_score"]),
                ]
                for row in report["qa"]["arms"]
            ],
        )
        if report["qa"]["paired"]:
            out.append("")
            out += _table(
                ["arm", "metric", "n", "delta", "SE"],
                [
                    [
                        row["arm"],
                        row["metric"],
                        str(row["n"]),
                        _signed(row["mean"]),
                        _fmt(row["se"]),
                    ]
                    for row in report["qa"]["paired"]
                ],
            )
        out.append("")
    return "\n".join(out).rstrip("\n")


def g8_gate(
    anchor_entries: Sequence[dict], paired: Sequence[dict], baseline_label: str
) -> dict:
    """G8 as a gate row, not just a section further down the page.

    G8 is "the anchors hold, and no other metric regressed by more than one
    noise-floor unit". Both halves were computed already but lived only in
    disconnected sections, where a reader looking at the gate table would see
    silence and read it as a pass. It stays a *reported* verdict rather than a
    non-zero exit: an anchor failing means "do not ship the change", not "this
    comparison is invalid", and the report is the evidence for that call.

    The gate judges the **candidate** arms only. Scoring the baseline's own
    anchor failures into it inverts the decision it exists to support: a run
    that repairs a broken baseline's refusal anchor would be told "do not ship"
    for a defect it fixed. A failing baseline is still reported, separately,
    because it changes how the deltas beside it should be read.
    """
    if not anchor_entries:
        return {
            "id": "G8",
            "name": "anchors and guard metrics",
            "status": "not evaluated",
            "detail": (
                "none of the anchor questions were asked in these arms. A run "
                "from before the anchors were added is a different graded set "
                "(G4/G6) — re-baseline rather than reading this as a pass."
            ),
        }
    failures, alarms, baseline_failures, unscored = [], [], [], []
    for entry in anchor_entries:
        for label, arm_entry in entry["arms"].items():
            named = f"{label} on {entry['question'][:50]!r}"
            if arm_entry.get("refusal") == "FAIL":
                (baseline_failures if label == baseline_label else failures).append(
                    named
                )
            if arm_entry.get("unscored"):
                unscored.append(named)
            # `alarms` is only ever populated for a non-baseline arm (it is a
            # delta against the baseline), but the guard makes that explicit.
            if label == baseline_label:
                continue
            for metric in arm_entry["alarms"]:
                alarms.append(f"{label} {entry['question'][:40]!r} {metric}")
    regressions = [
        f"{row['arm']} {row['metric']} {row['mean']:+.4f} past sigma "
        f"{row['sigma']:.4f}"
        for row in paired
        if row["mean"] is not None
        and row["sigma"] is not None
        and row["mean"] < 0
        and -row["mean"] > row["sigma"]
    ]
    detail = []
    if failures:
        detail.append("should_refuse FAIL: " + "; ".join(failures))
    if alarms:
        detail.append("easy_retrieve ALARM: " + "; ".join(alarms))
    if regressions:
        detail.append("regressed past one noise-floor unit: " + "; ".join(regressions))
    if unscored:
        detail.append(
            "unscored anchor(s) — could not be checked, so not held: "
            + "; ".join(unscored)
        )
    if not detail:
        detail.append(
            f"{len(anchor_entries)} anchor(s) held and no metric regressed past "
            "one noise-floor unit"
        )
    if baseline_failures:
        detail.append(
            "not counted against this gate, but note the baseline itself fails "
            "should_refuse: " + "; ".join(baseline_failures)
        )
    status = (
        "FAIL" if failures else "ALARM" if alarms or regressions or unscored else "pass"
    )
    return {
        "id": "G8",
        "name": "anchors and guard metrics",
        "status": status,
        "detail": ". ".join(detail),
    }


def build_report(
    arms: Sequence[Arm],
    baseline: Arm,
    *,
    gates: Sequence[dict],
    sigmas: Dict[str, float],
    questions: Sequence[str],
    anchors: Dict[str, dict],
    anchors_path: str,
    anchors_in_bank: bool,
    qa_runs: Optional[Dict[str, dict]] = None,
) -> dict:
    paired = paired_block(baseline, arms, questions, sigmas)
    anchor_entries = anchor_block(baseline, arms, anchors, sigmas, qa_runs or {})
    return {
        "baseline": baseline.label,
        "arms": [
            {"label": arm.label, "source": arm.source, "questions": len(arm.rows)}
            for arm in arms
        ],
        "counts": question_counts(baseline, anchors),
        "paired_question_count": len(questions),
        "anchors_path": anchors_path,
        "anchors_in_bank": anchors_in_bank,
        "provenance": provenance_rows(arms, anchors),
        "gates": list(gates) + [g8_gate(anchor_entries, paired, baseline.label)],
        "noise_floor": dict(sigmas),
        "paired": paired,
        "scored_counts": {arm.label: scored_counts(arm) for arm in arms},
        "sources": source_block(arms),
        "anchors": anchor_entries,
        "slices": slice_block(baseline, arms, questions, sigmas),
        "timing": timing_block(arms),
        "qa": qa_block(baseline, arms, qa_runs or {}),
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
    parser.add_argument(
        "--qa-run",
        action="append",
        default=[],
        metavar="LABEL=RUN_DIR",
        help="an `archi eval qa` run directory to join to an arm (repeatable)",
    )
    parser.add_argument("--json", metavar="PATH", help="write the report as JSON")
    return parser


def parse_qa_run_specs(specs: Sequence[str], arms: Sequence[Arm]) -> Dict[str, dict]:
    """Resolve ``LABEL=RUN_DIR`` pairs against the loaded arms."""
    labels = {arm.label for arm in arms}
    runs: Dict[str, dict] = {}
    for spec in specs:
        label, sep, directory = spec.partition("=")
        if not sep or not label or not directory:
            raise CompareError(
                f"--qa-run expects LABEL=RUN_DIR, got {spec!r}; "
                f"labels are {', '.join(sorted(labels))}",
                EXIT_USAGE,
            )
        if label not in labels:
            raise CompareError(
                f"--qa-run names no such arm: {label!r}; "
                f"labels are {', '.join(sorted(labels))}",
                EXIT_USAGE,
            )
        if label in runs:
            # The QA item pass overrides the refusal heuristic and feeds G8, so
            # last-write-wins could flip a candidate from FAIL to PASS with
            # nothing reporting the ambiguity.
            raise CompareError(
                f"--qa-run was given twice for {label!r} "
                f"({runs[label]['path']} and {directory}); give one run per arm",
                EXIT_USAGE,
            )
        runs[label] = load_qa_run(directory)
    return runs


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

    # The default anchors file is tracked in the repository. If it is absent the
    # checkout or package is incomplete, and continuing with G8 reported as
    # "not evaluated" would quietly weaken the ship gate on a broken
    # environment. A deliberately anchor-free comparison passes --anchors at a
    # file holding an empty list.
    anchors = anchor_questions(args.anchors)
    qa_runs = parse_qa_run_specs(args.qa_run, arms)
    questions = bank_questions(
        baseline, anchors, include_anchors=args.include_anchors_in_bank
    )

    sigmas: Dict[str, float] = {}
    if args.noise_runs:
        replicates = load_noise_replicates(args.noise_runs)
        check_noise_replicates(
            replicates,
            baseline,
            allow_corpus_differs=args.corpus_differs_by_design,
            ignore_divergence=args.ignore_config_divergence,
        )
        sigmas.update(noise_floor_from_arms(replicates, questions, arms))
        gates.append(noise_gate_row(replicates, arms, questions))
    if args.noise_floor:
        declared = parse_noise_floor(args.noise_floor)
        # sigma IS the G7 threshold. Quietly letting a command-line value
        # replace one the tool just measured would let the bar be lowered after
        # the fact, with nothing in the report saying so. Declaring a metric the
        # replicates could not measure is still allowed and useful.
        collisions = sorted(set(declared) & set(sigmas))
        if collisions:
            raise CompareError(
                "--noise-floor and --noise-runs both give a sigma for "
                f"{', '.join(collisions)}. Measured: "
                + ", ".join(f"{m}={sigmas[m]:.4f}" for m in collisions)
                + "; declared: "
                + ", ".join(f"{m}={declared[m]:.4f}" for m in collisions)
                + ". Pick one — a declared value silently replacing a measured "
                "one would move the significance threshold with nothing in the "
                "report to show it.",
                EXIT_USAGE,
            )
        sigmas.update(declared)

    report = build_report(
        arms,
        baseline,
        gates=gates,
        sigmas=sigmas,
        questions=questions,
        anchors=anchors,
        anchors_path=args.anchors,
        anchors_in_bank=args.include_anchors_in_bank,
        qa_runs=qa_runs,
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
