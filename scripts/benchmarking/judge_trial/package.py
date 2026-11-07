"""Build the judge-trial package: bundles, Sonnet reference scores and the swapped-answer
control (fasrc/archi#737, step 5.3).

    python -m scripts.benchmarking.judge_trial.package \
        --rebuilt-runs <folder with <basename>/judge_bundles/<basename>-arm0> \
        --results <folder with <basename>.json> --out <package root> \
        [--control-from <basename>] [--control-size 20] [--seed 20261027]

Inputs are inline Sonnet-judged runs whose full-context bundles were rebuilt from the
corpus database (the lynx-vs-ragas work, 2026-10-24). For each run the package gets:

- ``bundles/<basename>-arm0/``: the same rows, re-emitted with ``write_judge_bundle`` so
  the manifest carries the run's own ragas settings and metric list (the rebuilt manifests
  said ``faithfulness`` only and the ragas defaults), ``judge_mode: inline+bundle`` (the
  rows were judged inline and bundled), the run's ``code_version`` and the rebuilt rows'
  original digest;
- ``reference/<basename>.sonnet.jsonl``: one line per bundle row with the inline judge's
  per-metric values from the result JSON, joined by key;
- ``reference/<basename>.meta.json``: the run's judge identity, usage and settings.

One control bundle, ``bundles/control-unrelated-<basename>-arm0/``, pairs ``--control-size``
rows of ``--control-from`` with the answer of another, distinct row of the same run
(seeded, distinct donors, no passage shared between the row and its donor). A judge must
score those answers low on the answer-reading metrics (faithfulness, answer_relevancy,
answer_correctness); context_precision and context_recall do not read the answer.
``reference/control.jsonl`` records each control row's donor and the Sonnet score of the
original (unswapped) row.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.benchmark_schema import RAGAS_METRIC_NAMES  # noqa: E402
from src.utils.judge_bundle import MODE_INLINE_BUNDLE, write_judge_bundle  # noqa: E402
from src.utils.judge_scorer import BundleFailed, load_bundle  # noqa: E402

CONTROL_PREFIX = "control-unrelated-"
_REBUILT_NOTE = "unavailable: rows rebuilt from the corpus database, not from a run"


class PackageError(Exception):
    """A refusal; the message is for the operator."""


def ragas_settings_of(result: Dict[str, Any]) -> Dict[str, Any]:
    """The ragas settings the run used (running configuration first, then selected)."""
    arm = result["benchmarking_results"][0]
    for key in ("running_configuration", "configuration"):
        config = arm.get(key) or {}
        settings = (
            ((config.get("services") or {}).get("benchmarking") or {})
            .get("mode_settings", {})
            .get("ragas_settings")
        )
        if settings:
            return dict(settings)
    raise PackageError("the result JSON records no ragas_settings")


def reference_rows(result: Dict[str, Any], keys: Sequence[str]) -> List[Dict[str, Any]]:
    """One record per bundle key with the inline judge's metric values."""
    questions = result["benchmarking_results"][0]["single_question_results"]
    rows = []
    for key in keys:
        q = questions.get(key)
        if q is None:
            raise PackageError(f"the result JSON has no {key}, but the bundle does")
        rows.append(
            {
                "key": key,
                "status": q.get("status"),
                "anchor_type": q.get("anchor_type"),
                **{m: q.get(m) for m in RAGAS_METRIC_NAMES if m in q},
            }
        )
    return rows


def run_meta(result: Dict[str, Any], basename: str) -> Dict[str, Any]:
    arm = result["benchmarking_results"][0]
    return {
        "result_basename": basename,
        "ragas_settings": ragas_settings_of(result),
        "ragas_effective_settings": arm.get("ragas_effective_settings"),
        "ragas_judge_identity_effective": arm.get("ragas_judge_identity_effective"),
        "judge_usage": arm.get("judge_usage"),
        "code_version": (result.get("metadata") or {}).get("code_version"),
        "corpus_fingerprint": arm.get("corpus_fingerprint"),
        "question_count": len(arm["single_question_results"]),
    }


def _passages(row: Dict[str, Any]) -> set:
    return set(row.get("retrieved_contexts") or [])


