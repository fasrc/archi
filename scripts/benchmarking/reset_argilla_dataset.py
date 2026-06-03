"""Wipe all submitted annotations from an Argilla dataset, keeping records intact.

Use when you want to re-grade an existing dataset from scratch — e.g. after a
rubric change, a calibration round, or a sloppy test pass. Records, fields,
metadata, and dataset settings (questions, distribution) are preserved; only
the submitted Response objects are deleted.

The Argilla 2.x Python SDK does not expose a Response.delete() method, so this
script hits the REST endpoint `DELETE /api/v1/responses/{id}` directly.

Usage:
    export ARGILLA_API_URL=http://localhost:3080
    export ARGILLA_API_KEY=$(cat ~/.archi/secrets/argilla_api_key.txt)
    python scripts/benchmarking/reset_argilla_dataset.py --dataset bench-foo-20260603-...

    # Skip the confirmation prompt:
    python scripts/benchmarking/reset_argilla_dataset.py --dataset bench-foo-... --yes

    # Different workspace:
    python scripts/benchmarking/reset_argilla_dataset.py --dataset bench-foo-... --workspace some-other-ws
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Optional

import requests


def _get_response_id(response: Any) -> Optional[str]:
    """Return the response's API id across known SDK shapes.

    Argilla 2.x SDK has shifted the internal model attribute between minor
    versions; try the documented `_model.id` first, fall back to `id` directly.
    """
    model = getattr(response, "_model", None)
    rid = getattr(model, "id", None)
    if rid is not None:
        return str(rid)
    rid = getattr(response, "id", None)
    if rid is not None:
        return str(rid)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete all submitted annotations from an Argilla dataset (records preserved).",
    )
    parser.add_argument(
        "--dataset", "-d", required=True,
        help="Argilla dataset name (e.g. bench-dryrun-20260603-015649)",
    )
    parser.add_argument(
        "--workspace", "-w", default="archi",
        help="Argilla workspace (default: archi)",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the confirmation prompt",
    )
    args = parser.parse_args()

    api_url = os.environ.get("ARGILLA_API_URL", "http://localhost:3080").rstrip("/")
    api_key = os.environ.get("ARGILLA_API_KEY")
    if not api_key:
        secrets_dir = os.path.expanduser("~/.archi/secrets")
        key_path = os.path.join(secrets_dir, "argilla_api_key.txt")
        if os.path.exists(key_path):
            with open(key_path) as f:
                api_key = f.read().strip()
    if not api_key:
        print(
            "ERROR: set ARGILLA_API_KEY or place ~/.archi/secrets/argilla_api_key.txt",
            file=sys.stderr,
        )
        return 2

    try:
        import argilla as rg  # pyright: ignore[reportMissingImports]
    except ImportError:
        print(
            "ERROR: argilla SDK is not installed in this environment. "
            "Activate the archi conda env (or `pip install 'argilla>=2.5,<3'`).",
            file=sys.stderr,
        )
        return 2

    client = rg.Argilla(api_url=api_url, api_key=api_key)
    dataset = client.datasets(name=args.dataset, workspace=args.workspace)
    if dataset is None:
        print(
            f"ERROR: dataset {args.dataset!r} not found in workspace {args.workspace!r}",
            file=sys.stderr,
        )
        return 1

    # Count first so the operator sees the blast radius before agreeing.
    response_ids: list[str] = []
    record_count = 0
    for record in dataset.records(with_responses=True):
        record_count += 1
        for response in record.responses or []:
            rid = _get_response_id(response)
            if rid:
                response_ids.append(rid)

    if not response_ids:
        print(
            f"No responses found in {args.dataset!r} ({record_count} records). Nothing to do."
        )
        return 0

    print(
        f"About to delete {len(response_ids)} responses across {record_count} records "
        f"in {args.dataset!r} (workspace {args.workspace!r}).\n"
        "Records, fields, metadata, and dataset settings are PRESERVED — "
        "only submitted annotations are removed."
    )
    if not args.yes:
        confirm = input("Proceed? [yes/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 1

    headers = {"X-Argilla-Api-Key": api_key}
    deleted = 0
    failed = 0
    for rid in response_ids:
        r = requests.delete(f"{api_url}/api/v1/responses/{rid}", headers=headers, timeout=10)
        if r.ok:
            deleted += 1
        else:
            failed += 1
            print(f"  failed: {rid} -> HTTP {r.status_code}: {r.text[:120]}", file=sys.stderr)

    print(f"Deleted {deleted}/{len(response_ids)} responses ({failed} failed).")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
