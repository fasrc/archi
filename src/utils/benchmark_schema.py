"""Question-bank dialect + RAGAS metric eligibility for the benchmark harness.

Pure, ragas-free helpers (unit-testable without the benchmark-only ragas dep,
which is absent from the unit-test env). They sit beside the run-status helpers
in ``benchmark_resilience`` but own a different axis: the *dialect/schema*
contract and per-metric *data-emptiness* eligibility, as opposed to
failed/degraded run-status resilience.

Dialect
    ``normalize_record`` / ``normalize_bank`` map the legacy authoring dialect
    (``question`` / ``answer`` / ``contexts``) onto ragas 0.3.5's modern schema
    (``user_input`` / ``reference`` / ``retrieved_contexts``), preserving archi
    extension fields (``sources`` / ``source_match_field`` / ``anchor_type`` /
    ``notes``). The single highest-risk mapping is ``answer -> reference`` (the
    ground truth), NEVER ``answer -> response`` (which is the agent's run-time
    answer).

Validation vs eligibility
    ``required_fields_for_modes`` is *schema* validation and is deliberately
    SEPARATE from metric eligibility: ``user_input`` is always required and
    SOURCES mode adds ``sources``, but RAGAS mode does NOT require ``reference``
    at load — an empty ``reference`` is a valid draft row that per-metric
    eligibility (below) excludes only from the context metrics.

Per-metric eligibility
    ``row_is_eligible`` / ``score_metrics_per_eligibility`` score each RAGAS
    metric over its OWN eligible subset (context metrics drop empty-``reference``
    rows) so each aggregate is a mean over real rows — not a skip-NaN mean over a
    hidden partial denominator — and report each metric's scored denominator
    (``n_scored / n_total``). Scores attach back to their originating question by
    per-question key, never positionally, so excluding one row never shifts
    another row's score. The single ragas call is INJECTED as ``score_fn`` so all
    of this stays pure and fully unit-tested; the caller
    (``service_benchmark.get_ragas_results``) supplies the ragas-touching closure.
"""

from __future__ import annotations

import json
import math
import os
import posixpath
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Mirror of the Jinja defaults in src/cli/templates/base-config.yaml so the
# preflight judges a config by the SAME effective settings the rendered
# deployment runs — e.g. a config that omits ``modes`` still enters
# ``[SOURCES, RAGAS]`` and therefore must carry ``sources``. Keep in sync with
# that template.
DEFAULT_MODES: List[str] = ["SOURCES", "RAGAS"]
DEFAULT_QUERIES_PATH: str = "queries"
DEFAULT_ENABLED_METRICS: List[str] = [
    "answer_relevancy",
    "faithfulness",
    "context_precision",
    "context_recall",
]
DEFAULT_ANCHOR_PATH: str = "examples/benchmarking/anchor_questions.json"

# WORKDIR of the benchmarking image (src/cli/templates/dockerfiles/Dockerfile-benchmarks).
# A relative anchor path is probed against this at runtime, so it is also where the
# staged bank must be bind-mounted.
CONTAINER_WORKDIR: str = "/root/archi"

# The checkout root (this file is src/utils/benchmark_schema.py). `archi evaluate` is
# a CLI and may be invoked from any directory, but the tracked default anchor bank
# lives under the checkout's `examples/` — which is not packaged and is not COPYd
# into the benchmark image. Resolving the default against the process CWD alone means
# any invocation from elsewhere stages nothing. Same root `_copy_default_prompts`
# stages its defaults from.
REPO_ROOT: str = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# Legacy authoring dialect -> ragas 0.3.5 modern schema.
LEGACY_TO_MODERN: Dict[str, str] = {
    "question": "user_input",
    "answer": "reference",
    "contexts": "retrieved_contexts",
}

