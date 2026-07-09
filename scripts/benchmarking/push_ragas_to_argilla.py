"""Push a RAGAS-format JSON dataset directly to Argilla for human grading.

Skips the slow archi run entirely. Use this when you have pre-computed Q+A
pairs (from a different system, an earlier archi run, or hand-crafted) and
just want them in Argilla so a grader can rate them.

Input format (the modern RAGAS API):
    [
      {
        "user_input": "How do I ...?",
        "response": "You should ...",
        "retrieved_contexts": ["chunk1", "chunk2"],   # optional
        "reference": "The canonical answer is ..."     # optional
      },
      ...
    ]

Output: a single-mode Argilla dataset with question / response / reference_answer
fields and the standard correctness / failure_modes / quality / notes grading
schema. RAGAS metric metadata is left empty (faithfulness/context_precision/recall
need contexts; relevancy could be backfilled by calling the RAGAS judge if needed).

Usage:
    python scripts/benchmarking/push_ragas_to_argilla.py path/to/dataset.json

    # Custom dataset name + minimum graders required for a record to be
    # marked complete (default 1 — single-grader QA testing):
    python scripts/benchmarking/push_ragas_to_argilla.py dataset.json \\
        --name ragas-import-20260603 --min-submitted 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _ragas_to_bench_shape(ragas_items: list[dict]) -> dict:
    """Map RAGAS items into the dict that push_single_results_to_argilla wants.

    The push function reads `benchmarking_results[0].single_question_results`,
    a dict keyed by `question_N` with each item carrying `question`, `answer`,
    `reference_answer`, etc.
    """
    questions = {}
    for i, item in enumerate(ragas_items, 1):
        questions[f"question_{i}"] = {
            "question": item.get("user_input") or item.get("question") or "",
            "answer": item.get("response") or item.get("answer") or "(no answer)",
            "reference_answer": item.get("reference")
            or item.get("reference_answer")
            or "N/A",
            "messages": [],
        }
    return {
        "benchmarking_results": [
            {"single_question_results": questions},
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n\n", 1)[0],
    )
    parser.add_argument("input", help="Path to RAGAS-format JSON file")
    parser.add_argument(
        "--name",
        "-n",
        default=None,
        help="Argilla dataset name (default: ragas-import-<timestamp>)",
    )
    parser.add_argument(
        "--min-submitted",
        type=int,
        default=1,
        help="Min graders for a record to be 'complete' (default: 1 for solo QA)",
    )
    args = parser.parse_args()

    src = Path(args.input).expanduser()
    if not src.exists():
        print(f"ERROR: input file not found: {src}", file=sys.stderr)
        return 1

    items = json.loads(src.read_text())
    if not isinstance(items, list) or not items:
        print(f"ERROR: expected a non-empty list at top of {src}", file=sys.stderr)
        return 1

    bench_data = _ragas_to_bench_shape(items)
    n = len(bench_data["benchmarking_results"][0]["single_question_results"])
    print(f"Loaded {n} RAGAS items from {src.name}")

    # Need ARGILLA_API_URL / ARGILLA_API_KEY in env so the push module's
    # _get_client() picks them up. Fall back to the standard secrets file.
    if "ARGILLA_API_KEY" not in os.environ:
        key_file = Path("~/.archi/secrets/argilla_api_key.txt").expanduser()
        if key_file.exists():
            os.environ["ARGILLA_API_KEY"] = key_file.read_text().strip()
    if "ARGILLA_API_URL" not in os.environ:
        os.environ["ARGILLA_API_URL"] = "http://localhost:3080"

    # Lazy-import so the file is usable without the SDK to inspect the mapping.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.utils.benchmark_argilla import push_single_results_to_argilla

    dataset_name = args.name or f"ragas-import-{time.strftime('%Y%m%d-%H%M%S')}"
    pushed = push_single_results_to_argilla(
        benchmark_data=bench_data,
        dataset_name=dataset_name,
        corpus_snapshot_id=None,
        min_submitted=args.min_submitted,
    )
    print(f"\nDataset created: {pushed}")
    print(
        f"Open in Argilla: {os.environ['ARGILLA_API_URL'].rstrip('/')}/dataset/{pushed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
