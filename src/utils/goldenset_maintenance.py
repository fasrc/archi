"""Read-only detection passes for the RAGAS golden-set question bank.

The openspec change `maintain-ragas-goldenset`: read the ingested corpus,
reconcile its page URLs against the bank's `sources`, and report the three ways
the bank falls out of sync with the KB — coverage gaps, orphaned rows, and fact
drift. Every pass here is **proposal-only**: nothing in this module writes the
bank file, the corpus, or the live KB.

Two design constraints shape the module (see the change's design D6/D7):

- The persisted corpus is **not** a reliable mirror of the live KB. Ingestion
  upserts by `md5(url)` and skips the content write for a URL it already holds
  (`persistence.persist_resource(..., overwrite=False)`), and the collection
  path never prunes. So the corpus lags in-place edits and keeps pages that were
  removed upstream — corpus *absence* is not evidence of removal, and the corpus
  resource hash carries no content signal at all. Orphan detection therefore
  keys on a freshly expanded **live source inventory**, and drift on a live
  re-fetch.
- URL reconciliation reuses the ingest's own normalizer
  (`sitemap_source.normalize_page_url`) on both sides, so the bank and the
  corpus are compared in exactly the canonical form the ingest stores rather
  than through a bespoke second normalizer that could drift from it. Drift
  extends the same rule to page *content*: it hashes text produced by the
  ingest's own extraction (`processing.html_to_markdown`), so markup churn
  cannot masquerade as a fact change.

The three passes deliberately read different oracles, because they ask different
questions. Coverage and `--propose` read the **persisted** corpus, since a golden
question must be answerable from what the retriever actually serves. Orphans read
the **live source inventory**, since the corpus never prunes. Drift re-fetches the
**live page**, since the corpus lags edits and would agree with a stale reference.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple
from urllib.parse import urlparse

from src.data_manager.collectors.processing import html_to_markdown
from src.data_manager.collectors.scrapers.sitemap_source import (
    FetchText,
    SitemapExpansionError,
    SitemapParseError,
    SitemapPolicy,
    expand_sitemaps,
    is_url_allowed,
    normalize_page_url,
    parse_sitemap_document,
)
from src.utils.benchmark_schema import row_status
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
    report per-source rather than a flat dump. `file_path` is the persisted
    document — the *converted* text that was chunked and embedded, which is what
    the retriever actually serves and therefore the only honest thing to ground
    a golden question in. It defaults to empty so a JSON dump that omits the
    column still produces a gap report.
    """

    url: str
    source_type: str
    parent: str
    file_path: str = ""


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
        file_path = row.get("file_path")
        docs.append(
            CorpusDoc(
                url=url,
                source_type=source_type,
                parent=parent_source(url, source_type),
                file_path=file_path if isinstance(file_path, str) else "",
            )
        )
    return docs


def resolve_persisted_path(file_path: str, data_path: str) -> Optional[Path]:
    """Locate the persisted document on disk, contained under the data root.

    `documents.file_path` is stored relative to the deployment's data path — or
    absolute, which `catalog_postgres._resolve_path` accepts as already resolved.
    Both forms are honored here, but the **resolved** path must sit under the
    resolved data root or this raises.

    Containment is not optional politeness. `file_path` arrives from the catalog
    or from an operator-supplied `--corpus-json` dump, and the file it names is
    read and sent to an external model provider — so an unchecked `..` or
    absolute path is a file-disclosure channel out of the machine. The same check
    catches the boring case: a stale path that would silently ground a golden
    question in an unrelated file.

    Both sides are `resolve()`d, so a symlink inside the data root that points
    out of it is caught too, and containment is compared by path component so a
    sibling root (`/srv/data-old` against `/srv/data`) is not mistaken for a
    child. Returns None only when the row carries no path at all.
    """
    if not file_path:
        return None
    root = Path(data_path).resolve()
    candidate = Path(file_path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"persisted document {file_path!r} resolves to {resolved}, outside the "
            f"data root {root} — refusing to read it"
        ) from None
    return resolved


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
    """Which ingested pages the bank does and does not ground against.

    `suppressed` is reported rather than filtered away: a report that silently
    hides pages reads as clean when it is not.
    """

    gaps: Tuple[CorpusDoc, ...]
    covered: Tuple[CorpusDoc, ...]
    needs_reconciliation: Tuple[NearMiss, ...]
    suppressed: Tuple[CorpusDoc, ...] = ()