# ragas 0.3.5 metric -> the bank column it additionally REQUIRES beyond
# ``user_input`` / ``response`` / ``retrieved_contexts``. The context metrics need
# the ground-truth ``reference`` to grade retrieval against; ``answer_correctness``
# needs it to grade the ANSWER against. ``answer_relevancy`` and ``faithfulness``
# judge the answer only against the question and the retrieved contexts, so they
# score a reference-less draft row too.
#
# Register EVERY metric here, including the ones that need nothing:
# ``metric_required_column`` reads this map with ``.get(metric)``, so an
# unregistered name reads back as "no requirement" and would be scored on rows
# that cannot support it.
_METRIC_REQUIRED_COLUMN: Dict[str, Optional[str]] = {
    "context_precision": "reference",
    "context_recall": "reference",
    "answer_correctness": "reference",
    "answer_relevancy": None,
    "faithfulness": None,
}


def normalize_record(record: Any) -> Any:
    """Return ``record`` in the modern ragas dialect.

    Each legacy key is renamed to its modern equivalent UNLESS the modern key is
    already present (a modern-authored record passes through unchanged). Non-dict
    items pass through untouched so the caller's own per-item validation still
    runs. Extension fields are preserved verbatim.
    """
    if not isinstance(record, dict):
        return record
    out = dict(record)
    for legacy, modern in LEGACY_TO_MODERN.items():
        if legacy in out:
            value = out.pop(legacy)
            out.setdefault(modern, value)
    return out


def normalize_bank(records: Any) -> Any:
    """Normalize every record in a bank list; non-list input passes through."""
    if not isinstance(records, list):
        return records
    return [normalize_record(r) for r in records]


# --- confirmation state: draft/locked census (maintain-ragas-goldenset) ------
# A row is authoritative ground truth for the maintenance tooling ONLY when its
# ``status`` is exactly ``"locked"``; anything else — absent, ``"draft"``, or an
# unexpected value — is treated as a non-authoritative draft. These readers back
# the census and drift gating and NEVER touch benchmark scoring (the harness
# scores every row's ``reference`` regardless of ``status``). ``status`` and the
# ``source_hashes`` map are extension fields, already preserved verbatim by
# ``normalize_record`` (it copies every key), so loading is unchanged.


def row_status(record: Any) -> str:
    """Return a row's confirmation status: ``"locked"`` iff it is exactly
    ``"locked"``, else ``"draft"`` (absent or any other value is not
    authoritative)."""
    if isinstance(record, dict) and record.get("status") == "locked":
        return "locked"
    return "draft"


def bank_status_counts(bank: Any) -> Dict[str, Any]:
    """Census a bank by confirmation state, read from the field (not by parsing
    ``notes``): ``locked`` / ``draft`` counts, ``total``, and the ``anchor_type``
    distribution (rows without an ``anchor_type`` counted under
    ``"unassigned"``)."""
    rows = bank if isinstance(bank, list) else []
    locked = 0
    anchor_type: Dict[str, int] = {}
    for record in rows:
        if row_status(record) == "locked":
            locked += 1
        key = "unassigned"
        if isinstance(record, dict):
            key = record.get("anchor_type") or "unassigned"
        anchor_type[key] = anchor_type.get(key, 0) + 1
    total = len(rows)
    return {
        "locked": locked,
        "draft": total - locked,
        "total": total,
        "anchor_type": anchor_type,
    }


def required_fields_for_modes(benchmarking_configs: Any) -> List[str]:
    """Schema-validation field set for the modes being run.

    ``user_input`` is always required; SOURCES mode additionally requires
    ``sources`` (so a modern bank lacking ``sources`` does not silently enter
    SOURCES mode and mis-score). RAGAS mode adds NOTHING — an empty ``reference``
    is valid input, ineligible only for the context metrics. Returns a fresh list
    each call (never accumulates across configs).

    ``benchmarking_configs`` is the ``services.benchmarking`` mapping; the active
    modes live in its ``modes`` LIST (not as top-level keys), so membership is
    tested against that list.
    """
    fields = ["user_input"]
    modes = (
        benchmarking_configs.get("modes", [])
        if isinstance(benchmarking_configs, dict)
        else []
    )
    if "SOURCES" in modes:
        fields.append("sources")
    return fields


