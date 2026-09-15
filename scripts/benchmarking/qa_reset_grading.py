"""QA-focused reset: wipe annotations on the most recently created Argilla dataset.

Designed for the grading-UI testing loop where you need to repeatedly grade →
reset → grade again without remembering or copy-pasting dataset names.

Auto-selects the most recently inserted dataset in the workspace (default
`archi`) and delegates the actual deletion to `reset_argilla_dataset.py`. Records,
metadata, and dataset settings are preserved; only submitted Response objects
are removed, which puts every record back into the grader's queue.

Usage:
    # Most recent dataset, confirm before deleting:
    python scripts/benchmarking/qa_reset_grading.py

    # Most recent dataset, no confirmation prompt:
    python scripts/benchmarking/qa_reset_grading.py --yes

    # Different workspace:
    python scripts/benchmarking/qa_reset_grading.py --workspace some-other-ws

For resetting a specific named dataset, use the underlying tool directly:
    python scripts/benchmarking/reset_argilla_dataset.py --dataset <name>
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import requests


def _read_api_key() -> str | None:
    key = os.environ.get("ARGILLA_API_KEY")
    if key:
        return key
    fallback = Path("~/.archi/secrets/argilla_api_key.txt").expanduser()
    if fallback.exists():
        return fallback.read_text().strip()
    return None


def _ts(record: dict) -> str:
    # Argilla 2.x has shifted between "inserted_at" and "created_at"; check both.
    return record.get("inserted_at") or record.get("created_at") or ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset the most recently created Argilla dataset to ungraded state (for QA testing).",
    )
    parser.add_argument(
        "--workspace", "-w", default="archi", help="Argilla workspace (default: archi)"
    )
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip the confirmation prompt"
    )
    args = parser.parse_args()

    api_url = os.environ.get("ARGILLA_API_URL", "http://localhost:3080").rstrip("/")
    api_key = _read_api_key()
    if not api_key:
        print(
            "ERROR: set ARGILLA_API_KEY or place ~/.archi/secrets/argilla_api_key.txt",
            file=sys.stderr,
        )
        return 2

    headers = {"X-Argilla-Api-Key": api_key}

    # Resolve workspace name → id.
    wr = requests.get(f"{api_url}/api/v1/me/workspaces", headers=headers, timeout=10)
    wr.raise_for_status()
    ws_by_name = {w["name"]: w["id"] for w in wr.json().get("items", [])}
    ws_id = ws_by_name.get(args.workspace)
    if not ws_id:
        print(
            f"ERROR: workspace {args.workspace!r} not found. Available: {sorted(ws_by_name)}",
            file=sys.stderr,
        )
        return 1

    # List datasets and pick the most recent in this workspace.
    dr = requests.get(
        f"{api_url}/api/v1/me/datasets?limit=200", headers=headers, timeout=10
    )
    dr.raise_for_status()
    in_workspace = [
        d for d in dr.json().get("items", []) if d.get("workspace_id") == ws_id
    ]
    if not in_workspace:
        print(f"ERROR: no datasets in workspace {args.workspace!r}", file=sys.stderr)
        return 1

    in_workspace.sort(key=_ts, reverse=True)
    latest = in_workspace[0]
    print(f"Most recent dataset in {args.workspace!r}: {latest['name']}")
    print(f"  inserted: {_ts(latest) or '(unknown)'}")
    if len(in_workspace) > 1:
        print(
            f"  ({len(in_workspace) - 1} older dataset(s) in this workspace will not be touched)"
        )
    sys.stdout.flush()

    # Delegate the actual deletion to the canonical reset script.
    cmd = [
        sys.executable,
        str(Path(__file__).parent / "reset_argilla_dataset.py"),
        "--dataset",
        latest["name"],
        "--workspace",
        args.workspace,
    ]
    if args.yes:
        cmd.append("--yes")
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
