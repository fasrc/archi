"""Push an existing single-config `archi evaluate` output to Argilla for grading.

Unlike push_ragas_to_argilla.py (which takes a flat RAGAS list and is meant for
externally-produced Q+A pairs), this takes the bench-shaped JSON that
`archi evaluate` writes to bench_out/ — i.e. a dict with
`benchmarking_results[0].single_question_results` and a `metadata` block — and
pushes it as-is. This reuses a completed run instead of re-running the eval with
`--argilla`.

The dataset carries question / reference_answer / response / agent-trace fields
plus the standard correctness / failure_modes / quality / notes grading schema,
with the RAGAS metric scores (answer_relevancy, faithfulness, context_precision,
context_recall) attached as record metadata. The run's corpus_snapshot_id is
stamped onto every record (cross-sweep refusal guard in the analysis notebook).

Usage:
    python scripts/benchmarking/push_bench_output_to_argilla.py \\
        bench_out/benchmarking-ragas-bench-20260630_194654.json

    # Custom dataset name + grader quorum (default 2, matching ragas.yaml):
    python scripts/benchmarking/push_bench_output_to_argilla.py OUTPUT.json \\
        --name ragas-bench-20260630 --min-submitted 2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n\n", 1)[0],
    )
    parser.add_argument(
        "input", help="Path to an archi evaluate bench-output JSON file"
    )
    parser.add_argument(
        "--name",
        "-n",
        default=None,
        help="Argilla dataset name (default: derived from the input filename)",
    )
    parser.add_argument(
        "--min-submitted",
        type=int,
        default=2,
        help="Min graders for a record to be 'complete' (default: 2, matching ragas.yaml)",
    )
    args = parser.parse_args()

    src = Path(args.input).expanduser()
    if not src.exists():
        print(f"ERROR: input file not found: {src}", file=sys.stderr)
        return 1

    data = json.loads(src.read_text())
    results = data.get("benchmarking_results")
    if not results or not results[0].get("single_question_results"):
        print(
            f"ERROR: {src.name} has no benchmarking_results[0].single_question_results. "
            "Is this a single-config `archi evaluate` output?",
            file=sys.stderr,
        )
        return 1

    n = len(results[0]["single_question_results"])
    corpus_snapshot_id = (data.get("metadata") or {}).get("corpus_snapshot_id")
    print(f"Loaded {n} graded questions from {src.name}")
    print(f"corpus_snapshot_id: {corpus_snapshot_id}")

    # Same connection convention as push_ragas_to_argilla.py / scripts/bootstrap_argilla.py:
    # ARGILLA_API_URL/KEY from env, falling back to the standard secrets file + host port.
    if "ARGILLA_API_KEY" not in os.environ:
        key_file = Path("~/.archi/secrets/argilla_api_key.txt").expanduser()
        if key_file.exists():
            os.environ["ARGILLA_API_KEY"] = key_file.read_text().strip()
    os.environ.setdefault("ARGILLA_API_URL", "http://localhost:3080")

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.utils.benchmark_argilla import push_single_results_to_argilla

    dataset_name = (
        args.name or src.stem
    )  # e.g. benchmarking-ragas-bench-20260630_194654
    pushed = push_single_results_to_argilla(
        benchmark_data=data,
        dataset_name=dataset_name,
        corpus_snapshot_id=corpus_snapshot_id,
        min_submitted=args.min_submitted,
    )
    url = os.environ["ARGILLA_API_URL"].rstrip("/")
    print(f"\nDataset created: {pushed}")
    print(f"Open in Argilla: {url}/dataset/{pushed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