def find_coverage_gaps(
    corpus_docs: Iterable[CorpusDoc],
    bank: Iterable[Any],
    *,
    declined: Iterable[str] = (),
) -> CoverageReport:
    """Report ingested pages that no current bank row references in `sources`.

    Covered-ness is re-derived from the bank on every run, so drafting
    candidates for a page never marks it covered — only an applied bank row
    citing it does. A page that matches a bank source only by slug near-miss is
    reported for reconciliation instead of being called a gap.

    `declined` holds URLs an operator explicitly dismissed (the decision
    ledger). They move to `suppressed` rather than to `covered`: a declined page
    has no question and never will, so calling it covered would overstate the
    bank's reach. A decline is only ever consulted for a page that is *still* a
    gap — once a row cites the page it is covered, decline or not.
    """
    docs = list(corpus_docs)
    by_url = {doc.url: doc for doc in docs}
    result = reconcile(by_url, bank_source_urls(bank))
    skip = {url for url in (canonical_url(raw) for raw in declined) if url}
    gaps = [by_url[url] for url in result.unmatched if url in by_url]
    return CoverageReport(
        gaps=tuple(doc for doc in gaps if doc.url not in skip),
        covered=tuple(by_url[url] for url in result.matched if url in by_url),
        needs_reconciliation=result.near_misses,
        suppressed=tuple(doc for doc in gaps if doc.url in skip),
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
    tool cannot enumerate (git/elog/indico). They contribute no URLs — recorded so
    that a skipped source is visible instead of silently ignored.

    `unsupported_scopes` is the canonical URL subtree each of those sources owns.
    Host-level scope alone is not enough: a hand-listed page can put a host in
    scope while a fan-out source on the SAME host contributes nothing, and every
    per-file URL under it would then be judged against an inventory that cannot
    contain it. Scoping by subtree keeps those rows out of the orphan pass while
    still judging unrelated paths on the same host.
    """

    urls: Tuple[str, ...]
    complete: bool
    failures: Tuple[str, ...]
    unsupported: Tuple[str, ...] = ()
    unsupported_scopes: Tuple[str, ...] = ()


def _in_unsupported_scope(url: str, scopes: Iterable[str]) -> bool:
    """True when `url` falls under a source the inventory cannot enumerate.

    Matched on a path boundary so `…/org/repo` never swallows `…/org/repo2`:
    exempting a whole host would trade a false orphan for a silently missed one.
    """
    for scope in scopes:
        if url == scope or url.startswith(scope.rstrip("/") + "/"):
            return True
    return False


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
    unsupported_scopes: List[str] = []
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
            bare = line
            for prefix in FANOUT_PREFIXES:
                if bare.startswith(prefix):
                    bare = bare[len(prefix) :]
                    break
            scope = canonical_url(bare)
            if scope is not None:
                unsupported_scopes.append(scope)
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
        unsupported_scopes=tuple(unsupported_scopes),
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
            if _in_unsupported_scope(url, inventory.unsupported_scopes) or (
                _host_of(url) not in in_scope_hosts
            ):
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


# --------------------------------------------------------------------------- #
# Decision ledger — DECLINES only (design D3)
# --------------------------------------------------------------------------- #
# The ledger records the one decision the conversational greenlight path cannot
# otherwise remember: "I looked at this page and it does not earn a question."
# It deliberately does NOT record "drafted" or "covered". Covered-ness is
# re-derived from the bank every run, so a page whose candidates were proposed
# and then abandoned stays a visible gap until a row actually lands. A ledger
# that also suppressed "drafted" URLs would make that abandoned page read as
# clean forever — the silent-false-negative failure this whole module avoids.


@dataclass(frozen=True)
class Decline:
    """One operator dismissal of an uncovered page.

    `url` is stored canonical, so a ledger hand-edited with a trailing slash
    still matches the corpus — a decline that silently stops working is worse
    than no decline at all.
    """

    url: str
    reason: str = ""
    at: str = ""


def read_declines(entries: Any) -> Tuple[Decline, ...]:
    """Parse ledger entries, failing closed on anything malformed.

    Every entry must be a JSON object with a `url` that canonicalizes. A bad one
    raises, naming its index, rather than being skipped.

    Skipping looked defensible — a dropped decline fails in the *visible*
    direction, since the page simply reappears as a gap. But it fails silently,
    on an otherwise green run, and a decline is the one record here that cannot
    be re-derived from the bank. A corrupt ledger *file* is already an
    operational failure for exactly that reason; a corrupt *entry* is the same
    failure at a finer grain, and treating them differently was a threshold set
    by count rather than by principle.

    This is deliberately stricter than `bank_source_urls`, which does skip
    mangled rows: the bank is large, hand-authored, and a skipped row only
    over-reports a gap. The ledger is small and machine-written, so a malformed
    entry means something is actually wrong.
    """
    if not isinstance(entries, list):
        raise ValueError("ledger is not a list of decline entries")
    declines: List[Decline] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"ledger entry {index} is not a JSON object")
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"ledger entry {index} has no usable `url`")
        canonical = canonical_url(url)
        if canonical is None:
            raise ValueError(f"ledger entry {index} has an unparseable url {url!r}")
        declines.append(
            Decline(
                url=canonical,
                reason=str(entry.get("reason") or ""),
                at=str(entry.get("at") or ""),
            )
        )
    return tuple(declines)


def declined_urls(declines: Iterable[Decline]) -> Set[str]:
    """The canonical URL set to suppress.

    Canonicalization happens in `read_declines`, so nothing can be dropped here.
    """
    return {decline.url for decline in declines}


def with_decline(
    entries: Any, url: str, *, reason: str = "", at: str = ""
) -> List[Dict[str, str]]:
    """Return the ledger entries plus `url`, without mutating the input.

    Idempotent by canonical URL: declining the same page twice keeps the first
    entry (and its reason), so a repeated dismissal never grows the file.

    Raises if the existing ledger is malformed — appending to a ledger the tool
    cannot fully read would present the surviving subset as authoritative while
    carrying the broken entries forward unnoticed.
    """
    existing = list(entries) if isinstance(entries, list) else []
    read_declines(existing)
    canonical = canonical_url(url)
    if canonical is None:
        raise ValueError(f"cannot decline an unusable URL: {url!r}")
    if canonical in declined_urls(read_declines(existing)):
        return existing
    entry: Dict[str, str] = {"url": canonical}
    if reason:
        entry["reason"] = reason
    if at:
        entry["at"] = at
    return existing + [entry]


# --------------------------------------------------------------------------- #
# Greenlit-only candidate proposal (design D3 / D4)
# --------------------------------------------------------------------------- #
#: Anchor types a page-grounded candidate may carry. `should_refuse` is absent
#: on purpose: those rows carry NO `sources` by design (they test that the agent
#: declines rather than that it retrieves), so one "grounded in" a page is a
#: contradiction in terms.
GROUNDED_ANCHOR_TYPES = ("easy_retrieve", "reasoning")

#: Every proposed candidate is a draft. There is no locked path here at all —
#: locking is a human act on an applied row (group 1's `status` lifecycle).
DRAFT_STATUS = "draft"

#: Page text handed to the model, in characters. A KB article is a few thousand;
#: the cap exists so a pathological page cannot blow the context window (and the
#: request budget) on a run an operator expects to be cheap.
MAX_PROMPT_PAGE_CHARS = 24_000

#: Takes a prompt, returns the model's raw reply. Injected so tests need no LLM.
AskLLM = Callable[[str], str]


class ProposalError(Exception):
    """The proposal run itself failed — not "this page yielded nothing"."""


@dataclass(frozen=True)
class Candidate:
    """A proposed bank row, always a draft, always grounded in one page.

    `status` is deliberately NOT a field: there is no way to express a locked
    candidate, so a confused or hostile model reply cannot smuggle one through a
    validation gap. `sources` is set by the tool from the greenlit URL, never
    read from the model, so "grounded" is a property of the type rather than a
    promise the model is trusted to keep.
    """

    user_input: str
    reference: str
    anchor_type: str
    sources: Tuple[str, ...]
    notes: str = ""

    def as_row(self) -> Dict[str, Any]:
        """The bank-shaped dict, in the field order the bank file uses."""
        return {
            "anchor_type": self.anchor_type,
            "status": DRAFT_STATUS,
            "user_input": self.user_input,
            "sources": list(self.sources),
            "reference": self.reference,
            "source_match_field": ["url"],
            "notes": self.notes,
        }


@dataclass(frozen=True)
class RejectedCandidate:
    """A model-proposed candidate the sanitizer refused, and why."""

    reason: str
    raw: Any


@dataclass(frozen=True)
class Proposal:
    """The outcome of one `--propose` run over one greenlit page."""

    url: str
    candidates: Tuple[Candidate, ...]
    rejected: Tuple[RejectedCandidate, ...]


_PROMPT_TEMPLATE = """\
You are drafting candidate questions for a RAG benchmark's golden-answer set.

Write {count} question/answer pairs that are answerable ONLY from the page below.
Rules:
- The answer must be stated in the page text. Do not use outside knowledge.
- Keep each answer to one or two sentences, quoting the page's own specifics
  (flag names, paths, commands) rather than paraphrasing them away.
- `anchor_type` is one of: {types}.
  Use "easy_retrieve" when one passage answers it outright; use "reasoning" when
  answering requires combining two or more parts of the page.
- Skip anything the page does not actually settle. Fewer good pairs beats
  padding.

Page URL: {url}
Page text:
---
{page_text}
---

Reply with ONLY a JSON array of objects, each with the keys "user_input",
"reference", "anchor_type", and an optional short "notes". No prose, no fences.
"""


def build_candidate_prompt(url: str, page_text: str, *, count: int = 3) -> str:
    """Compose the grounding prompt for one page."""
    text = page_text.strip()
    if len(text) > MAX_PROMPT_PAGE_CHARS:
        text = text[:MAX_PROMPT_PAGE_CHARS] + "\n[... page truncated ...]"
    return _PROMPT_TEMPLATE.format(
        count=count,
        types=", ".join(f'"{t}"' for t in GROUNDED_ANCHOR_TYPES),
        url=url,
        page_text=text,
    )


def _strip_code_fence(raw: str) -> str:
    """Drop a ```json fence a model may wrap its reply in."""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _candidate_records(raw: str) -> List[Any]:
    """Parse the model reply into a list of candidate records.

    Raises rather than returning `[]` on unparseable output: zero candidates
    from a broken reply must not read as "this page has nothing worth asking".
    """
    try:
        parsed = json.loads(_strip_code_fence(raw))
    except ValueError as exc:
        raise ProposalError(f"model reply was not JSON: {exc}") from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("candidates")
    if not isinstance(parsed, list):
        raise ProposalError("model reply was not a JSON array of candidates")
    return parsed


def parse_candidates(raw: str, url: str) -> Proposal:
    """Sanitize a model reply into grounded draft candidates.

    Everything that matters is imposed by this function, not accepted from the
    model: the source URL, the draft status, and the anchor-type vocabulary. The
    model contributes only the question and the answer text. Rejected records
    are returned with a reason rather than dropped, so a run that quietly
    produced nothing is distinguishable from one that produced nothing useful.
    """
    source = canonical_url(url)
    if source is None:
        raise ProposalError(f"cannot ground candidates in an unusable URL: {url!r}")

    candidates: List[Candidate] = []
    rejected: List[RejectedCandidate] = []
    for record in _candidate_records(raw):
        if not isinstance(record, dict):
            rejected.append(RejectedCandidate("not a JSON object", record))
            continue
        anchor_type = record.get("anchor_type")
        if anchor_type == "should_refuse":
            rejected.append(
                RejectedCandidate(
                    "should_refuse rows carry no sources and cannot be grounded "
                    "in a page",
                    record,
                )
            )
            continue
        if anchor_type not in GROUNDED_ANCHOR_TYPES:
            rejected.append(
                RejectedCandidate(
                    f"anchor_type {anchor_type!r} is not one of "
                    f"{', '.join(GROUNDED_ANCHOR_TYPES)}",
                    record,
                )
            )
            continue
        user_input = str(record.get("user_input") or "").strip()
        reference = str(record.get("reference") or "").strip()
        if not user_input:
            rejected.append(RejectedCandidate("blank user_input", record))
            continue
        if not reference:
            rejected.append(RejectedCandidate("blank reference", record))
            continue
        notes = str(record.get("notes") or "").strip()
        candidates.append(
            Candidate(
                user_input=user_input,
                reference=reference,
                anchor_type=anchor_type,
                sources=(source,),
                notes=_provenance_note(notes, source),
            )
        )
    return Proposal(url=source, candidates=tuple(candidates), rejected=tuple(rejected))


def _provenance_note(model_note: str, source: str) -> str:
    """Stamp where a candidate came from, keeping any note the model wrote.

    The bank's convention is that `notes` carries the DRAFT provenance a human
    needs in order to confirm a row later, so an unreviewed machine draft must
    be self-identifying rather than indistinguishable from an authored row.
    """
    stamp = f"DRAFT — proposed by goldenset_maintenance from {source}; unreviewed."
    return f"{model_note} {stamp}".strip() if model_note else stamp


def propose_candidates(
    url: str, page_text: str, ask_llm: AskLLM, *, count: int = 3
) -> Proposal:
    """Draft grounded candidates for one greenlit page. Writes nothing.

    "Greenlit" is the fact that a caller named this URL: nothing here scans for
    pages to propose against, so there is no path by which an unattended run
    drafts anything. The bank file is never opened.

    Both preconditions are checked BEFORE the model is called, so a run that
    cannot possibly produce a grounded candidate does not spend a request first.
    The canonical URL is what reaches the prompt, so the page the model is told
    it is reading is the same one the resulting row cites.
    """
    source = canonical_url(url)
    if source is None:
        raise ProposalError(f"cannot ground candidates in an unusable URL: {url!r}")
    if not page_text or not page_text.strip():
        raise ProposalError(
            f"no page text extracted for {source} — cannot ground a candidate in "
            "an empty page"
        )
    return parse_candidates(
        ask_llm(build_candidate_prompt(source, page_text, count=count)), source
    )


def without_decline(entries: Any, url: str) -> List[Dict[str, str]]:
    """Return the ledger entries minus `url`, without mutating the input.

    The reversal a decline otherwise has no path back from. Matching is by
    canonical URL, so an entry written under a different slash form is still
    found — a reversal that silently misses is how a page stays suppressed while
    the operator believes they cleared it.
    """
    existing = list(entries) if isinstance(entries, list) else []
    canonical = canonical_url(url)
    if canonical is None:
        raise ValueError(f"cannot undecline an unusable URL: {url!r}")
    kept: List[Dict[str, str]] = []
    for entry, decline in zip(existing, read_declines(existing)):
        if decline.url != canonical:
            kept.append(entry)
    return kept


# --------------------------------------------------------------------------- #
# Fact drift — hash tripwire, then LLM diff (design D2 / D6)
# --------------------------------------------------------------------------- #
# A locked row stores `source_hashes`: a map from each grounding URL to a content
# hash taken when a human confirmed the reference against that page. Each pass
# re-fetches every source, re-hashes, and compares — no "last run" state file and
# no timestamps, so the check is the same whether it runs hourly or once a year.
#
# Drift keys on the **live** page, not the persisted corpus, and the two passes in
# this module therefore read different things on purpose. Proposal grounds in the
# persisted text because a candidate must be answerable from what the retriever
# serves. Drift asks the opposite question — "has the world moved past what we
# recorded?" — and the corpus cannot answer it: ingestion skips the content write
# for a URL it already holds, so a corpus-based check would compare a stale
# reference against an equally stale copy and call it clean (design D6). Live
# re-fetch fails toward cheap, human-reviewed false positives instead.

#: Digest algorithm, carried **in** the stored value as a `sha256:` label.
#: A bare hex string does not say how it was computed, so a later change to the
#: extraction or hashing rule would silently compare against the wrong baseline —
#: either a bank-wide false-flag wave or, worse, a coincidental clean. Labeled
#: values let an unrecognized rule be reported as *incomparable* instead.
HASH_ALGORITHM = "sha256"

#: What the LLM diff may conclude. Imposed, never read from the model.
DRIFT_VERDICTS = ("holds", "broken", "unclear")
UNCLEAR_VERDICT = "unclear"

#: Per-source outcomes. Everything that is not `unchanged` or `changed` is a
#: state the tool could not judge, and each is reported rather than folded into
#: either — a page the tool failed to check must never read as a page that is fine.
DRIFT_UNCHANGED = "unchanged"
DRIFT_CHANGED = "changed"
DRIFT_UNBASELINED = "unbaselined"
DRIFT_INCOMPARABLE = "incomparable"
DRIFT_UNREACHABLE = "unreachable"
DRIFT_REFUSED = "refused"

_INLINE_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINE_RUN = re.compile(r"\n{3,}")


class DriftExtractionError(Exception):
    """A fetched page yielded no text to hash."""


def normalize_extracted_text(text: str) -> str:
    """Reduce extracted text to the form a content hash is taken over.

    Only *presentation* is normalized — line endings, Unicode composition form,
    runs of spaces/tabs, and runs of blank lines. Wording, case and punctuation
    are left exactly as written, because those are the content.

    This is the second half of design D6's sign-off condition. The first half is
    measuring the live page through the ingest's own extraction; this is what
    keeps the residue of that conversion (a reflowed paragraph, a re-indented
    list) from moving a hash and manufacturing drift nobody needs to review.
    """
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    collapsed = _INLINE_WHITESPACE.sub(" ", unicodedata.normalize("NFC", unified))
    stripped = "\n".join(line.strip() for line in collapsed.split("\n"))
    return _BLANK_LINE_RUN.sub("\n\n", stripped).strip()


def content_digest(text: str) -> str:
    """Return the algorithm-labeled digest of already-normalized text."""
    encoded = text.encode("utf-8")
    return f"{HASH_ALGORITHM}:{hashlib.sha256(encoded).hexdigest()}"


def extract_page_text(html: str) -> str:
    """Extract a fetched page the way the ingest does, normalized for hashing.

    Raises rather than returning `""` when the conversion yields nothing. An
    empty extraction hashes to a single fixed value, so every page that stopped
    converting would agree with every other one — and each would then read as
    "unchanged" on every future run. That is a silent false clean on exactly the
    pages something is already wrong with.
    """
    extracted = normalize_extracted_text(html_to_markdown(html))
    if not extracted:
        raise DriftExtractionError(
            "the page converted to no text at all — refusing to hash an empty "
            "extraction, which would read as 'unchanged' forever"
        )
    return extracted


def page_digest(html: str) -> str:
    """Fetch-to-hash in one step: the value a `source_hashes` entry holds."""
    return content_digest(extract_page_text(html))


@dataclass(frozen=True)
class DriftVerdict:
    """The model's advisory read on whether a stored reference still holds."""

    verdict: str
    explanation: str = ""


@dataclass(frozen=True)
class SourceCheck:
    """One grounding URL of one locked row, checked against its baseline.

    `fresh_text` is retained only for a `changed` source — it is what the LLM
    diff reads, and holding every page's body for a whole bank would be a lot of
    memory for text nothing goes on to use.
    """

    url: str
    state: str
    stored: str = ""
    fresh: str = ""
    detail: str = ""
    fresh_text: str = ""
    verdict: Optional[DriftVerdict] = None


@dataclass(frozen=True)
class RowDrift:
    """One locked row's sources, checked.

    `stale_baselines` names hashes stored for URLs the row no longer cites —
    left behind when someone edited `sources` without clearing the map. Harmless
    on its own, but it means a recorded confirmation refers to a page that is no
    longer part of this question, and silently ignoring it hides that.
    """

    row_index: int
    user_input: str
    reference: str
    checks: Tuple[SourceCheck, ...]
    stale_baselines: Tuple[str, ...] = ()

    @property
    def changed(self) -> Tuple[SourceCheck, ...]:
        return tuple(c for c in self.checks if c.state == DRIFT_CHANGED)


@dataclass(frozen=True)
class DriftReport:
    """What one drift pass found. Advisory: nothing here edits the bank."""

    rows: Tuple[RowDrift, ...]
    checked_rows: int
    skipped_rows: int
    abstained: bool = False
    reasons: Tuple[str, ...] = ()

    def _in_state(self, state: str) -> Tuple[SourceCheck, ...]:
        return tuple(c for row in self.rows for c in row.checks if c.state == state)

    @property
    def drifted(self) -> Tuple[RowDrift, ...]:
        return tuple(row for row in self.rows if row.changed)

    @property
    def unbaselined(self) -> Tuple[SourceCheck, ...]:
        return self._in_state(DRIFT_UNBASELINED)

    @property
    def incomparable(self) -> Tuple[SourceCheck, ...]:
        return self._in_state(DRIFT_INCOMPARABLE)

    @property
    def unreachable(self) -> Tuple[SourceCheck, ...]:
        return self._in_state(DRIFT_UNREACHABLE)

    @property
    def refused(self) -> Tuple[SourceCheck, ...]:
        return self._in_state(DRIFT_REFUSED)


_DRIFT_PROMPT = """\
A question in a RAG benchmark's golden-answer set was confirmed against a page.
That page has since changed. Decide whether the recorded answer is still correct.

Question: {user_input}
Recorded answer: {reference}

The page NOW reads ({url}):
---
{page_text}
---

Answer only about whether the recorded answer is still supported by this page.
Do not rewrite it, and do not judge its style.

Reply with ONLY a JSON object:
{{"verdict": "holds" | "broken" | "unclear", "explanation": "<one sentence>"}}
- "holds": the page still supports the recorded answer.
- "broken": the page now contradicts it, or no longer supports it.
- "unclear": the page does not settle it either way.
No prose, no fences.
"""


def build_drift_prompt(
    user_input: str, reference: str, url: str, page_text: str
) -> str:
    """Compose the diff prompt for one changed source."""
    text = page_text.strip()
    if len(text) > MAX_PROMPT_PAGE_CHARS:
        text = text[:MAX_PROMPT_PAGE_CHARS] + "\n[... page truncated ...]"
    return _DRIFT_PROMPT.format(
        user_input=user_input, reference=reference, url=url, page_text=text
    )


def parse_drift_verdict(raw: str) -> DriftVerdict:
    """Sanitize a model reply into one of the three allowed verdicts.

    Never raises. The verdict is advisory triage on a finding the hash already
    established, so a broken reply must degrade to `unclear` rather than take the
    run down — but it says *why* it is unclear, so an operator can tell a model
    that read the page from one that never answered.
    """
    try:
        parsed = json.loads(_strip_code_fence(raw))
    except ValueError as exc:
        return DriftVerdict(UNCLEAR_VERDICT, f"the model reply was not JSON: {exc}")
    if not isinstance(parsed, dict):
        return DriftVerdict(
            UNCLEAR_VERDICT, "the model reply was not JSON: expected an object"
        )
    explanation = str(parsed.get("explanation") or "").strip()
    verdict = parsed.get("verdict")
    if verdict not in DRIFT_VERDICTS:
        return DriftVerdict(
            UNCLEAR_VERDICT,
            f"the model answered {verdict!r}, which is not one of "
            f"{', '.join(DRIFT_VERDICTS)}. {explanation}".strip(),
        )
    return DriftVerdict(str(verdict), explanation)


def _ask_drift(
    ask_llm: AskLLM, user_input: str, reference: str, url: str, page_text: str
) -> DriftVerdict:
    """Run the diff, degrading a provider failure to `unclear`.

    A model that cannot be reached must not erase the finding: the hash mismatch
    is the fact, and the verdict only tells an operator how urgently to look.
    """
    prompt = build_drift_prompt(user_input, reference, url, page_text)
    try:
        return parse_drift_verdict(ask_llm(prompt))
    except Exception as exc:  # noqa: BLE001 - any provider failure is advisory
        return DriftVerdict(UNCLEAR_VERDICT, f"the model could not be reached: {exc}")


def is_fetchable_source(url: str, allowed_hosts: Iterable[str] = ()) -> bool:
    """Whether drift may turn this `sources` value into an outbound request.

    A `sources` entry is *data* read out of a bank file, and drift is the one
    pass that dials it. The ingest's trust filter lives in `expand_sitemaps`,
    not in `fetch_sitemap_text`, so reusing the ingest's fetcher inherits its
    redirect and size limits and **none** of its target policy. Without this
    check the tool would fetch `http://169.254.169.254/…` from whatever host it
    runs on and — with `--model` and any hash mismatch — forward the response to
    an external provider.

    Delegates to the ingest's own `is_url_allowed` rather than restating its
    rules, so the two cannot diverge: non-http(s) schemes, malformed ports, and
    loopback/private/link-local or obfuscated-numeric hosts are refused.

    With no `allowed_hosts`, a URL's own host stands in as the allowed one, so
    only the unconditional rules apply and any public host is reachable — the
    bank legitimately cites external authorities (the upstream Slurm docs).
    Passing `allowed_hosts` narrows that to an explicit list.

    Not a substitute for DNS-rebinding-resistant connection pinning, which
    `fetch_sitemap_text` defers (its "§Deferred hardening v2/H1"). Drift is no
    more exposed than the ingest that fetches these hosts continuously.
    """
    hosts = [host for host in allowed_hosts if host]
    if hosts:
        return is_url_allowed(url, "", hosts)
    try:
        own_host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return bool(own_host) and is_url_allowed(url, own_host, [])


def _fetch_extract(
    url: str, cache: Dict[str, Tuple[str, str, str]], fetch_html: FetchText
) -> Tuple[str, str, str]:
    """Return `(text, digest, error)` for a URL, fetching each URL at most once.

    Failures are cached too. A bank cites the same page from several rows, so an
    uncached failure would re-request a dead URL once per row — turning one
    unreachable page into a burst of traffic at the KB, and one slow timeout into
    many.

    The digest is taken over the **whole** extracted page; only the retained copy
    is truncated, and only to the length a prompt could actually use. Hashing the
    truncation instead would make every edit past the cut invisible — a silent
    false clean on exactly the long pages hardest to review by eye. Retaining the
    whole thing is the opposite trap: this cache lives for the length of the run,
    so a bank with many large sources would grow it without bound.
    """
    if url in cache:
        return cache[url]
    try:
        text = extract_page_text(fetch_html(url))
        result = (text[:MAX_PROMPT_PAGE_CHARS], content_digest(text), "")
    except Exception as exc:  # noqa: BLE001 - fetcher is injected; any failure is local
        result = ("", "", str(exc))
    cache[url] = result
    return result


def _baselines(record: Mapping[str, Any]) -> Dict[str, str]:
    """Read a row's `source_hashes` map, canonicalizing its URL keys.

    Keys are canonicalized for the same reason `sources` are: a map written with
    a trailing slash must still match the source it baselines, or the row reads
    as unbaselined forever while looking perfectly well-formed.
    """
    stored = record.get("source_hashes")
    if not isinstance(stored, dict):
        return {}
    baselines: Dict[str, str] = {}
    for raw_key, value in stored.items():
        if not isinstance(raw_key, str) or not isinstance(value, str):
            continue
        key = canonical_url(raw_key)
        if key is not None:
            baselines[key] = value
    return baselines


def _check_source(
    url: str,
    stored: Optional[str],
    row: Mapping[str, Any],
    cache: Dict[str, Tuple[str, str, str]],
    fetch_html: FetchText,
    ask_llm: Optional[AskLLM],
    allowed_hosts: Iterable[str] = (),
) -> SourceCheck:
    """Compare one grounding URL against its baseline, escalating on a mismatch.

    The policy check and the LLM call both live here, on either side of the
    fetch, so "never dial a refused target" and "the model fires only on a moved
    hash" are structural rather than rules some caller has to honor.
    """
    if not is_fetchable_source(url, allowed_hosts):
        return SourceCheck(
            url=url,
            state=DRIFT_REFUSED,
            stored=stored or "",
            detail=(
                "refused by the fetch policy (not http/https, a loopback/private/"
                "link-local address, or a host outside --allowed-hosts) — not "
                "fetched, and never shown to a model"
            ),
        )
    text, fresh, error = _fetch_extract(url, cache, fetch_html)
    if error:
        return SourceCheck(
            url=url, state=DRIFT_UNREACHABLE, stored=stored or "", detail=error
        )
    if not stored:
        return SourceCheck(url=url, state=DRIFT_UNBASELINED, fresh=fresh)
    if not stored.startswith(f"{HASH_ALGORITHM}:"):
        return SourceCheck(
            url=url,
            state=DRIFT_INCOMPARABLE,
            stored=stored,
            fresh=fresh,
            detail=(
                f"the stored hash is not a {HASH_ALGORITHM}: digest, so it cannot "
                "be compared with a freshly computed one"
            ),
        )
    if stored == fresh:
        return SourceCheck(url=url, state=DRIFT_UNCHANGED, stored=stored, fresh=fresh)
    verdict = None
    if ask_llm is not None:
        verdict = _ask_drift(
            ask_llm,
            str(row.get("user_input") or ""),
            str(row.get("reference") or ""),
            url,
            text,
        )
    return SourceCheck(
        url=url,
        state=DRIFT_CHANGED,
        stored=stored,
        fresh=fresh,
        fresh_text=text,
        verdict=verdict,
    )


def find_drift(
    bank: Iterable[Any],
    fetch_html: FetchText,
    *,
    ask_llm: Optional[AskLLM] = None,
    allowed_hosts: Iterable[str] = (),
) -> DriftReport:
    """Re-hash every locked row's grounding pages and report what moved.

    Only `locked` rows are checked. A `draft` row is unconfirmed by definition —
    no human ever vouched for its reference against a page — so there is nothing
    for a hash to be a baseline *of*, and its sources are not even fetched. A
    locked row with no `sources` (the `should_refuse` shape) has nothing to
    check either; locking one must not require a grounding hash.

    A row is flagged when **any** of its sources moved, and the changed URL is
    named: a row grounded in three pages where one was rewritten is exactly as
    stale as one grounded in a single rewritten page.

    Abstains only when every fetch failed. That is deliberately weaker than the
    orphan pass, which abstains on a *single* inventory failure — there, one
    missing sitemap makes unrelated rows look deleted, so the damage spreads. A
    failure here is local: it affects only the rows citing that URL, and those
    rows are individually reported as unchecked rather than as clean. When
    nothing at all could be read, though, "no drift" would be a false clean over
    the entire bank, so the run says so instead.

    Read-only: the bank is never mutated, and `source_hashes` is never rewritten.
    Re-baselining a row is a human act, like locking it in the first place.
    """
    cache: Dict[str, Tuple[str, str, str]] = {}
    rows: List[RowDrift] = []
    checked = 0
    skipped = 0

    for index, record in enumerate(bank):
        if not isinstance(record, dict) or row_status(record) != "locked":
            skipped += 1
            continue
        sources = record.get("sources")
        if not isinstance(sources, list) or not sources:
            skipped += 1
            continue
        baselines = _baselines(record)
        cited: List[str] = []
        checks: List[SourceCheck] = []
        for raw in sources:
            if not isinstance(raw, str) or not raw:
                continue
            url = canonical_url(raw)
            if url is None or url in cited:
                continue
            cited.append(url)
            checks.append(
                _check_source(
                    url,
                    baselines.get(url),
                    record,
                    cache,
                    fetch_html,
                    ask_llm,
                    allowed_hosts,
                )
            )
        if not checks:
            skipped += 1
            continue
        checked += 1
        rows.append(
            RowDrift(
                row_index=index,
                user_input=str(record.get("user_input") or ""),
                reference=str(record.get("reference") or ""),
                checks=tuple(checks),
                stale_baselines=tuple(u for u in baselines if u not in cited),
            )
        )

    failures = tuple(f"{url}: {r[2]}" for url, r in cache.items() if r[2])
    abstained = bool(cache) and len(failures) == len(cache)
    return DriftReport(
        rows=tuple(rows),
        checked_rows=checked,
        skipped_rows=skipped,
        abstained=abstained,
        reasons=failures if abstained else (),
    )
