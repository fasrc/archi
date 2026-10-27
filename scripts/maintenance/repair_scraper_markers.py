#!/usr/bin/env python
"""Restore scraper markers that a link re-fetch erased before fix #640.

Before #640, a plain link re-fetch replaced a row's whole `extra_json` and erased its
`scraper` marker, so ELOG and Indico rows fell back to the `links` source. This script
gives those rows their marker back, but only from an explicit `elog-` or `indico-`
entry in the input lists that you name. It never guesses from URL shape. It reports
the ELOG-shaped and Indico-shaped rows that it cannot place; a row whose URL looks like
a plain link gets no output, so an Indico material linked from a timetable on another
host is not reported.

Usage:
    python scripts/maintenance/repair_scraper_markers.py --pg-dsn <dsn> \\
        --input-list <path> [--input-list <path> ...] [--dry-run | --apply]

The run is a dry run by default: it prints the plan and writes nothing. Add `--apply`
to write the markers. Run it on the host that can reach the stack's Postgres.
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data_manager.collectors.utils.scraper_marker_repair import (  # noqa: E402  # isort: skip
    read_list_entries,
    run_repair,
)


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore scraper markers erased before fix #640 (dry run by default)."
    )
    parser.add_argument("--pg-dsn", required=True, help="Postgres DSN of the stack.")
    parser.add_argument(
        "--input-list",
        required=True,
        action="append",
        help="An input-list file of the deployment; repeat for each file.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and write nothing (the default).",
    )
    mode.add_argument("--apply", action="store_true", help="Write the markers.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        entries = read_list_entries(args.input_list)
    except OSError as exc:
        print(f"error: cannot read input list: {exc}", file=sys.stderr)
        return 2

    import psycopg2

    conn = psycopg2.connect(args.pg_dsn)
    try:
        for line in run_repair(conn, entries, apply=args.apply):
            print(line)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
