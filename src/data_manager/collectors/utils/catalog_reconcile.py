"""
Catalog reconcile pass: scope mapping, collection recording, and gap reporting.

Design: openspec/changes/fix-issue-534-report-uncollected-catalog-rows/design.md (D1, D2, D4)
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from src.utils.logging import get_logger

logger = get_logger(__name__)

_Scope = Tuple[str, Optional[str]]

_INDICO_ELOG = {"indico", "elog"}


def scope_for(metadata: dict) -> _Scope:
    """Return the (source_type, scope_key) pair for a catalog row or persisted resource."""
    source_type = metadata.get("source_type", "")

    if source_type == "git":
        key = metadata.get("parent") or metadata.get("git_repo") or None
        return ("git", key)

    if source_type in ("web", "sso"):
        scraper = metadata.get("scraper")
        if source_type == "web" and scraper in _INDICO_ELOG:
            return ("web", scraper)
        url = metadata.get("url", "") or ""
        netloc = urlparse(url).netloc.lower() if url else None
        return (source_type, netloc or None)

    if source_type == "local_files":
        return ("local_files", "*")

    if source_type == "ticket":
        key = metadata.get("ticket_provider") or None
        return ("ticket", key)

    return (source_type, None)


@dataclass
class CollectionPass:
    """Thread-safe recorder of collected hashes and failures within one ingestion pass."""

    collected: Dict[_Scope, Set[str]] = field(default_factory=dict)
    ran: Set[str] = field(default_factory=set)
    failed: Dict[_Scope, str] = field(default_factory=dict)
    failed_types: Dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    def record_collected(self, resource_hash: str, metadata: dict) -> None:
        source_type, scope_key = scope_for(metadata)
        scope: _Scope = (source_type, scope_key)
        with self._lock:
            self.ran.add(source_type)
            self.collected.setdefault(scope, set()).add(resource_hash)

    def record_failure(
        self, source_type: str, scope_key: Optional[str], reason: str
    ) -> None:
        with self._lock:
            self.ran.add(source_type)
            if scope_key is None:
                self.failed_types[source_type] = reason
            else:
                self.failed[(source_type, scope_key)] = reason


def record_failure(
    persistence: Any,
    source_type: str,
    scope_key: Optional[str],
    reason: str,
) -> None:
    """Forward a failure to the open collection pass. No-op when no pass is open."""
    cp: Optional[CollectionPass] = getattr(persistence, "collection_pass", None)
    if cp is not None:
        cp.record_failure(source_type, scope_key, reason)


# Candidate tuple: (resource_hash, path_or_url, source_type, suffix, scope)
_Candidate = Tuple[str, str, str, str, _Scope]
# Skipped tuple: (scope, reason, row_count)
_Skipped = Tuple[_Scope, str, int]


@dataclass
class ReconcileReport:
    candidates: List[_Candidate]
    skipped: List[_Skipped]
    unscoped_count: int


def find_uncollected(
    rows: List[Tuple[str, dict]],
    collection_pass: CollectionPass,
) -> ReconcileReport:
    """Compute catalog rows not seen in this collection pass (D4)."""
    by_scope: Dict[_Scope, List[Tuple[str, dict]]] = {}
    unscoped_count = 0

    for resource_hash, metadata in rows:
        source_type, scope_key = scope_for(metadata)
        if scope_key is None:
            unscoped_count += 1
            continue
        scope: _Scope = (source_type, scope_key)
        by_scope.setdefault(scope, []).append((resource_hash, metadata))

    candidates: List[_Candidate] = []
    skipped: List[_Skipped] = []

    for scope, scope_rows in by_scope.items():
        source_type, scope_key = scope
        row_count = len(scope_rows)

        # Rule 1: Indico / ELOG always skipped
        if source_type == "web" and scope_key in _INDICO_ELOG:
            skipped.append((scope, "no per-source success signal", row_count))
            continue

        # Rule 2: Source type did not run — no candidates, no warning
        if source_type not in collection_pass.ran:
            continue

        # Rule 3: Whole-type failure
        if source_type in collection_pass.failed_types:
            skipped.append(
                (scope, collection_pass.failed_types[source_type], row_count)
            )
            continue

        # Rule 4: Scope-level failure
        if scope in collection_pass.failed:
            skipped.append((scope, collection_pass.failed[scope], row_count))
            continue

        # Rule 5: Collected zero while catalog holds rows
        collected_hashes = collection_pass.collected.get(scope, set())
        if not collected_hashes:
            reason = f"collected 0 resources while the catalog holds {row_count} rows"
            skipped.append((scope, reason, row_count))
            continue

        # Rule 6: Emit candidates
        for resource_hash, metadata in scope_rows:
            if resource_hash not in collected_hashes:
                path_or_url = metadata.get("url") or metadata.get("path") or ""
                raw_suffix = metadata.get("suffix") or ""
                if raw_suffix:
                    suffix = raw_suffix.lstrip(".")
                elif path_or_url:
                    suffix = Path(urlparse(path_or_url).path).suffix.lstrip(".")
                else:
                    suffix = ""
                candidates.append(
                    (resource_hash, path_or_url, source_type, suffix, scope)
                )

    return ReconcileReport(
        candidates=candidates,
        skipped=skipped,
        unscoped_count=unscoped_count,
    )


def log_reconcile_report(report: ReconcileReport) -> None:
    """Log the reconcile report: one INFO summary, DEBUG per candidate, WARNING per skip."""
    by_type = Counter(c[2] for c in report.candidates)
    by_suffix = Counter(c[3] for c in report.candidates)

    type_summary = ", ".join(f"{n} {t}" for t, n in sorted(by_type.items()))
    suffix_summary = ", ".join(f"{n} {s}" for s, n in sorted(by_suffix.items()))

    logger.info(
        "Reconcile: %d candidate(s) [%s] [%s]; %d scope(s) skipped; %d unscoped row(s)",
        len(report.candidates),
        type_summary,
        suffix_summary,
        len(report.skipped),
        report.unscoped_count,
    )

    for resource_hash, path_or_url, source_type, _suffix, _scope in report.candidates:
        logger.debug("Candidate: %s %s (%s)", resource_hash, path_or_url, source_type)

    for scope, reason, row_count in report.skipped:
        source_type, scope_key = scope
        logger.warning(
            "Skipped scope (%s, %s) [%d rows]: %s",
            source_type,
            scope_key,
            row_count,
            reason,
        )