def metric_required_column(metric: str) -> Optional[str]:
    """The bank column ``metric`` requires be non-empty, or ``None`` if it has no
    extra data requirement beyond the always-present ``user_input``/``response``."""
    return _METRIC_REQUIRED_COLUMN.get(metric)


def validate_bank(bank: Any, benchmarking_configs: Any) -> List[str]:
    """Return per-item schema errors for ``bank`` under the configured modes.

    The bank is first ``normalize_bank``-d (same as the harness) so a legacy
    bank is judged by its modern shape, then every item is checked for the
    presence of each field in ``required_fields_for_modes``. Returns a list of
    human-readable errors (empty ⇒ valid). Pure and never raises — a malformed
    item is reported, not thrown, so a preflight can surface EVERY problem at
    once rather than aborting on the first.
    """
    normalized = normalize_bank(bank)
    if not isinstance(normalized, list):
        return [f"bank is not a JSON list (got {type(normalized).__name__})"]
    required = required_fields_for_modes(benchmarking_configs)
    errors: List[str] = []
    for i, item in enumerate(normalized):
        if not isinstance(item, dict):
            errors.append(f"item[{i}]: not a dict (got {type(item).__name__})")
            continue
        missing = [f for f in required if f not in item]
        if missing:
            errors.append(f"item[{i}]: missing {missing} (has {sorted(item)})")
    return errors


def bank_eligibility_warnings(bank: Any, benchmarking_configs: Any) -> List[str]:
    """Return non-fatal warnings for RAGAS metrics that will score on a subset.

    Only when ``RAGAS`` is among the modes: for each enabled metric with a
    required column (see ``metric_required_column``), count the rows whose column
    is empty and warn with the scored denominator, so an operator sees the real
    per-metric sample size before spending an ingest. Never fatal, never raises.
    """
    if not isinstance(benchmarking_configs, dict):
        return []
    if "RAGAS" not in (benchmarking_configs.get("modes") or []):
        return []
    normalized = normalize_bank(bank)
    if not isinstance(normalized, list) or not normalized:
        return []
    total = len(normalized)
    metrics = (
        benchmarking_configs.get("mode_settings", {})
        .get("ragas_settings", {})
        .get("enabled_metrics", [])
    )
    warnings: List[str] = []
    for metric in metrics:
        column = metric_required_column(metric)
        if not column:
            continue
        n_ok = sum(
            1 for it in normalized if isinstance(it, dict) and bool(it.get(column))
        )
        if n_ok < total:
            warnings.append(
                f"{metric}: only {n_ok}/{total} rows have non-empty '{column}'"
                f" -> the other {total - n_ok} are excluded from this metric"
            )
    return warnings


def preflight_bank_file(
    queries_path: Any, benchmarking_configs: Any
) -> Tuple[List[str], List[str]]:
    """Load the bank JSON at ``queries_path`` and return ``(errors, warnings)``.

    A missing, unreadable, non-JSON, or non-list file is returned as a single
    hard error (the function NEVER raises), so every caller branches uniformly on
    ``errors``. A well-formed list is delegated to ``validate_bank`` /
    ``bank_eligibility_warnings``. A missing or non-string ``queries_path`` (e.g.
    a config that omits it) is likewise a single hard error, never a raise.
    """
    if not isinstance(queries_path, str) or not queries_path:
        return ([f"queries file path is missing or invalid: {queries_path!r}"], [])
    try:
        with open(queries_path, "r") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return ([f"queries file not found: {queries_path}"], [])
    except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError
        return ([f"queries file could not be read as JSON ({queries_path}): {exc}"], [])
    if not isinstance(raw, list):
        return (
            [
                f"queries file must be a JSON list, got {type(raw).__name__}: {queries_path}"
            ],
            [],
        )
    return (
        validate_bank(raw, benchmarking_configs),
        bank_eligibility_warnings(raw, benchmarking_configs),
    )


