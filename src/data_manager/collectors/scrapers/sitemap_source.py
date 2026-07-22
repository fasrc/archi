"""Runtime XML-sitemap expansion for the ``sitemap-`` source-list prefix.

A ``sitemap-<url>`` line in an ingestion source list is expanded here, at ingest
time, into the page URLs the sitemap advertises; those URLs then flow through the
standard web-scraping path exactly as if each had been hand-listed. The logic is
kept in this small module (parse / normalize / trust-filter / expand) with an
INJECTED fetch callable so every branch is unit-testable with fixture XML and no
network — the thin ``ScraperManager`` call site supplies the real ``requests``
fetch.

Scope note (v1): this targets a TRUSTED first-party sitemap (the FASRC KB). The
trust policy is basic — scheme/host + IP-literal reject + no cross-host redirect.
Fuller SSRF defenses (DNS resolve-to-global + connection pinning) and fetch-work
budgets an *untrusted* sitemap needs are specified in the change's design
§Deferred hardening (v2) and must land before this prefix is aimed at any
untrusted/third-party sitemap.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import requests

from src.utils.logging import get_logger

logger = get_logger(__name__)

# GET timeout (seconds) and body-size cap — mirror ``sources_builder`` so the two
# sitemap fetchers behave identically. The sitemaps.org protocol caps a single
# sitemap at 50 MB uncompressed, so 64 MB is generous headroom.
_SITEMAP_TIMEOUT = 30
_MAX_FETCH_BYTES = 64 * 1024 * 1024
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

FetchText = Callable[[str], str]


class SitemapParseError(Exception):
    """Raised when a sitemap document cannot be parsed.

    Covers malformed XML, a rejected DTD/entity declaration, or an unrecognized
    root element. Caught per-document by the expander (fail-open), never fatal on
    its own.
    """


class SitemapFetchError(Exception):
    """Raised when a sitemap document cannot be fetched.

    Covers connection/timeout errors, a non-2xx status, an over-size body, or a
    cross-host redirect. Caught per-document by the expander (fail-open).
    """


class SitemapExpansionError(Exception):
    """Raised when a whole ``sitemap-`` source fails a source-level bound.

    This is NOT caught by per-document fail-open — it propagates out to the
    ingestion run and fails it deliberately, so a runaway (``over_cap``) or an
    empty/near-empty (``below_floor``) sitemap cannot silently ship a bad corpus.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        source_url: Optional[str] = None,
        count: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason  # {"below_floor", "over_cap"}
        self.source_url = source_url
        self.count = count


@dataclass
class SitemapPolicy:
    """Per-source trust/bounds (v1). Values are global config, applied per source.

    ``allowed_hosts`` — hosts (besides the sitemap's own host) whose URLs may be
    fetched/emitted. ``min_pages`` — floor below which a source fails the ingest.
    ``max_pages`` — cap above which a source fails deterministically.
    """

    allowed_hosts: List[str] = field(default_factory=list)
    min_pages: int = 1
    max_pages: int = 20000


# --------------------------------------------------------------------------- #
# Parsing (namespace-agnostic, DTD/entity-rejecting) — mirrors sources_builder
# --------------------------------------------------------------------------- #
def _local_tag(tag: str) -> str:
    """Strip an XML namespace, returning the local tag name (e.g. ``loc``)."""
    return tag.rsplit("}", 1)[-1]


def _locs(root: "ElementTree.Element", wrapper: str) -> List[str]:
    """Return the ``<loc>`` text of each DIRECT ``<wrapper>`` child of ``root``.

    ``wrapper="url"`` for a ``<urlset>``, ``wrapper="sitemap"`` for a
    ``<sitemapindex>``. Descent is deliberately shallow: only the ``<loc>``
    immediately inside a direct wrapper child is read, so an inline-nested index
    buried in a child contributes nothing.
    """
    out: List[str] = []
    for child in root:
        if _local_tag(child.tag) != wrapper:
            continue
        for grand in child:
            if _local_tag(grand.tag) == "loc" and grand.text:
                out.append(grand.text.strip())
                break  # one <loc> per <url>/<sitemap> wrapper
    return out


def parse_sitemap_document(text: str) -> Tuple[str, List[str]]:
    """Parse a sitemap document into ``(root_kind, loc_values)``.

    ``root_kind`` is ``"urlset"`` (loc values are page URLs) or ``"sitemapindex"``
    (loc values are child-sitemap URLs). A DTD/entity declaration, malformed XML,
    or an unknown root element raises :class:`SitemapParseError`. A valid but
    empty ``<urlset>`` is NOT an error — it returns ``("urlset", [])``; the
    source-level consequence of a low count is the floor's job, not the parser's.
    """
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise SitemapParseError(
            "refusing sitemap with a DTD/entity declaration "
            "(possible entity-expansion attack)"
        )
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise SitemapParseError(f"malformed sitemap XML: {exc}")

    kind = _local_tag(root.tag)
    if kind == "urlset":
        return "urlset", _locs(root, "url")
    if kind == "sitemapindex":
        return "sitemapindex", _locs(root, "sitemap")
    raise SitemapParseError(f"unexpected sitemap root <{kind}>")


