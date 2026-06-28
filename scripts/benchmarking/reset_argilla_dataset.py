"""Wipe all submitted annotations from an Argilla dataset, keeping records intact.

Use when you want to re-grade an existing dataset from scratch — e.g. after a
rubric change, a calibration round, or a sloppy test pass. Records, fields,
metadata, and dataset settings (questions, distribution) are preserved; only
the submitted Response objects are deleted.

The Argilla 2.x Python SDK silently returns empty `record.responses` even when
responses exist (verified on SDK 2.8 against datasets with submitted annotations
from non-current-user accounts), so this script bypasses the SDK entirely and
talks to the REST API.

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
from typing import Optional

import requests


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete all submitted annotations from an Argilla dataset (records preserved).",
    )
    parser.add_argument(
        "--dataset",
        "-d",
        required=True,
        help="Argilla dataset name (e.g. bench-dryrun-20260603-015649)",
    )
    parser.add_argument(
        "--workspace",
        "-w",
        default="archi",
        help="Argilla workspace (default: archi)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
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

    headers = {"X-Argilla-Api-Key": api_key}

    # Resolve workspace name → id (REST; the SDK's record.responses returns
    # empty even when responses exist, so we use REST throughout).
    wr = requests.get(f"{api_url}/api/v1/me/workspaces", headers=headers, timeout=10)
    wr.raise_for_status()
    ws_by_name = {w["name"]: w["id"] for w in wr.json().get("items", [])}
    ws_id = ws_by_name.get(args.workspace)
    if ws_id is None:
        print(
            f"ERROR: workspace {args.workspace!r} not found. Available: {sorted(ws_by_name)}",
            file=sys.stderr,
        )
        return 1

    # Find dataset by (name, workspace_id), paginating so a workspace with
    # >200 datasets doesn't yield a false "not found".
    ds_id: Optional[str] = None
    ds_offset = 0
    ds_page = 200
    while ds_id is None:
        dr = requests.get(
            f"{api_url}/api/v1/me/datasets",
            params={"limit": ds_page, "offset": ds_offset},
            headers=headers,
            timeout=10,
        )
        dr.raise_for_status()
        ds_items = dr.json().get("items", [])
        if not ds_items:
            break
        for d in ds_items:
            if d.get("name") == args.dataset and d.get("workspace_id") == ws_id:
                ds_id = d["id"]
                break
        if len(ds_items) < ds_page:
            break
        ds_offset += ds_page
    if ds_id is None:
        print(
            f"ERROR: dataset {args.dataset!r} not found in workspace {args.workspace!r}",
            file=sys.stderr,
        )
        return 1

    # Page through records?include=responses and collect submitted response ids.
    # The REST endpoint returns submitted, draft, AND discarded responses, but
    # this tool only wipes *submitted* annotations — graders' in-progress drafts
    # and discarded responses are preserved.
    response_ids: list[str] = []
    record_count = 0
    offset = 0
    page_size = 200
    while True:
        rr = requests.get(
            f"{api_url}/api/v1/datasets/{ds_id}/records",
            params={"include": "responses", "limit": page_size, "offset": offset},
            headers=headers,
            timeout=30,
        )
        rr.raise_for_status()
        items = rr.json().get("items", [])
        if not items:
            break
        record_count += len(items)
        for rec in items:
            for resp in rec.get("responses") or []:
                if resp.get("status") != "submitted":
                    continue  # preserve draft / discarded responses
                rid = resp.get("id")
                if rid:
                    response_ids.append(rid)
        if len(items) < page_size:
            break
        offset += page_size

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

    deleted = 0
    failed = 0
    for rid in response_ids:
        r = requests.delete(
            f"{api_url}/api/v1/responses/{rid}", headers=headers, timeout=10
        )
        if r.ok:
            deleted += 1
        else:
            failed += 1
            print(
                f"  failed: {rid} -> HTTP {r.status_code}: {r.text[:120]}",
                file=sys.stderr,
            )

    print(f"Deleted {deleted}/{len(response_ids)} responses ({failed} failed).")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