def effective_benchmarking(bench: Any) -> Dict[str, Any]:
    """Return ``services.benchmarking`` with the base-config.yaml defaults applied.

    The rendered deployment fills ``modes`` (default ``[SOURCES, RAGAS]``),
    ``queries_path`` (default ``queries``), and ``enabled_metrics`` (the four RAGAS
    metrics) when a config omits them. The preflight must judge a config by those
    SAME effective values — otherwise a config that omits ``modes`` would look
    RAGAS-only here yet run SOURCES (requiring ``sources``) after the ingest.
    Builds a fresh dict; the input is never mutated.
    """
    bench = bench if isinstance(bench, dict) else {}
    eff = dict(bench)
    eff["modes"] = bench.get("modes") or DEFAULT_MODES
    eff["queries_path"] = bench.get("queries_path") or DEFAULT_QUERIES_PATH
    mode_settings = dict(bench.get("mode_settings") or {})
    ragas_settings = dict(mode_settings.get("ragas_settings") or {})
    ragas_settings["enabled_metrics"] = (
        ragas_settings.get("enabled_metrics") or DEFAULT_ENABLED_METRICS
    )
    mode_settings["ragas_settings"] = ragas_settings
    eff["mode_settings"] = mode_settings
    return eff


def _load_bank_file(path: Any) -> Optional[List[Any]]:
    """Load + normalize a bank JSON file to a list, or ``None`` on any problem
    (missing / unreadable / non-JSON / non-list). Never raises."""
    if not isinstance(path, str) or not path:
        return None
    try:
        with open(path, "r") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return None
    bank = normalize_bank(raw)
    return bank if isinstance(bank, list) else None


def _bank_user_inputs(path: Any) -> set:
    """The set of ``user_input`` values in the bank at ``path`` (for anchor dedup)."""
    bank = _load_bank_file(path) or []
    return {
        it.get("user_input")
        for it in bank
        if isinstance(it, dict) and it.get("user_input")
    }


def _resolve_anchor_path(anchors: Dict[str, Any], data_path: Any) -> Optional[str]:
    """Resolve the anchor file the SAME way ``_merge_anchor_questions`` does:
    ``Path(DATA_PATH)/path`` first, then the raw (CWD-relative) path — then, as a
    host-only last resort, the checkout root (see ``REPO_ROOT``), so that running
    ``archi evaluate`` from outside the repo still finds the tracked default bank
    instead of silently reverting to "running without anchors". Returns the first
    that exists on disk, else ``None`` (mirrors the runtime's skip-if-absent — a
    container-only ``DATA_PATH`` file simply isn't visible at host preflight and is
    left to the runtime's own per-item guard)."""
    raw = anchors.get("path") or DEFAULT_ANCHOR_PATH
    if not isinstance(raw, str) or not raw:
        return None
    candidates: List[str] = []
    if isinstance(data_path, str) and data_path:
        candidates.append(os.path.join(data_path, raw))
    candidates.append(raw)
    candidates.append(os.path.join(REPO_ROOT, raw))
    return next((c for c in candidates if os.path.exists(c)), None)


def _anchor_cfg(benchmarking_configs: Any) -> Dict[str, Any]:
    """The ``anchors`` sub-mapping, tolerating a missing/non-dict config."""
    if not isinstance(benchmarking_configs, dict):
        return {}
    anchors = benchmarking_configs.get("anchors")
    return anchors if isinstance(anchors, dict) else {}