def build_control(
    rows: Sequence[Dict[str, Any]],
    keys: Sequence[str],
    *,
    size: int,
    seed: int,
) -> Tuple[List[Dict[str, Any]], List[str], List[Dict[str, Any]]]:
    """``size`` rows with another row's answer; distinct donors; no shared passage.

    Deterministic for a seed. Raises PackageError when the run is too small to give
    ``size`` valid pairs.
    """
    if size < 1:
        raise PackageError("--control-size must be 1 or more")
    if len(rows) < size + 1:
        raise PackageError(f"{len(rows)} rows cannot give {size} control pairs")
    rng = random.Random(seed)
    order = list(range(len(rows)))
    rng.shuffle(order)
    targets = order[:size]
    donors_pool = [i for i in order if i not in set(targets)] + list(targets)
    used = set()
    control_rows, control_keys, ledger = [], [], []
    for t in targets:
        row = rows[t]
        donor = None
        for d in donors_pool:
            if d == t or d in used:
                continue
            cand = rows[d]
            if cand["response"] == row["response"]:
                continue
            if _passages(cand) & _passages(row):
                continue
            donor = d
            break
        if donor is None:
            raise PackageError(
                f"no distinct donor without a shared passage for {keys[t]}"
            )
        used.add(donor)
        control_rows.append(
            {
                "user_input": row["user_input"],
                "retrieved_contexts": list(row["retrieved_contexts"]),
                "response": rows[donor]["response"],
                "reference": row["reference"],
            }
        )
        control_keys.append(f"{keys[t]}__answer_from__{keys[donor]}")
        ledger.append({"key": control_keys[-1], "row": keys[t], "donor": keys[donor]})
    return control_rows, control_keys, ledger


def _bundle_of(rebuilt_runs: Path, basename: str) -> Path:
    folder = rebuilt_runs / basename / "judge_bundles" / f"{basename}-arm0"
    if not (folder / "BUNDLE_COMPLETE").is_file():
        raise PackageError(f"{folder} is not a complete bundle")
    return folder


def _emit(
    out: Path,
    basename: str,
    rows: Sequence[Dict[str, Any]],
    keys: Sequence[str],
    settings: Dict[str, Any],
    code_version: Dict[str, Any],
    source_digest: str,
) -> Dict[str, str]:
    return write_judge_bundle(
        out / "bundles",
        result_basename=basename,
        arm_index=0,
        arm_count=1,
        rows=rows,
        keys=keys,
        enabled_metrics=settings.get("enabled_metrics") or list(RAGAS_METRIC_NAMES),
        ragas_settings=settings,
        verbosity=0,
        code_version={**dict(code_version or {}), "rebuilt_rows_sha256": source_digest},
        package_digest=_REBUILT_NOTE,
        judge_mode=MODE_INLINE_BUNDLE,
    )


def _write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> int:
    count = 0
    with open(path, "w") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            count += 1
    return count