# --------------------------------------------------------------------------- #
# Normalization + trust policy (v1)
# --------------------------------------------------------------------------- #
def normalize_page_url(url: str) -> str:
    """Normalize an emitted page URL to the hand-list form.

    Drops the fragment, lowercases the scheme and host, preserves the query, and
    collapses a single trailing path slash (root ``/`` preserved) — matching
    ``sources_builder.normalize_url`` so sitemap output cannot reintroduce the
    ``/x`` vs ``/x/`` slash-variant duplicate-chunk issue (#118).
    """
    parts = urlparse(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    # Skip the collapse when matrix params are present: in `/x/;v=1` the `;v=1`
    # belongs to the empty trailing segment, so stripping the slash would move it
    # onto `x` and change the URL (mirrors LinkScraper._normalize_url).
    if len(path) > 1 and path.endswith("/") and not parts.params:
        # Strip exactly ONE trailing slash (like LinkScraper._normalize_url), so
        # `/x//` stays `/x/` rather than collapsing to `/x` and diverging from the
        # hand-listed form.
        path = path[:-1]
    return urlunparse((scheme, netloc, path, parts.params, parts.query, ""))


def _as_ip(
    host: str,
) -> Optional["ipaddress.IPv4Address | ipaddress.IPv6Address"]:
    """Return the parsed IP if ``host`` is an IP literal, else ``None``."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_url_allowed(url: str, sitemap_host: str, allowed_hosts: List[str]) -> bool:
    """Return whether ``url`` may be fetched/emitted under the v1 trust policy.

    Rules: (1) scheme is ``http``/``https``; (2) host equals ``sitemap_host`` or
    is in ``allowed_hosts``; (3) an IP-literal host in a loopback/private/
    link-local range is rejected (even if allowlisted). Exact-host match (not
    registrable-domain) avoids a public-suffix-list dependency.
    """
    try:
        parts = urlparse(url)
    except ValueError:
        # A malformed URL (e.g. a stray `[` -> "Invalid IPv6 URL") is untrusted.
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    if not host:
        return False
    ip = _as_ip(host)
    if ip is not None and (ip.is_loopback or ip.is_private or ip.is_link_local):
        return False
    allowed = {(sitemap_host or "").lower()}
    allowed.update(h.lower() for h in (allowed_hosts or []))
    return host in allowed


# --------------------------------------------------------------------------- #
# Fetch helper (requests-backed; injected in tests)
# --------------------------------------------------------------------------- #
def fetch_sitemap_text(
    url: str,
    *,
    verify: bool = False,
    timeout: int = _SITEMAP_TIMEOUT,
    max_bytes: int = _MAX_FETCH_BYTES,
) -> str:
    """GET ``url`` and return the decoded body text.

    Streams the body and aborts as soon as ``max_bytes`` is exceeded. Redirects
    are NOT auto-followed: a same-host redirect is followed manually (bounded by
    :data:`_MAX_REDIRECTS`), a cross-host redirect raises :class:`SitemapFetchError`
    without ever requesting the off-host target (v1 SSRF containment; DNS-resolve
    + connection pinning is §Deferred hardening v2/H1). Raises
    :class:`SitemapFetchError` on any connection/timeout/status/over-size failure.
    """
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        try:
            resp = requests.get(
                current,
                timeout=timeout,
                verify=verify,
                stream=True,
                allow_redirects=False,
            )
        except requests.exceptions.RequestException as exc:
            raise SitemapFetchError(f"failed to fetch {current}: {exc}")
        try:
            if resp.status_code in _REDIRECT_STATUSES:
                target = urljoin(current, resp.headers.get("Location", ""))
                if _host_of(target) != _host_of(current):
                    raise SitemapFetchError(
                        f"refusing cross-host redirect {current} -> {target}"
                    )
                current = target
                continue
            resp.raise_for_status()
            chunks = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise SitemapFetchError(
                        f"body from {current} exceeds the {max_bytes}-byte cap"
                    )
                chunks.append(chunk)
            # Use the header charset if present, else default to UTF-8 (the XML
            # default). Do NOT probe resp.apparent_encoding: it reads resp.content,
            # which raises RuntimeError after the body was streamed via
            # iter_content — a charset-less application/xml sitemap would then
            # abort ingestion instead of parsing.
            body = b"".join(chunks)
            encoding = resp.encoding or "utf-8"
            try:
                return body.decode(encoding, errors="replace")
            except LookupError:
                # An unknown/misdeclared charset (e.g. `charset=bogus`) makes
                # decode raise LookupError before errors="replace" applies; fall
                # back to UTF-8 rather than aborting the ingest.
                return body.decode("utf-8", errors="replace")
        except requests.exceptions.RequestException as exc:
            raise SitemapFetchError(f"failed to read {current}: {exc}")
        finally:
            resp.close()
    raise SitemapFetchError(f"too many redirects fetching {url}")


# --------------------------------------------------------------------------- #
# Expansion (per source, no aggregate counting — design D10)
# --------------------------------------------------------------------------- #
def _fetch_and_parse(
    url: str, fetch_text: FetchText
) -> Tuple[Optional[str], List[str]]:
    """Fetch + parse one document, failing open: log a WARNING and return
    ``(None, [])`` on any per-document fetch/parse failure."""
    try:
        text = fetch_text(url)
        return parse_sitemap_document(text)
    except (SitemapFetchError, SitemapParseError) as exc:
        logger.warning("sitemap: skipping %s (%s)", url, exc)
        return None, []


def expand_sitemap_source(
    sitemap_url: str, fetch_text: FetchText, policy: SitemapPolicy
) -> List[str]:
    """Expand ONE ``sitemap-`` source into its validated page URLs.

    A ``<urlset>`` contributes its ``<loc>`` pages; a ``<sitemapindex>`` has each
    child fetched once (a child ``<urlset>`` contributes pages, a nested index
    contributes nothing). Per-document fetch/parse failures fail open (WARNING,
    zero URLs). Emitted pages are normalized, trust-filtered, and deduped, then
    the per-source page cap and floor are applied to THIS source's own count with
    THIS source's own host — raising :class:`SitemapExpansionError` on
    over-cap/below-floor. No counting is shared across sources (design D10).
    """
    sitemap_host = _host_of(sitemap_url)

    raw_pages: List[str] = []
    # Validate the top-level sitemap URL BEFORE fetching it, so a bad list entry
    # (wrong scheme, or an IP-literal loopback/private/link-local host such as the
    # cloud-metadata address) is never contacted — the same trust policy applied
    # to child sitemaps and emitted pages. A rejected seed contributes nothing and
    # therefore fails the source below its floor. (Host equals its own host, so the
    # scheme + IP-literal rules are what fire here.)
    if not is_url_allowed(sitemap_url, sitemap_host, policy.allowed_hosts):
        logger.warning(
            "sitemap: refusing untrusted top-level sitemap URL %s", sitemap_url
        )
        kind, locs = None, []
    else:
        kind, locs = _fetch_and_parse(sitemap_url, fetch_text)
    if kind == "urlset":
        raw_pages.extend(locs)
    elif kind == "sitemapindex":
        for child_url in locs:
            if not is_url_allowed(child_url, sitemap_host, policy.allowed_hosts):
                logger.warning(
                    "sitemap: dropping untrusted child sitemap %s (source %s)",
                    child_url,
                    sitemap_url,
                )
                continue
            child_kind, child_locs = _fetch_and_parse(child_url, fetch_text)
            if child_kind == "urlset":
                raw_pages.extend(child_locs)
            elif child_kind == "sitemapindex":
                logger.warning(
                    "sitemap: nested sitemapindex %s not followed (source %s)",
                    child_url,
                    sitemap_url,
                )

    emitted: List[str] = []
    seen = set()
    for loc in raw_pages:
        try:
            norm = normalize_page_url(loc)
        except ValueError:
            # A malformed <loc> (e.g. a stray `[`) must fail open per document,
            # not crash the ingest — drop it with a warning like any bad URL.
            logger.warning(
                "sitemap: dropping unparseable page %s (source %s)", loc, sitemap_url
            )
            continue
        if not is_url_allowed(norm, sitemap_host, policy.allowed_hosts):
            logger.warning(
                "sitemap: dropping untrusted page %s (source %s)", loc, sitemap_url
            )
            continue
        if norm in seen:
            continue
        seen.add(norm)
        emitted.append(norm)

    count = len(emitted)
    if count > policy.max_pages:
        logger.error(
            "sitemap: source %s emitted %d pages, over cap %d; failing ingest",
            sitemap_url,
            count,
            policy.max_pages,
        )
        raise SitemapExpansionError(
            f"sitemap {sitemap_url} emitted {count} pages, over cap {policy.max_pages}",
            reason="over_cap",
            source_url=sitemap_url,
            count=count,
        )
    if count < policy.min_pages:
        logger.error(
            "sitemap: source %s emitted %d pages, below floor %d; failing ingest",
            sitemap_url,
            count,
            policy.min_pages,
        )
        raise SitemapExpansionError(
            f"sitemap {sitemap_url} emitted {count} pages, below floor "
            f"{policy.min_pages}",
            reason="below_floor",
            source_url=sitemap_url,
            count=count,
        )
    return emitted


def expand_sitemaps(
    sitemap_urls: List[str], fetch_text: FetchText, policy: SitemapPolicy
) -> List[str]:
    """Expand every ``sitemap-`` source and merge the results (order-preserving
    dedupe). Each source is expanded and validated independently; the first
    source-level :class:`SitemapExpansionError` propagates and fails the ingest."""
    merged: List[str] = []
    seen = set()
    for sitemap_url in sitemap_urls:
        for page in expand_sitemap_source(sitemap_url, fetch_text, policy):
            if page not in seen:
                seen.add(page)
                merged.append(page)
    return merged