def anchors_enabled(benchmarking_configs: Any) -> bool:
    """True unless a config sets ``anchors.enabled`` to an explicit ``false``.

    Mirrors the runtime predicate in ``service_benchmark._merge_anchor_questions``
    (``anchor_cfg.get("enabled", True) is False``) EXACTLY: an absent ``anchors``
    block means anchors are ON, so a config that never mentions them still merges
    the default bank.
    """
    return _anchor_cfg(benchmarking_configs).get("enabled", True) is not False


def anchor_container_path(benchmarking_configs: Any) -> Optional[str]:
    """Absolute in-container path the runtime will probe for the anchor bank.

    A relative configured path is resolved against the image's ``CONTAINER_WORKDIR``
    (the runtime's second candidate, after the ``DATA_PATH`` volume miss); an
    absolute one is used verbatim. ``None`` when anchors are disabled, so the
    compose template can omit the mount entirely rather than bind a path that
    Docker would materialise as an empty directory.
    """
    if not anchors_enabled(benchmarking_configs):
        return None
    raw = _anchor_cfg(benchmarking_configs).get("path") or DEFAULT_ANCHOR_PATH
    if not isinstance(raw, str) or not raw:
        return None
    return raw if posixpath.isabs(raw) else posixpath.join(CONTAINER_WORKDIR, raw)


def anchor_source_path(benchmarking_configs: Any, data_path: Any) -> Optional[str]:
    """The host anchor bank to stage, or ``None`` if disabled or absent on disk.

    Resolved DATA_PATH-first exactly like ``preflight_benchmark_configs`` so the
    file that is *validated* at deploy is the same file that gets *staged*.
    """
    if not anchors_enabled(benchmarking_configs):
        return None
    return _resolve_anchor_path(_anchor_cfg(benchmarking_configs), data_path)


def _anchor_errors(
    anchor_path: str, eff: Dict[str, Any], staged_user_inputs: set
) -> List[str]:
    """Validate only the anchors that would actually be MERGED — the runtime skips
    an anchor whose ``user_input`` already appears in the question bank before it
    is ever validated, so validating those here would false-fail a valid run."""
    anchors = _load_bank_file(anchor_path)
    if anchors is None:
        return []  # unreadable anchor file -> runtime warns+skips, not an error
    # Mirror the runtime's exact merge predicate (_merge_anchor_questions): an
    # anchor is validated only if it is a dict WITH a truthy user_input that is
    # NOT already in the bank. Non-dict / user_input-less / duplicate anchors are
    # skipped there before validation, so flagging them here would false-fail.
    to_merge = [
        a
        for a in anchors
        if isinstance(a, dict)
        and a.get("user_input")
        and a["user_input"] not in staged_user_inputs
    ]
    return validate_bank(to_merge, eff)


def preflight_benchmark_configs(configs: Any) -> Tuple[List[str], List[str]]:
    """Preflight the effective question set for every config before a deploy.

    Models what actually runs: the deployment stages ONLY the FIRST config's
    ``queries_path`` (``cli_main`` sets ``query_file`` from ``configs[0]``;
    ``templates_manager`` copies that single file to ``queries.txt``;
    ``service_benchmark`` loads it once) and runs that one bank under EVERY
    config's modes. So the staged first bank is validated against each config's
    effective (template-defaulted) modes — not each config's own bank. Per config,
    an enabled anchor bank is also validated, resolved DATA_PATH-first like the
    runtime and deduplicated against the staged bank so only anchors that would be
    merged are checked. A missing/unreadable anchor file is skipped, never an
    error. Returns aggregated ``(errors, warnings)`` labelled per config.
    """
    if not isinstance(configs, list):
        configs = [configs]
    entries = []
    for idx, config in enumerate(configs):
        config = config if isinstance(config, dict) else {}
        bench = (config.get("services") or {}).get("benchmarking") or {}
        data_path = (config.get("global") or {}).get("DATA_PATH")
        label = config.get("name") or f"config[{idx}]"
        entries.append((label, effective_benchmarking(bench), data_path))

    all_errors: List[str] = []
    all_warnings: List[str] = []
    if not entries:
        return all_errors, all_warnings

    # Only the first config's bank is staged and run under every config.
    staged_queries_path = entries[0][1].get("queries_path")
    staged_user_inputs = _bank_user_inputs(staged_queries_path)

    for label, eff, data_path in entries:
        q_errors, q_warnings = preflight_bank_file(staged_queries_path, eff)
        all_errors.extend(f"{label} queries {e}" for e in q_errors)
        all_warnings.extend(f"{label} queries: {w}" for w in q_warnings)

        anchors = eff.get("anchors") or {}
        if anchors.get("enabled", True) is not False:
            anchor_path = _resolve_anchor_path(anchors, data_path)
            if anchor_path:
                a_errors = _anchor_errors(anchor_path, eff, staged_user_inputs)
                all_errors.extend(f"{label} anchors {e}" for e in a_errors)
    return all_errors, all_warnings