def build_package(
    *,
    rebuilt_runs: Path,
    results: Path,
    out: Path,
    basenames: Sequence[str],
    control_from: Optional[str],
    control_size: int,
    seed: int,
    unscored: Sequence[str] = (),
    unscored_rebuilt: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write the package. ``unscored`` names rebuilt runs that no judge has scored (no
    reference rows): they are re-emitted under ``bundles-unscored/`` with the ragas
    settings of the first run in ``basenames`` and ``judge_mode: deferred``."""
    out = Path(out)
    (out / "bundles").mkdir(parents=True, exist_ok=True)
    (out / "reference").mkdir(exist_ok=True)
    manifest: Dict[str, Any] = {
        "seed": seed,
        "runs": [],
        "control": None,
        "unscored": [],
    }
    rows_by_run: Dict[str, Tuple[List[Dict[str, Any]], List[str]]] = {}
    for basename in basenames:
        try:
            source_manifest, rows, keys = load_bundle(
                _bundle_of(rebuilt_runs, basename)
            )
        except BundleFailed as exc:
            raise PackageError(f"{basename}: {exc}") from exc
        result_path = results / f"{basename}.json"
        if not result_path.is_file():
            raise PackageError(f"no result JSON at {result_path}")
        result = json.loads(result_path.read_text())
        settings = ragas_settings_of(result)
        written = _emit(
            out,
            basename,
            rows,
            keys,
            settings,
            (result.get("metadata") or {}).get("code_version") or {},
            source_manifest["rows_sha256"],
        )
        n_ref = _write_jsonl(
            out / "reference" / f"{basename}.sonnet.jsonl", reference_rows(result, keys)
        )
        (out / "reference" / f"{basename}.meta.json").write_text(
            json.dumps(run_meta(result, basename), indent=2, sort_keys=True) + "\n"
        )
        rows_by_run[basename] = (rows, keys)
        manifest["runs"].append(
            {
                "result_basename": basename,
                "bundle": written["folder"],
                "rows": len(rows),
                "rows_sha256": written["digest"],
                "rebuilt_rows_sha256": source_manifest["rows_sha256"],
                "reference_rows": n_ref,
                "metrics": settings.get("enabled_metrics"),
                "judge": settings.get("evaluator_model"),
            }
        )
    if control_from:
        if control_from not in rows_by_run:
            raise PackageError(f"--control-from {control_from} is not one of the runs")
        rows, keys = rows_by_run[control_from]
        c_rows, c_keys, ledger = build_control(rows, keys, size=control_size, seed=seed)
        result = json.loads((results / f"{control_from}.json").read_text())
        settings = ragas_settings_of(result)
        c_name = f"{CONTROL_PREFIX}{control_from}"
        written = _emit(
            out,
            c_name,
            c_rows,
            c_keys,
            settings,
            (result.get("metadata") or {}).get("code_version") or {},
            "n/a",
        )
        originals = {r["key"]: r for r in reference_rows(result, keys)}
        for entry in ledger:
            entry["original_sonnet"] = {
                m: originals[entry["row"]].get(m)
                for m in RAGAS_METRIC_NAMES
                if m in originals[entry["row"]]
            }
        _write_jsonl(out / "reference" / "control.jsonl", ledger)
        manifest["control"] = {
            "bundle": written["folder"],
            "rows": len(c_rows),
            "rows_sha256": written["digest"],
            "source_run": control_from,
            "answer_reading_metrics": [
                "faithfulness",
                "answer_relevancy",
                "answer_correctness",
            ],
        }
    if unscored:
        if not basenames:
            raise PackageError("--unscored needs at least one --run for its settings")
        settings = ragas_settings_of(
            json.loads((results / f"{basenames[0]}.json").read_text())
        )
        root = unscored_rebuilt or rebuilt_runs
        for basename in unscored:
            try:
                source_manifest, rows, keys = load_bundle(_bundle_of(root, basename))
            except BundleFailed as exc:
                raise PackageError(f"{basename}: {exc}") from exc
            stand_in = root / basename / f"{basename}.json"
            code_version = {}
            if stand_in.is_file():
                code_version = (
                    json.loads(stand_in.read_text()).get("metadata") or {}
                ).get("code_version") or {}
            written = write_judge_bundle(
                out / "bundles-unscored",
                result_basename=basename,
                arm_index=0,
                arm_count=1,
                rows=rows,
                keys=keys,
                enabled_metrics=settings.get("enabled_metrics")
                or list(RAGAS_METRIC_NAMES),
                ragas_settings=settings,
                verbosity=0,
                code_version={
                    **code_version,
                    "rebuilt_rows_sha256": source_manifest["rows_sha256"],
                },
                package_digest=_REBUILT_NOTE,
            )
            manifest["unscored"].append(
                {
                    "result_basename": basename,
                    "bundle": written["folder"],
                    "rows": len(rows),
                    "rows_sha256": written["digest"],
                    "settings_from": basenames[0],
                }
            )
    (out / "package.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--rebuilt-runs", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run", action="append", required=True, dest="runs")
    parser.add_argument("--control-from")
    parser.add_argument("--control-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261027)
    parser.add_argument("--unscored", action="append", default=[])
    parser.add_argument("--unscored-rebuilt", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = build_package(
            rebuilt_runs=args.rebuilt_runs,
            results=args.results,
            out=args.out,
            basenames=args.runs,
            control_from=args.control_from,
            control_size=args.control_size,
            seed=args.seed,
            unscored=args.unscored,
            unscored_rebuilt=args.unscored_rebuilt,
        )
    except PackageError as exc:
        print(f"package: refused: {exc}", file=sys.stderr)
        return 1
    for run in manifest["runs"]:
        print(
            f"{run['bundle']}: {run['rows']} rows, {run['reference_rows']} reference rows"
        )
    if manifest["control"]:
        print(
            f"{manifest['control']['bundle']}: {manifest['control']['rows']} control rows"
        )
    for run in manifest["unscored"]:
        print(
            f"bundles-unscored/{run['bundle']}: {run['rows']} rows (no reference scores)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
