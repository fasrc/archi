#!/usr/bin/env python3
"""Stamp existing benchmark artifacts with a code version and a config version.

Why
---
A benchmark artifact is evidence only if it can say which code and which settings
produced its scores. The reports already in ``bench_out/`` cannot:

* every run from 2026-08-11 through 2026-08-17 records the same
  ``git_info.last_commit`` (``0a157cdce0``) with an empty diff, because
  ``archi create`` writes ``git_info.yaml`` once and then freezes it -- the value
  identifies the deploy, not the image a given arm ran;
* ``corpus_snapshot_id`` is a fresh UUID per invocation, so it separates runs but
  can never show two runs saw the same corpus;
* nothing recorded the configuration's identity at all, which is how
  ``bench-8192-20260817_170850.json`` -- the 8192 arm -- came to attest
  ``context_window: 32768``;
* the HTML reports carried none of it: ``parse_benchmark_results`` kept only
  ``time`` out of the whole metadata block.

What this does and does not claim
---------------------------------
This is a backfill, so it is strictly additive and it refuses to invent:

* ``config_version.digest`` is real -- the configuration *file* was recorded, so
  two artifacts with different digests definitely ran different files. It is
  labelled as reconstructed from that file, because the file is not necessarily
  what the agent read.
* ``code_version.digest`` stays ``None``. The code an old run executed is not
  recoverable from its artifact, and promoting the frozen deploy commit would
  manufacture the very false attribution this exists to prevent.

Existing keys are never overwritten, and a file already stamped is skipped, so
the script is safe to re-run.

``--regenerate-html`` is independent of that skip: the HTML is a view of the JSON
and goes stale when the *renderer* changes, not only when the data does. So a
report-format fix re-renders every artifact, stamped or not.

Usage
-----
    python scripts/benchmarking/backfill_report_provenance.py --dry-run
    python scripts/benchmarking/backfill_report_provenance.py
    python scripts/benchmarking/backfill_report_provenance.py --regenerate-html
    python scripts/benchmarking/backfill_report_provenance.py --regenerate-md

    # a subset
    python scripts/benchmarking/backfill_report_provenance.py bench_out/bench-8192-*.json
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.benchmark_provenance import (  # noqa: E402
    reconstruct_version_stamp,
)
from src.utils.generate_benchmark_report import (  # noqa: E402
    format_html_output,
    format_markdown_output,
    parse_benchmark_results,
)

DEFAULT_GLOB = "bench_out/*.json"
STAMP_KEYS = ("code_version", "config_version", "config_versions")
NOT_AN_ARTIFACT = "skipped (not a benchmark artifact)"


def stamp_file(path, dry_run=False):
    """Add the version blocks to one artifact. Returns a short status string.

    The config version goes on each result record, because one invocation runs
    every config in a sweep -- ``benchmarking-bench-sweep-20260610_015120.json``
    holds three arms, and a single per-file stamp would label all three with
    whichever ran last. The metadata block carries the code version (one image
    per invocation) and a digest per arm, in the order they ran.
    """
    with open(path, "r") as handle:
        document = json.load(handle)

    if not isinstance(document, dict) or "metadata" not in document:
        return NOT_AN_ARTIFACT

    metadata = document["metadata"]
    if any(key in metadata for key in STAMP_KEYS):
        return "skipped (already stamped)"

    results = document.get("benchmarking_results") or []

    digests = []
    arms = []
    for record in results:
        stamp = reconstruct_version_stamp(
            metadata,
            recorded_config=record.get("configuration"),
            configuration_file=record.get("configuration_file"),
        )
        record["config_version"] = stamp["config_version"]
        digests.append(stamp["config_version"]["digest"])
        arm = stamp["config_version"]["key_settings"].get(
            "services.chat_app.context_editing"
        )
        if arm:
            arms.append(json.dumps(arm, sort_keys=True))

    # code_version is per invocation, so it is derived once from the metadata.
    metadata["code_version"] = reconstruct_version_stamp(metadata, None, None)[
        "code_version"
    ]
    metadata["config_versions"] = digests

    shown = ", ".join((d or "none")[:19] for d in digests) or "no arms"
    detail = f"{len(digests)} arm(s): {shown}"
    if arms:
        detail += f" context_editing={'; '.join(arms)}"

    if dry_run:
        return f"would stamp ({detail})"

    with open(path, "w") as handle:
        json.dump(document, handle, indent=4)
    return f"stamped ({detail})"


def regenerate_html(json_path, dry_run=False):
    """Re-render the HTML sibling so the panel shows the freshly stamped values.

    The HTML is a view of the JSON, so re-rendering is how an old report gains the
    provenance panel. Only rewrites a report that already exists -- this is not
    the place to create reports that were never generated.
    """
    html_path = json_path.with_name(json_path.stem + "_report.html")
    if not html_path.exists():
        return None
    if dry_run:
        return f"would re-render {html_path.name}"

    with open(json_path, "r") as handle:
        document = json.load(handle)

    metadata = document["metadata"]
    results = document["benchmarking_results"]
    # parse_benchmark_results renders record 0 and builds the provenance block
    # from that record plus the metadata, so the panel is captioned with record
    # 0's arm rather than a sweep's last one.
    config_data, config_name, timestamp, questions, total_results, provenance = (
        parse_benchmark_results(results, metadata)
    )
    html = format_html_output(
        config_data,
        config_name,
        timestamp,
        questions,
        total_results,
        provenance=provenance,
    )
    with open(html_path, "w") as handle:
        handle.write(html)
    return f"re-rendered {html_path.name}"


def regenerate_md(json_path, dry_run=False):
    """Render the markdown sibling; create it when missing.

    Markdown is the run's default report, so a valid artifact without its
    ``_report.md`` is a recoverable gap — a report write that failed after the
    JSON landed — and this path creates it. That is why it validates harder
    than ``NOT_AN_ARTIFACT`` does: without the existing-sibling guard the HTML
    path has, a metadata-bearing foreign JSON would otherwise gain a bogus
    report. Anything that does not parse as a benchmark artifact is skipped
    cleanly: no file, no error.
    """
    with open(json_path, "r") as handle:
        document = json.load(handle)

    if not isinstance(document, dict):
        return None
    results = document.get("benchmarking_results")
    metadata = document.get("metadata")
    if not results or metadata is None:
        return None

    try:
        config_data, config_name, timestamp, questions, total_results, provenance = (
            parse_benchmark_results(results, metadata)
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None

    md_path = json_path.with_name(json_path.stem + "_report.md")
    verb = "re-rendered" if md_path.exists() else "created"
    if dry_run:
        return (
            f"would re-render {md_path.name}"
            if md_path.exists()
            else f"would create {md_path.name}"
        )

    markdown = format_markdown_output(
        config_data,
        config_name,
        timestamp,
        questions,
        total_results,
        provenance=provenance,
    )
    with open(md_path, "w") as handle:
        handle.write(markdown)
    return f"{verb} {md_path.name}"


def main():
    parser = argparse.ArgumentParser(
        description="Stamp existing benchmark artifacts with a code version "
        "and a config version."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=f"artifact JSON files (default: {DEFAULT_GLOB} under the repo root)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing",
    )
    parser.add_argument(
        "--regenerate-html",
        action="store_true",
        help="also re-render each artifact's existing _report.html",
    )
    parser.add_argument(
        "--regenerate-md",
        action="store_true",
        help="also render each artifact's _report.md (created when missing)",
    )
    args = parser.parse_args()

    if args.paths:
        paths = [Path(p) for p in args.paths]
    else:
        paths = sorted(REPO_ROOT.glob(DEFAULT_GLOB))

    if not paths:
        print(f"No artifacts found (looked for {DEFAULT_GLOB})", file=sys.stderr)
        return 1

    changed = 0
    rendered = 0
    md_rendered = 0
    for path in paths:
        try:
            status = stamp_file(path, dry_run=args.dry_run)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"{path.name}: ERROR {exc}", file=sys.stderr)
            continue

        print(f"{path.name}: {status}")
        if not status.startswith("skipped"):
            changed += 1

        # Deliberately NOT gated on whether the JSON changed. The HTML is a view
        # of the JSON, so it also goes stale when the RENDERER changes -- an
        # already-stamped artifact still needs re-rendering after a report fix.
        # Gating this on `changed` meant a report-format correction silently
        # reached nothing, because every artifact was already stamped.
        if args.regenerate_html and status != NOT_AN_ARTIFACT:
            try:
                note = regenerate_html(path, dry_run=args.dry_run)
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                print(f"{path.name}: ERROR re-rendering {exc}", file=sys.stderr)
                continue
            if note:
                rendered += 1
                print(f"{path.name}: {note}")

        if args.regenerate_md and status != NOT_AN_ARTIFACT:
            try:
                note = regenerate_md(path, dry_run=args.dry_run)
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                print(f"{path.name}: ERROR rendering markdown {exc}", file=sys.stderr)
                continue
            if note:
                md_rendered += 1
                print(f"{path.name}: {note}")

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{changed} of {len(paths)} artifact(s) {verb}.")
    if args.regenerate_html:
        noun = "would re-render" if args.dry_run else "re-rendered"
        print(f"{noun} {rendered} report(s).")
    if args.regenerate_md:
        noun = "would render" if args.dry_run else "rendered"
        print(f"{noun} {md_rendered} markdown report(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