def row_is_eligible(row: Dict[str, Any], metric: str) -> bool:
    """True if ``row`` populates ``metric``'s required column (if any)."""
    column = metric_required_column(metric)
    if column is None:
        return True
    return bool(row.get(column))


def eligible_subset(
    rows: Sequence[Dict[str, Any]], keys: Sequence[str], metric: str
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Split ``(rows, keys)`` into the parallel subset eligible for ``metric``."""
    elig_rows: List[Dict[str, Any]] = []
    elig_keys: List[str] = []
    for row, key in zip(rows, keys):
        if row_is_eligible(row, metric):
            elig_rows.append(row)
            elig_keys.append(key)
    return elig_rows, elig_keys


def _mean_ignoring_nan(scores: Sequence[float]) -> float:
    """Mean over ``scores``, skipping any NaN cells ragas may emit for a row that
    the judge could not score. NaN here is a per-cell scoring failure inside an
    already-eligible subset, distinct from an ineligible (excluded) row."""
    real = [s for s in scores if isinstance(s, (int, float)) and not math.isnan(s)]
    if not real:
        return math.nan
    return sum(real) / len(real)


def score_metrics_per_eligibility(
    rows: Sequence[Dict[str, Any]],
    keys: Sequence[str],
    metrics: Sequence[str],
    question_wise_results: Dict[str, Dict[str, Any]],
    score_fn: Callable[[str, List[Dict[str, Any]]], Sequence[float]],
) -> Dict[str, Any]:
    """Score each metric over its own eligible subset, attach scores by key, and
    report per-metric aggregates + scored denominators.

    ``rows`` and ``keys`` are parallel: ``keys[i]`` is the ``question_<n>`` key of
    ``rows[i]`` in ``question_wise_results``. For each metric the eligible subset
    is scored via ``score_fn(metric, eligible_rows) -> per-row scores``; the
    per-row score is written onto ``question_wise_results[key][metric]``. A metric
    whose eligible subset is empty records ``n/a`` (a NaN aggregate,
    ``"0 of n_total"``) WITHOUT calling ``score_fn`` — so ragas is never invoked
    on an empty dataset. Returns ``{aggregate_<metric>, <metric>_scored}`` for
    every metric.

    WARNING: mutates ``question_wise_results`` in place.
    """
    total = len(rows)
    out: Dict[str, Any] = {}
    for metric in metrics:
        elig_rows, elig_keys = eligible_subset(rows, keys, metric)
        agg_key = f"aggregate_{metric}"
        scored_key = f"{metric}_scored"
        if not elig_rows:
            out[agg_key] = math.nan
            out[scored_key] = f"0 of {total}"
            continue
        scores = list(score_fn(metric, elig_rows))
        for key, score in zip(elig_keys, scores):
            question_wise_results[key][metric] = score
        out[agg_key] = _mean_ignoring_nan(scores)
        out[scored_key] = f"{len(elig_rows)} of {total}"
    return out
