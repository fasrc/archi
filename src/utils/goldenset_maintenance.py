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
from fnmatch import fnmatchcase
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple
from urllib.parse import urlparse

from src.data_manager.collectors.scrapers.sitemap_source import (
    FetchText,
    SitemapExpansionError,
    SitemapParseError,
    SitemapPolicy,
    expand_sitemaps,
    normalize_page_url,
    parse_sitemap_document,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Parent-source patterns, mirroring the catalog's grouping SQL
# (`catalog_postgres.py` "Group by: domain for web, repo URL for git"). A git
# source contributes one URL per file, so its parent is the repository — that is
# what keeps a large repo from flooding the coverage report.
_WEB_PARENT = re.compile(r"^(https?://[^/]+)")
_GIT_PARENT = re.compile(r"^(https?://[^/]+/[^/]+/[^/]+)")

# Near-miss slug normalization. `-\d+$` strips the collision suffix a WordPress
# KB appends when a slug is reused (`running-jobs-2`); it cannot erase a slug
# that is itself numeric (`/kb/2024`) because that has no leading hyphen.
_HTML_SUFFIX = re.compile(r"\.html?$")
_ALIAS_SUFFIX = re.compile(r"-\d+$")

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


def reconciliation_key(url: str) -> Optional[str]:
    """Return the near-miss grouping key for a URL, or None if it has no slug.

    The key is the final path segment, lowercased, with an `.html` extension and
    a WordPress-style `-N` collision suffix stripped — so a page that merely
    moved prefix (`/kb/x` -> `/docs/x`) or picked up an alias (`/kb/x-2`) shares
    a key with its original. A genuine rename (`running-jobs` ->
    `submitting-jobs`) deliberately does NOT share a key: that is a real gap or
    orphan and the operator must see it, not have it hidden in a review bucket.

    A URL with no path segment (a bare host) has no key and pairs with nothing.
    """
    try:
        path = urlparse(url.strip()).path
    except ValueError:
        return None
    slug = path.rstrip("/").rsplit("/", 1)[-1].lower()
    slug = _ALIAS_SUFFIX.sub("", _HTML_SUFFIX.sub("", slug))
    return slug or None


@dataclass(frozen=True)
class NearMiss:
    """A URL that matches the other side only by reconciliation key.

    Reported for human review and never classified as a definitive coverage gap
    or orphan — the pair may be the same page under a moved slug, or two genuinely
    different pages, and only a human can tell.
    """

    url: str
    candidates: Tuple[str, ...]
    key: str


@dataclass(frozen=True)
class Reconciliation:
    """Partition of subject URLs against a reference set of URLs."""

    matched: Tuple[str, ...]
    near_misses: Tuple[NearMiss, ...]
    unmatched: Tuple[str, ...]


def reconcile(
    subject_urls: Iterable[str], reference_urls: Iterable[str]
) -> Reconciliation:
    """Classify each subject URL against a reference set: exact / near / absent.

    Direction-agnostic, so both passes share one rule: coverage runs corpus URLs
    against the bank's `sources`, orphan detection runs the bank's `sources`
    against the freshly expanded live inventory. Both sides are canonicalized
    with the ingest's own normalizer first, so scheme/host case, fragments and
    the `/x` vs `/x/` split (#118) can never masquerade as a difference.
    Unparseable URLs are dropped on either side rather than crashing a read-only
    report. Subject order is preserved and duplicates collapse.

    A near-miss additionally requires the **same normalized host**. The slug key
    alone is far too weak across hosts: the bank cites external authorities (the
    upstream Slurm docs), so an unscoped key would let `slurm.schedmd.com/mpi`
    "reconcile" a KB page `…/kb/mpi` and hide a genuine coverage gap behind a
    bogus pairing. Coverage has no scope guard of its own — `find_orphans` filters
    foreign hosts before calling this, but `find_coverage_gaps` does not — so the
    constraint lives here, where both passes get it.
    """
    by_key: Dict[Tuple[str, str], List[str]] = {}
    reference: Set[str] = set()
    for raw in reference_urls:
        url = canonical_url(raw)
        if url is None or url in reference:
            continue
        reference.add(url)
        key = reconciliation_key(url)
        if key is not None:
            by_key.setdefault((_host_of(url), key), []).append(url)

    matched: List[str] = []
    near_misses: List[NearMiss] = []
    unmatched: List[str] = []
    seen = set()
    for raw in subject_urls:
        url = canonical_url(raw)
        if url is None or url in seen:
            continue
        seen.add(url)
        if url in reference:
            matched.append(url)
            continue
        key = reconciliation_key(url)
        candidates = by_key.get((_host_of(url), key)) if key is not None else None
        if key is not None and candidates:
            near_misses.append(
                NearMiss(url=url, candidates=tuple(sorted(candidates)), key=key)
            )
        else:
            unmatched.append(url)
    return Reconciliation(
        matched=tuple(matched),
        near_misses=tuple(near_misses),
        unmatched=tuple(unmatched),
    )


def read_corpus_docs(fetch_rows: CorpusRowFetcher) -> List[CorpusDoc]:
    """Read the ingested corpus as canonical `CorpusDoc`s, deduped by URL.

    Mirrors the ingestion-verifier read but takes the fetcher as an argument so
    the pure shaping logic is unit-testable without a database. Rows are skipped
    when they carry no usable URL, when the URL will not parse, when they are
    soft-deleted, or when they are not **retrievable**; slash/case/fragment
    variants of one page collapse to a single doc, in first-seen order.

    Retrievability lives here rather than in either fetcher so both corpus
    inputs — the live `--pg-dsn` query and an offline `--corpus-json` dump —
    agree by construction. `ingestion_status` is one of
    pending/embedding/embedded/failed and rows are inserted as `pending`; only
    `embedded` has chunks the retriever can serve, so anything else would have
    coverage ask for a golden question the agent cannot answer.

    A row that declares **no** `ingestion_status` is kept: a dump omitting the
    column cannot be judged, and dropping those rows would empty the report and
    read as "fully covered" — a silent false clean, the same failure class the
    orphan abstention guard exists to prevent. Over-reporting a gap is visible
    and cheap; under-reporting hides work.

    The row's `resource_hash` is deliberately ignored: it is `md5(url)`, so it
    changes only when the URL does and never signals a content change.
    """
    docs: List[CorpusDoc] = []
    seen = set()
    for row in fetch_rows():
        if row.get("is_deleted"):
            continue
        status = row.get("ingestion_status")
        if status is not None and status != "embedded":
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


def bank_source_urls(bank: Iterable[Any]) -> List[str]:
    """Return every URL the bank grounds against, canonicalized and deduped.

    Rows with no `sources` — a `should_refuse` row intentionally carries none —
    contribute nothing, and a row whose `sources` is missing or not a list is
    skipped rather than raising: a read-only report must survive a hand-edited
    bank.
    """
    urls: List[str] = []
    seen = set()
    for record in bank or []:
        if not isinstance(record, dict):
            continue
        sources = record.get("sources")
        if not isinstance(sources, list):
            continue
        for raw in sources:
            if not isinstance(raw, str) or not raw:
                continue
            url = canonical_url(raw)
            if url is None or url in seen:
                continue
            seen.add(url)
            urls.append(url)
    return urls


@dataclass(frozen=True)
class CoverageReport:
    """Which ingested pages the bank does and does not ground against."""

    gaps: Tuple[CorpusDoc, ...]
    covered: Tuple[CorpusDoc, ...]
    needs_reconciliation: Tuple[NearMiss, ...]


def find_coverage_gaps(
    corpus_docs: Iterable[CorpusDoc], bank: Iterable[Any]
) -> CoverageReport:
    """Report ingested pages that no current bank row references in `sources`.

    Covered-ness is re-derived from the bank on every run, so drafting
    candidates for a page never marks it covered — only an applied bank row
    citing it does. A page that matches a bank source only by slug near-miss is
    reported for reconciliation instead of being called a gap.
    """
    docs = list(corpus_docs)
    by_url = {doc.url: doc for doc in docs}
    result = reconcile(by_url, bank_source_urls(bank))
    return CoverageReport(
        gaps=tuple(by_url[url] for url in result.unmatched if url in by_url),
        covered=tuple(by_url[url] for url in result.matched if url in by_url),
        needs_reconciliation=result.near_misses,
    )


def group_by_parent(docs: Iterable[CorpusDoc]) -> Dict[str, Tuple[CorpusDoc, ...]]:
    """Bucket docs by their parent source, in first-seen order.

    A single git source can contribute thousands of per-file URLs; grouping is
    what lets an operator greenlight or dismiss a whole source at once instead of
    reading a flat dump.
    """
    grouped: Dict[str, List[CorpusDoc]] = {}
    for doc in docs:
        grouped.setdefault(doc.parent, []).append(doc)
    return {parent: tuple(items) for parent, items in grouped.items()}


def filter_docs(
    docs: Iterable[CorpusDoc],
    *,
    source_type: Optional[str] = None,
    parent: Optional[str] = None,
    path_glob: Optional[str] = None,
) -> Tuple[CorpusDoc, ...]:
    """Narrow docs to one source or path. Filters combine conjunctively.

    `path_glob` is matched case-sensitively against the **full URL**, so an
    operator can paste a prefix straight from the report
    (`https://github.com/org/repo/blob/dev/docs/*`).
    """
    selected: List[CorpusDoc] = []
    for doc in docs:
        if source_type is not None and doc.source_type != source_type:
            continue
        if parent is not None and doc.parent != parent:
            continue
        if path_glob is not None and not fnmatchcase(doc.url, path_glob):
            continue
        selected.append(doc)
    return tuple(selected)


SITEMAP_PREFIX = "sitemap-"
SSO_PREFIX = "sso-"

# Source types that fan out into MANY sub-documents at ingest: a git source
# ingests one document per file, and elog/indico expand into per-entry pages.
# The inventory cannot enumerate those without cloning or crawling, so such a
# source is recorded as unsupported and its host is deliberately kept OUT of the
# inventory. `find_orphans` scopes itself to hosts the inventory actually
# contains, so bank rows on those hosts are reported out-of-scope instead of
# being proposed for prune. Mirrors `ScraperManager._collect_urls_from_lists_by_type`.
FANOUT_PREFIXES = ("git-", "elog-", "indico-")


def _is_fanout_url(url: str) -> bool:
    """Mirror the ingest's UNPREFIXED elog/indico auto-detection.

    `ScraperManager._is_elog_url` matches `/elog/` or `/elogs/` in the path, and
    `_is_indico_url` matches `/event/` on an Indico host. Both expand into many
    per-entry documents, so the inventory cannot enumerate them.

    The ingest's Indico check also consults the configured `indico.base_url`,
    which is not available to this read-only tool; an Indico instance whose host
    does not contain "indico" must therefore use the explicit `indico-` prefix to
    be recognized here.
    """
    parts = urlparse(url)
    path = (parts.path or "").lower()
    if "/elog/" in path or "/elogs/" in path:
        return True
    return "/event/" in path and "indico" in (parts.netloc or "").lower()


@dataclass(frozen=True)
class LiveInventory:
    """The set of URLs the configured sources currently advertise.

    `complete` is the load-bearing field. `expand_sitemaps` fails **open** per
    document — a sitemap that will not fetch or parse contributes zero URLs with
    only a WARNING — so a healthy-looking expansion can silently be missing a
    whole branch of the KB. Orphan detection must never run against a partial
    inventory, because every unlisted page would look deleted.

    `unsupported` lists source lines whose type fans out into sub-documents this
    tool cannot enumerate (git/elog/indico). They contribute no URLs, so their
    hosts stay out of scope rather than generating false orphans — recorded so
    that a skipped source is visible instead of silently ignored.
    """

    urls: Tuple[str, ...]
    complete: bool
    failures: Tuple[str, ...]
    unsupported: Tuple[str, ...] = ()


def build_live_inventory(
    source_lines: Iterable[str],
    fetch_text: FetchText,
    policy: Optional[SitemapPolicy] = None,
) -> LiveInventory:
    """Expand the current source list into the live URL inventory.

    `sitemap-<url>` lines are expanded with the ingest's own `expand_sitemaps`;
    every other non-blank, non-comment line is a hand-listed page and is its own
    inventory entry. The fetch callable is injected, so this is testable with
    fixture XML and no network.

    Completeness is tracked because the expander cannot report it: the injected
    fetch is wrapped so a failed fetch is recorded, and each returned document is
    re-parsed here so a document the expander silently dropped as unparseable is
    recorded too. A source-level `SitemapExpansionError` (below floor / over cap)
    and an empty inventory also mark the run incomplete — nothing to compare
    against must never be read as "everything was removed".
    """
    policy = policy or SitemapPolicy()
    failures: List[str] = []

    def recording_fetch(url: str) -> str:
        """Fetch as normal, but record what the expander is about to swallow."""
        try:
            text = fetch_text(url)
        except Exception as exc:
            # Re-raised so the expander's own fail-open path still runs, but
            # recorded so the caller can tell that a fail-open ran at all.
            failures.append(f"{url}: {exc}")
            raise
        try:
            parse_sitemap_document(text)
        except SitemapParseError as exc:
            # The expander drops an unparseable document per-document; without
            # recording it here, a partially-expanded index looks complete.
            failures.append(f"{url}: {exc}")
        return text

    hand_listed: List[str] = []
    sitemap_urls: List[str] = []
    unsupported: List[str] = []
    for raw_line in source_lines:
        line = (raw_line or "").strip()
        if not line or line.startswith("#"):
            continue
        # `sources.list` supports a `URL,depth` suffix. The ingest drops it in
        # `ScraperManager._extract_urls_from_file` BEFORE prefix routing, so the
        # oracle must parse identically — otherwise the inventory holds
        # `…/kb/a,2` while the corpus holds `…/kb/a`, and every bank row citing a
        # still-published page reads as removed (and `sitemap-…/x.xml,2` would
        # fetch a URL that cannot exist, failing the run into abstention).
        line = line.split(",", 1)[0].strip()
        if not line:
            continue
        # Mirror the ingest's prefix routing. `sitemap-` is peeled FIRST so a
        # sitemap URL whose path happens to contain `/elog/` still expands as a
        # sitemap (the ingest's explicit-prefix-beats-heuristic rule). `sso-` only
        # tells the ingest to authenticate, so the line is still exactly one page.
        if line.startswith(SITEMAP_PREFIX):
            sitemap_urls.append(line[len(SITEMAP_PREFIX) :])
        elif line.startswith(SSO_PREFIX):
            hand_listed.append(line[len(SSO_PREFIX) :])
        elif line.startswith(FANOUT_PREFIXES) or _is_fanout_url(line):
            unsupported.append(line)
        else:
            hand_listed.append(line)

    expanded: List[str] = []
    if sitemap_urls:
        try:
            expanded = expand_sitemaps(sitemap_urls, recording_fetch, policy)
        except SitemapExpansionError as exc:
            failures.append(f"{exc.source_url}: {exc.reason} ({exc.count} pages)")

    urls: List[str] = []
    seen = set()
    for raw in list(hand_listed) + expanded:
        url = canonical_url(raw)
        if url is None or url in seen:
            continue
        seen.add(url)
        urls.append(url)

    if not urls:
        failures.append("live inventory is empty")
    return LiveInventory(
        urls=tuple(urls),
        complete=not failures,
        failures=tuple(failures),
        unsupported=tuple(unsupported),
    )


@dataclass(frozen=True)
class Orphan:
    """A bank row whose grounding page is gone from the live KB."""

    row_index: int
    user_input: str
    urls: Tuple[str, ...]


@dataclass(frozen=True)
class OrphanReport:
    """Rows to propose for prune or conversion — never deleted automatically."""

    orphans: Tuple[Orphan, ...]
    out_of_scope: Tuple[str, ...]
    needs_reconciliation: Tuple[NearMiss, ...]
    abstained: bool
    reasons: Tuple[str, ...]


def _host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def find_orphans(bank: Iterable[Any], inventory: LiveInventory) -> OrphanReport:
    """Flag rows whose `sources` URL is absent from the LIVE source inventory.

    Keyed on the live inventory rather than the corpus, because the corpus never
    prunes: a page removed upstream still has a corpus row, so corpus presence
    proves nothing (design D6).

    Two guards keep the pass honest:

    - **Abstention.** If the inventory is incomplete, nothing is flagged at all.
    - **Scope.** The inventory is authoritative only for the hosts it actually
      contains. A `sources` URL on any other host (an external authority such as
      the upstream Slurm docs) was never in the KB to be removed, so it is
      reported as out-of-scope rather than judged.

    A slug near-miss is reconciliation-needed, and a `should_refuse` row's empty
    `sources` yields nothing. Read-only: the bank is never mutated.
    """
    if not inventory.complete:
        return OrphanReport(
            orphans=(),
            out_of_scope=(),
            needs_reconciliation=(),
            abstained=True,
            reasons=inventory.failures,
        )

    in_scope_hosts = {_host_of(url) for url in inventory.urls}
    out_of_scope: List[str] = []
    seen_out = set()
    near_by_url: Dict[str, NearMiss] = {}
    orphans: List[Orphan] = []

    for index, record in enumerate(bank):
        if not isinstance(record, dict):
            continue
        sources = record.get("sources")
        if not isinstance(sources, list) or not sources:
            continue
        judged: List[str] = []
        for raw in sources:
            if not isinstance(raw, str) or not raw:
                continue
            url = canonical_url(raw)
            if url is None:
                continue
            if _host_of(url) not in in_scope_hosts:
                if url not in seen_out:
                    seen_out.add(url)
                    out_of_scope.append(url)
                continue
            judged.append(url)
        if not judged:
            continue
        result = reconcile(judged, inventory.urls)
        for near in result.near_misses:
            near_by_url.setdefault(near.url, near)
        if result.unmatched:
            orphans.append(
                Orphan(
                    row_index=index,
                    user_input=str(record.get("user_input") or ""),
                    urls=result.unmatched,
                )
            )
    return OrphanReport(
        orphans=tuple(orphans),
        out_of_scope=tuple(out_of_scope),
        needs_reconciliation=tuple(near_by_url.values()),
        abstained=False,
        reasons=(),
    )
