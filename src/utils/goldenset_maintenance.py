"""Read-only detection passes for the RAGAS golden-set question bank.

Group 2 of the openspec change `maintain-ragas-goldenset`: read the ingested
corpus, reconcile its page URLs against the bank's `sources`, and report
coverage gaps and orphans. Every pass here is **proposal-only** — nothing in
this module writes the bank file, the corpus, or the live KB.

Two design constraints shape the module (see the change's design D6/D7):

- The persisted corpus is **not** a reliable mirror of the live KB. Ingestion
  upserts by `md5(url)` and skips the content write for a URL it already holds
  (`persistence.persist_resource(..., overwrite=False)`), and the collection
  path never prunes. So the corpus lags in-place edits and keeps pages that were
  removed upstream — corpus *absence* is not evidence of removal, and the corpus
  resource hash carries no content signal at all. Orphan detection therefore
  keys on a freshly expanded **live source inventory**.
- URL reconciliation reuses the ingest's own normalizer
  (`sitemap_source.normalize_page_url`) on both sides, so the bank and the
  corpus are compared in exactly the canonical form the ingest stores rather
  than through a bespoke second normalizer that could drift from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Mapping, Optional

from src.data_manager.collectors.scrapers.sitemap_source import normalize_page_url
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Parent-source patterns, mirroring the catalog's grouping SQL
# (`catalog_postgres.py` "Group by: domain for web, repo URL for git"). A git
# source contributes one URL per file, so its parent is the repository — that is
# what keeps a large repo from flooding the coverage report.
_WEB_PARENT = re.compile(r"^(https?://[^/]+)")
_GIT_PARENT = re.compile(r"^(https?://[^/]+/[^/]+/[^/]+)")

#: Returns the live `documents` rows — injected so tests need no Postgres.
CorpusRowFetcher = Callable[[], Iterable[Mapping[str, Any]]]


@dataclass(frozen=True)
class CorpusDoc:
    """One ingested KB page: its canonical URL and how it was sourced.

    `url` is normalized with the ingest's own `normalize_page_url`. `parent` is
    the grouping label (host for web, repo for git) used to keep the coverage
    report per-source rather than a flat dump.
    """

    url: str
    source_type: str
    parent: str


def parent_source(url: str, source_type: str) -> str:
    """Return the grouping label for a document, mirroring the catalog's CASE."""
    if source_type == "web":
        match = _WEB_PARENT.match(url)
        return match.group(1) if match else url
    if source_type == "git":
        match = _GIT_PARENT.match(url)
        return match.group(1) if match else url
    if source_type == "local_files":
        return "Local files"
    if source_type == "jira":
        return "Jira"
    return source_type or "Unknown"


def canonical_url(url: str) -> Optional[str]:
    """Normalize a URL to the form the ingest stores, or None if unparseable.

    Wraps `sitemap_source.normalize_page_url` so a malformed URL — in the corpus
    or in a bank row's `sources` — is dropped with a warning rather than
    crashing a read-only report.
    """
    try:
        return normalize_page_url(url)
    except ValueError:
        logger.warning("goldenset: dropping unparseable URL %r", url)
        return None


def read_corpus_docs(fetch_rows: CorpusRowFetcher) -> List[CorpusDoc]:
    """Read the ingested corpus as canonical `CorpusDoc`s, deduped by URL.

    Mirrors the ingestion-verifier read (`SELECT url, source_type FROM documents
    WHERE NOT is_deleted`) but takes the fetcher as an argument so the pure
    shaping logic is unit-testable without a database. Rows are skipped when
    they carry no usable URL, when the URL will not parse, or when they are
    soft-deleted; slash/case/fragment variants of one page collapse to a single
    doc, in first-seen order.

    The row's `resource_hash` is deliberately ignored: it is `md5(url)`, so it
    changes only when the URL does and never signals a content change.
    """
    docs: List[CorpusDoc] = []
    seen = set()
    for row in fetch_rows():
        if row.get("is_deleted"):
            continue
        raw_url = row.get("url")
        if not raw_url:
            continue
        url = canonical_url(raw_url)
        if url is None or url in seen:
            continue
        seen.add(url)
        source_type = row.get("source_type") or ""
        docs.append(
            CorpusDoc(
                url=url,
                source_type=source_type,
                parent=parent_source(url, source_type),
            )
        )
    return docs
