"""Repair scraper markers on web rows that lost them before fix #640.

A link re-fetch can overwrite `extra_json` and erase a stored `scraper` marker.
This module plans and applies repairs from explicit `elog-` / `indico-` entries
in the operator's input-list files.  It never guesses from URL shape: shape is
used only to decide which unmarked rows to *report* as unresolved.

Usage (via the maintenance script):
    python scripts/maintenance/repair_scraper_markers.py \\
        --pg-dsn <dsn> --input-list <path> [--input-list <path> ...] [--apply]
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Iterable, List, Optional, Sequence, Tuple, Union
from urllib.parse import urlsplit, urlunsplit

from src.cli.source_registry import (
    is_elog_url,
    read_input_list_entries,
    split_prefixed_entry,
)

MARKER_SOURCES = ("elog", "indico")


@dataclass(frozen=True)
class ListEntries:
    explicit: Tuple[Tuple[str, str], ...]  # (source, url) in list order
    plain: FrozenSet[str]  # normalize_url() of each unprefixed entry


@dataclass(frozen=True)
class MarkerRepair:
    resource_hash: str
    url: str
    source: str
    entry: str  # the list entry URL that covers the row (without its prefix)


@dataclass(frozen=True)
class UnresolvedRow:
    resource_hash: str
    url: str
    reason: str


@dataclass(frozen=True)
class RepairPlan:
    repairs: Tuple[MarkerRepair, ...]
    unresolved: Tuple[UnresolvedRow, ...]


def normalize_url(url: str) -> str:
    """Lowercase scheme and host, strip trailing slash from path, drop query/fragment."""
    parts = urlsplit(url.strip())
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


def entry_scope(source: str, url: str) -> Optional[str]:
    """Return the normalized URL prefix that an explicit entry covers.

    For elog, the whole normalized logbook URL. For indico, the URL cut just
    after /event/<id>. Returns None when no event id is present (category entry).
    """
    if source == "elog":
        return normalize_url(url)
    if source == "indico":
        parts = urlsplit(url.strip())
        m = re.search(r"/event/(\d+)", parts.path, re.IGNORECASE)
        if not m:
            return None
        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path[: m.end()],
                "",
                "",
            )
        )
    return None


def read_list_entries(paths: Sequence[Union[str, Path]]) -> ListEntries:
    """Parse list files and split entries into explicit (elog/indico) and plain sets.

    A missing or unreadable file raises OSError. Sitemap, git, and sso entries
    are ignored. Each plain entry is stored as its normalized URL.
    """
    explicit: List[Tuple[str, str]] = []
    plain: set = set()
    for path in paths:
        for entry in read_input_list_entries(path):
            parsed = split_prefixed_entry(entry)
            if parsed is not None:
                source, entry_url = parsed
                if source in ("elog", "indico"):
                    explicit.append((source, entry_url))
            else:
                if entry.startswith("sitemap-"):
                    continue
                plain.add(normalize_url(entry))
    return ListEntries(explicit=tuple(explicit), plain=frozenset(plain))


def plan_marker_repairs(
    rows: Iterable[Tuple[str, Optional[str]]],
    entries: ListEntries,
) -> RepairPlan:
    """Build a repair plan: which unmarked rows can be assigned a marker.

    Rows whose url is empty or None are skipped. A row is repaired only when
    exactly one explicit entry covers it and it is not also a plain link.
    Shaped rows (elog- or indico-URL) that no entry covers are reported as
    unresolved. Plain rows that nothing covers are silently skipped.
    """
    repairs: List[MarkerRepair] = []
    unresolved: List[UnresolvedRow] = []

    for resource_hash, url in rows:
        if not url:
            continue
        n = normalize_url(url)

        # First covering entry per source, in list order.
        covering: dict = {}
        for source, entry_url in entries.explicit:
            if source in covering:
                continue
            scope = entry_scope(source, entry_url)
            if scope is None:
                continue
            if n == scope or n.startswith(scope + "/"):
                covering[source] = entry_url

        if len(covering) > 1:
            sources_str = ", ".join(sorted(covering))
            unresolved.append(
                UnresolvedRow(
                    resource_hash,
                    url,
                    f"covered by entries of more than one source: {sources_str}",
                )
            )
        elif len(covering) == 1:
            source, entry_url = next(iter(covering.items()))
            if n in entries.plain:
                unresolved.append(
                    UnresolvedRow(resource_hash, url, "also listed as a plain link")
                )
            else:
                repairs.append(MarkerRepair(resource_hash, url, source, entry_url))
        else:
            parts = urlsplit(url)
            indico_shaped = (
                "indico" in parts.netloc.lower() and "/event/" in parts.path.lower()
            )
            if is_elog_url(url) or indico_shaped:
                unresolved.append(
                    UnresolvedRow(
                        resource_hash,
                        url,
                        "no explicit elog- or indico- entry covers this row",
                    )
                )

    return RepairPlan(repairs=tuple(repairs), unresolved=tuple(unresolved))


def format_plan(plan: RepairPlan) -> List[str]:
    """Render the repair plan as a list of human-readable lines."""
    lines: List[str] = []
    for r in plan.repairs:
        lines.append(f"repair {r.source} {r.resource_hash} {r.url} (entry {r.entry})")
    for u in plan.unresolved:
        lines.append(f"unresolved {u.resource_hash} {u.url}: {u.reason}")
    lines.append(f"{len(plan.repairs)} repair(s), {len(plan.unresolved)} unresolved")
    return lines


SELECT_UNMARKED_WEB_ROWS_SQL = """
    SELECT resource_hash, url
    FROM documents
    WHERE NOT is_deleted
      AND source_type = 'web'
      AND COALESCE(jsonb_typeof(extra_json -> 'scraper'), 'null') = 'null'
    ORDER BY resource_hash
"""

UPDATE_MARKER_SQL = """
    UPDATE documents
    SET extra_json = COALESCE(extra_json, '{}'::jsonb)
        || jsonb_build_object('scraper', %s::text)
    WHERE resource_hash = %s
      AND NOT is_deleted
      AND COALESCE(jsonb_typeof(extra_json -> 'scraper'), 'null') = 'null'
"""


def read_unmarked_web_rows(conn) -> List[Tuple[str, Optional[str]]]:
    """Return all non-deleted web rows that have no scraper marker."""
    with conn.cursor() as cur:
        cur.execute(SELECT_UNMARKED_WEB_ROWS_SQL)
        return [(row[0], row[1]) for row in cur.fetchall()]


def apply_marker_repairs(conn, repairs: Sequence[MarkerRepair]) -> int:
    """Write scraper markers for each repair; commit once. Return count of rows written.

    The UPDATE guard skips any row that already has a marker since the read.
    On any exception, rolls back and re-raises without committing.
    """
    try:
        with conn.cursor() as cur:
            written = 0
            for repair in repairs:
                cur.execute(UPDATE_MARKER_SQL, (repair.source, repair.resource_hash))
                written += cur.rowcount
        conn.commit()
        return written
    except Exception:
        conn.rollback()
        raise


def run_repair(conn, entries: ListEntries, apply: bool) -> List[str]:
    """Plan and optionally apply marker repairs; return lines for printing.

    If apply is False (dry run), no UPDATE is executed and commit is never called.
    """
    plan = plan_marker_repairs(read_unmarked_web_rows(conn), entries)
    lines = format_plan(plan)
    if apply:
        written = apply_marker_repairs(conn, plan.repairs)
        lines.append(f"wrote {written} of {len(plan.repairs)} marker(s)")
    else:
        lines.append("dry run: nothing written; rerun with --apply to write")
    return lines
