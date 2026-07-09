#!/usr/bin/env python
"""Preflight-validate a benchmark question bank against the harness schema.

Fail fast — in under a second — BEFORE ``archi evaluate`` deploys and re-ingests
(~50 min), instead of discovering a bank/mode schema mismatch per-question at
grading time (after the ingest). Reuses the harness's OWN single-source-of-truth
helpers in ``src.utils.benchmark_schema`` so it can never drift from what the
benchmarker enforces.

Usage:
    python scripts/benchmarking/validate_queries.py -c <config.yaml> [-q <bank.json>]

Exit code 0 = safe to run; 1 = would score 0 / degrade. Warnings (per-metric
eligibility) are printed but do not fail.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils.benchmark_schema import preflight_bank_file  # noqa: E402  # isort: skip


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a benchmark question bank against the harness schema for the "
            "config's modes, before running archi evaluate."
        )
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to the benchmark .yaml config."
    )
    parser.add_argument(
        "-q",
        "--queries",
        help="Override the bank file (else services.benchmarking.queries_path).",
    )
    args = parser.parse_args(argv)

    with open(args.config, "r") as handle:
        config = yaml.safe_load(handle)
    bench = (config or {}).get("services", {}).get("benchmarking", {})
    queries_path = args.queries or bench.get("queries_path")

    print(f"config : {args.config}")
    print(f"modes  : {bench.get('modes', [])}")
    print(f"queries: {queries_path}")

    if not queries_path:
        print(
            "FAIL: no queries file (pass -q or set services.benchmarking.queries_path)"
        )
        return 1

    errors, warnings = preflight_bank_file(queries_path, bench)

    for warning in warnings:
        print(f"WARNING: {warning}")

    if errors:
        print(f"FAIL: {len(errors)} schema error(s) — the run would score 0/degrade:")
        for err in errors[:10]:
            print(f"  {err}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more")
        return 1

    print("PASS: bank satisfies the required schema for these modes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
