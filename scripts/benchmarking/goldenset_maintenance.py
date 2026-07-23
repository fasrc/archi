#!/usr/bin/env python
"""Read-only maintenance passes over the RAGAS golden-set question bank.

Answers two questions an operator otherwise has to eyeball by hand:

- ``coverage`` — which ingested KB pages does no bank row ground against?
- ``orphans`` — which bank rows cite a page the live KB no longer publishes?

Both passes are **proposal-only**: they print work lists and leave the bank file
byte-unchanged. Adding a question, locking a reference, or pruning an orphan is a
separate, explicitly human-initiated step.

Exit codes follow the cron contract: **0 even when there are findings** (gaps and
orphans are work to do, not a broken run), non-zero only on operational failure —
an unreadable bank, corpus, or source list.

Usage:
    # coverage against a JSON dump of the corpus (hermetic / offline)
    python scripts/benchmarking/goldenset_maintenance.py coverage \\
        --bank examples/benchmarking/fasrc_ragas_queries.json \\
        --corpus-json corpus.json [--source-type web] [--path-glob 'https://…/kb/*']

    # coverage straight from the live catalog
    python scripts/benchmarking/goldenset_maintenance.py coverage \\
        --bank <bank.json> --pg-dsn "postgresql://archi@localhost/archi-db"

    # orphans against the current source list (sitemap- lines are expanded live)
    python scripts/benchmarking/goldenset_maintenance.py orphans \\
        --bank <bank.json> --sources deploy/fasrc-dev/sources.list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils.benchmark_schema import normalize_bank  # noqa: E402  # isort: skip
from src.utils.goldenset_maintenance import (  # noqa: E402  # isort: skip
    build_live_inventory,
    filter_docs,
    find_coverage_gaps,
    find_orphans,
    group_by_parent,
    read_corpus_docs,
)


class OperationalError(Exception):
    """A failure of the run itself (unreadable input), not a finding."""


def _load_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise OperationalError(f"cannot read {path}: {exc}") from exc


def load_bank(path: str) -> List[Any]:
    """Load the bank through `benchmark_schema`, so the legacy dialect still works."""
    bank = normalize_bank(_load_json(path))
    if not isinstance(bank, list):
        raise OperationalError(f"{path} is not a bank array")
    return bank


def corpus_rows_from_json(path: str):
    """Row fetcher over a JSON dump of `documents` — for offline runs and tests."""
    rows = _load_json(path)
    if not isinstance(rows, list):
        raise OperationalError(f"{path} is not a list of corpus rows")
    return lambda: rows


def corpus_rows_from_postgres(dsn: str):
    """Row fetcher over the live catalog, mirroring the ingestion-verifier read."""

    def fetch():
        import psycopg2
        import psycopg2.extras

        try:
            with psycopg2.connect(dsn) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT url, source_type FROM documents WHERE NOT is_deleted"
                    )
                    return [dict(row) for row in cur.fetchall()]
        except Exception as exc:  # pragma: no cover - needs a live database
            raise OperationalError(f"cannot read the corpus: {exc}") from exc

    return fetch


def read_source_lines(path: str) -> List[str]:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OperationalError(f"cannot read {path}: {exc}") from exc


def _print_group(title: str, lines: Sequence[str], stream=sys.stdout) -> None:
    print(f"\n{title}", file=stream)
    for line in lines:
        print(f"  {line}", file=stream)


def _sitemap_policy(args: argparse.Namespace, source_lines: Sequence[str]):
    """Build the sitemap policy the live-inventory expansion runs under.

    The floor is the completeness guard, so it must match the deployment's.
    ``SitemapPolicy``'s own default is ``min_pages=1``: under it a truncated
    sitemap (a handful of pages instead of a few hundred) expands
    "successfully", the inventory reads as complete, and every bank URL missing
    from that partial response is reported as an orphan. FASRC configures 150.
    So when the source list actually contains a ``sitemap-`` line, refuse to run
    without an explicit floor rather than silently judging against 1.
    """
    from src.data_manager.collectors.scrapers.sitemap_source import SitemapPolicy

    has_sitemap = any(line.strip().startswith("sitemap-") for line in source_lines)
    if has_sitemap and args.min_pages is None:
        raise OperationalError(
            "refusing to judge orphans against a sitemap without an explicit "
            "--min-pages floor (match the deployment's sitemap.min_pages). The "
            "default floor is 1, so a truncated sitemap would read as complete "
            "and every unlisted bank row would look deleted."
        )
    policy = SitemapPolicy()
    if args.min_pages is not None:
        policy.min_pages = args.min_pages
    if args.max_pages is not None:
        policy.max_pages = args.max_pages
    if args.allowed_hosts:
        policy.allowed_hosts = list(args.allowed_hosts)
    return policy


def run_coverage(args: argparse.Namespace) -> int:
    bank = load_bank(args.bank)
    if args.corpus_json:
        fetch_rows = corpus_rows_from_json(args.corpus_json)
    else:
        fetch_rows = corpus_rows_from_postgres(args.pg_dsn)
    docs = filter_docs(
        read_corpus_docs(fetch_rows),
        source_type=args.source_type,
        parent=args.parent,
        path_glob=args.path_glob,
    )
    report = find_coverage_gaps(docs, bank)

    print(
        f"corpus: {len(docs)} pages | covered: {len(report.covered)} | "
        f"{len(report.gaps)} gaps | {len(report.needs_reconciliation)} need reconciliation"
    )
    if report.gaps:
        for parent, group in group_by_parent(report.gaps).items():
            _print_group(f"gaps — {parent} ({len(group)})", [d.url for d in group])
    if report.needs_reconciliation:
        _print_group(
            "needs reconciliation (slug near-miss — confirm before treating as a gap)",
            [
                f"{near.url}  ~  {', '.join(near.candidates)}"
                for near in report.needs_reconciliation
            ],
        )
    return 0


def run_orphans(args: argparse.Namespace) -> int:
    from src.data_manager.collectors.scrapers.sitemap_source import fetch_sitemap_text

    bank = load_bank(args.bank)
    source_lines = read_source_lines(args.sources)
    policy = _sitemap_policy(args, source_lines)
    inventory = build_live_inventory(source_lines, fetch_sitemap_text, policy)
    report = find_orphans(bank, inventory)

    if report.abstained:
        # An incomplete inventory is an OPERATIONAL failure, not a finding: no
        # orphan analysis happened. Exiting zero here would let a cron read
        # "nothing was flagged" as healthy and hide a broken inventory forever.
        print(
            "ABSTAINED — the live source inventory is incomplete, so nothing was "
            "flagged. Orphan detection needs a complete inventory: a partial one "
            "would make every unlisted page look deleted.",
            file=sys.stderr,
        )
        _print_group("why", report.reasons, stream=sys.stderr)
        return 1

    print(
        f"live inventory: {len(inventory.urls)} URLs | {len(report.orphans)} orphans | "
        f"{len(report.out_of_scope)} out of scope | "
        f"{len(report.needs_reconciliation)} need reconciliation"
    )
    if report.orphans:
        _print_group(
            "orphans (grounding page gone from the live KB — propose, never delete)",
            [
                f"row {o.row_index}: {', '.join(o.urls)}  — {o.user_input[:70]}"
                for o in report.orphans
            ],
        )
    if report.out_of_scope:
        _print_group(
            "out of scope (host the inventory does not cover — never judged)",
            report.out_of_scope,
        )
    if report.needs_reconciliation:
        _print_group(
            "needs reconciliation (slug near-miss)",
            [
                f"{n.url}  ~  {', '.join(n.candidates)}"
                for n in report.needs_reconciliation
            ],
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only coverage / orphan detection for the RAGAS golden-set bank. "
            "Never writes the bank."
        )
    )
    sub = parser.add_subparsers(dest="command")

    def add_bank(sp):
        sp.add_argument("--bank", required=True, help="Path to the bank JSON array.")

    coverage = sub.add_parser("coverage", help="Ingested pages no bank row grounds on.")
    add_bank(coverage)
    source = coverage.add_mutually_exclusive_group(required=True)
    source.add_argument("--corpus-json", help="JSON dump of `documents` rows.")
    source.add_argument("--pg-dsn", help="Postgres DSN for the live catalog.")
    coverage.add_argument("--source-type", help="Only this source_type (web/git/…).")
    coverage.add_argument("--parent", help="Only this parent source (host or repo).")
    coverage.add_argument("--path-glob", help="Only URLs matching this glob.")
    coverage.set_defaults(func=run_coverage)

    orphans = sub.add_parser("orphans", help="Rows whose grounding page is gone.")
    add_bank(orphans)
    orphans.add_argument(
        "--sources",
        required=True,
        help="Source list; `sitemap-` lines are expanded live.",
    )
    orphans.add_argument(
        "--min-pages",
        type=int,
        help=(
            "Sitemap completeness floor — match the deployment's "
            "data_manager.sources.links.sitemap.min_pages (FASRC: 150). Required "
            "when the source list contains a `sitemap-` line; without it a "
            "truncated sitemap reads as complete and yields false orphans."
        ),
    )
    orphans.add_argument(
        "--max-pages",
        type=int,
        help="Sitemap cap — match the deployment's sitemap.max_pages.",
    )
    orphans.add_argument(
        "--allowed-hosts",
        nargs="*",
        help="Extra hosts the sitemap may emit — the deployment's allowed_hosts.",
    )
    orphans.set_defaults(func=run_orphans)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        print("error: a subcommand is required (coverage | orphans)", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except OperationalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
