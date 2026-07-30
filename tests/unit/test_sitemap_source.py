from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.data_manager.collectors.scrapers import sitemap_source as ss
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager

# --------------------------------------------------------------------------- #
# Fixture XML documents (namespaced + un-namespaced urlset, index, malformed)
# --------------------------------------------------------------------------- #
HOST = "docs.rc.fas.harvard.edu"

URLSET_NS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.rc.fas.harvard.edu/kb/copying-data-to-and-from-cluster-using-scp/</loc><lastmod>2026-01-01</lastmod></url>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/running-jobs</loc><lastmod>2026-01-02</lastmod></url>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/fairshare</loc><lastmod>2026-01-03</lastmod></url>
</urlset>"""

URLSET_NONS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/one</loc></url>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/two</loc></url>
</urlset>"""

EMPTY_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>"""

URLSET_STRAY = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <comment>a direct child that is not a &lt;url&gt; is ignored</comment>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/only</loc></url>
</urlset>"""

SITEMAPINDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://docs.rc.fas.harvard.edu/child-1.xml</loc></sitemap>
  <sitemap><loc>https://docs.rc.fas.harvard.edu/child-2.xml</loc></sitemap>
</sitemapindex>"""

CHILD_1 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.rc.fas.harvard.edu/kb/1a</loc></url>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/1b</loc></url>
</urlset>"""

CHILD_2 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.rc.fas.harvard.edu/kb/2a</loc></url>
</urlset>"""

NESTED_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://docs.rc.fas.harvard.edu/grandchild.xml</loc></sitemap>
</sitemapindex>"""

MALFORMED = "<urlset <<< not well formed"

# Two entries normalize to the same URL (/kb/x); the first has lastmod "2026-01-01".
URLSET_DUP_WITH_LASTMOD = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.rc.fas.harvard.edu/kb/x/</loc><lastmod>2026-01-01</lastmod></url>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/x</loc><lastmod>2026-02-01</lastmod></url>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/y</loc><lastmod>2026-03-01</lastmod></url>
</urlset>"""

DOCTYPE_BODY = """<?xml version="1.0"?>
<!DOCTYPE urlset [ <!ENTITY lol "lol"> ]>
<urlset><url><loc>https://docs.rc.fas.harvard.edu/kb/x</loc></url></urlset>"""

ENTITY_BODY = """<?xml version="1.0"?>
<!ENTITY foo "bar">
<urlset><url><loc>https://docs.rc.fas.harvard.edu/kb/x</loc></url></urlset>"""

RSS_BODY = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>not a sitemap</title></channel></rss>"""


class FakeFetch:
    """Injectable ``fetch_text`` callable backed by a URL->body dict.

    A missing URL raises ``SitemapFetchError`` (simulating an unreachable
    document); a body that is an ``Exception`` instance is raised as-is.
    """

    def __init__(self, bodies):
        self.bodies = bodies
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.bodies:
            raise ss.SitemapFetchError(f"no body for {url}")
        body = self.bodies[url]
        if isinstance(body, Exception):
            raise body
        return body


def _policy(allowed_hosts=None, min_pages=1, max_pages=20000):
    return ss.SitemapPolicy(
        allowed_hosts=list(allowed_hosts or []),
        min_pages=min_pages,
        max_pages=max_pages,
    )


# --------------------------------------------------------------------------- #
# Fixtures for lastmod-aware parse (tasks 1.1 / 1.2 / 1.3)
# --------------------------------------------------------------------------- #
URLSET_LASTMOD_MIXED = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.org/a</loc><lastmod>2026-04-21T19:19:35+00:00</lastmod></url>
  <url><loc>https://example.org/b</loc></url>
  <url><loc>https://example.org/c</loc><lastmod></lastmod></url>
</urlset>"""

SITEMAPINDEX_WITH_LASTMOD = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.org/s1.xml</loc><lastmod>2026-01-01</lastmod></sitemap>
  <sitemap><loc>https://example.org/s2.xml</loc></sitemap>
</sitemapindex>"""


# --------------------------------------------------------------------------- #
# 1.1 parse_sitemap_entries — TDD: new lastmod-aware parser
# --------------------------------------------------------------------------- #
class TestParseSitemapEntries:
    def test_entry_with_lastmod_yields_value(self):
        kind, entries = ss.parse_sitemap_entries(URLSET_LASTMOD_MIXED)
        assert kind == "urlset"
        assert entries[0] == ("https://example.org/a", "2026-04-21T19:19:35+00:00")

    def test_entry_without_lastmod_yields_none(self):
        _, entries = ss.parse_sitemap_entries(URLSET_LASTMOD_MIXED)
        assert entries[1] == ("https://example.org/b", None)

    def test_empty_lastmod_yields_none(self):
        _, entries = ss.parse_sitemap_entries(URLSET_LASTMOD_MIXED)
        assert entries[2] == ("https://example.org/c", None)

    def test_all_three_entries_present(self):
        _, entries = ss.parse_sitemap_entries(URLSET_LASTMOD_MIXED)
        assert len(entries) == 3

    def test_malformed_document_raises_sitemap_parse_error_only(self):
        with pytest.raises(ss.SitemapParseError):
            ss.parse_sitemap_entries("<urlset <<< not well formed")

    def test_doctype_rejected_in_entries(self):
        with pytest.raises(ss.SitemapParseError):
            ss.parse_sitemap_entries(DOCTYPE_BODY)

    def test_unknown_root_raises_in_entries(self):
        with pytest.raises(ss.SitemapParseError):
            ss.parse_sitemap_entries(RSS_BODY)

    def test_sitemapindex_entries_carry_lastmod(self):
        kind, entries = ss.parse_sitemap_entries(SITEMAPINDEX_WITH_LASTMOD)
        assert kind == "sitemapindex"
        assert entries[0] == ("https://example.org/s1.xml", "2026-01-01")
        assert entries[1] == ("https://example.org/s2.xml", None)

    def test_empty_urlset_returns_empty_entries(self):
        kind, entries = ss.parse_sitemap_entries(EMPTY_URLSET)
        assert kind == "urlset"
        assert entries == []

    def test_lastmod_whitespace_trimmed(self):
        body = """<?xml version="1.0"?>
<urlset><url><loc>https://example.org/x</loc><lastmod>  2026-05-01  </lastmod></url></urlset>"""
        _, entries = ss.parse_sitemap_entries(body)
        assert entries[0] == ("https://example.org/x", "2026-05-01")

    def test_namespaced_lastmod_captured(self):
        # Namespace on urlset must not prevent reading lastmod under the same ns.
        body = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://docs.rc.fas.harvard.edu/kb/x</loc>
    <lastmod>2026-03-15</lastmod>
  </url>
</urlset>"""
        _, entries = ss.parse_sitemap_entries(body)
        assert entries[0] == ("https://docs.rc.fas.harvard.edu/kb/x", "2026-03-15")

    def test_stray_non_url_child_skipped_in_entries(self):
        # A direct child that is not a <url> wrapper must be ignored — matching
        # the _locs behavior for non-wrapper children (covers the continue branch
        # in _loc_entries).
        _, entries = ss.parse_sitemap_entries(URLSET_STRAY)
        assert len(entries) == 1
        assert entries[0][0] == "https://docs.rc.fas.harvard.edu/kb/only"


# --------------------------------------------------------------------------- #
# 1.3 Regression: existing parse callers keep their behavior
# --------------------------------------------------------------------------- #
class TestParseSitemapDocumentRegression:
    def test_parse_sitemap_document_still_returns_loc_list_not_entries(self):
        # parse_sitemap_document must return (kind, List[str]) — never tuples —
        # so sources_builder and goldenset_maintenance callers are unaffected.
        kind, locs = ss.parse_sitemap_document(URLSET_LASTMOD_MIXED)
        assert kind == "urlset"
        assert locs == [
            "https://example.org/a",
            "https://example.org/b",
            "https://example.org/c",
        ]
        # Values are plain strings, not tuples.
        assert all(isinstance(loc, str) for loc in locs)

    def test_parse_sitemap_document_ignores_lastmod_entirely(self):
        # Even with lastmod present, parse_sitemap_document sees only locs.
        kind, locs = ss.parse_sitemap_document(URLSET_NS)
        assert locs == [
            "https://docs.rc.fas.harvard.edu/kb/copying-data-to-and-from-cluster-using-scp/",
            "https://docs.rc.fas.harvard.edu/kb/running-jobs",
            "https://docs.rc.fas.harvard.edu/kb/fairshare",
        ]


# --------------------------------------------------------------------------- #
# 1.2 parse_sitemap_document
# --------------------------------------------------------------------------- #
class TestParse:
    def test_namespaced_urlset(self):
        kind, locs = ss.parse_sitemap_document(URLSET_NS)
        assert kind == "urlset"
        assert locs == [
            "https://docs.rc.fas.harvard.edu/kb/copying-data-to-and-from-cluster-using-scp/",
            "https://docs.rc.fas.harvard.edu/kb/running-jobs",
            "https://docs.rc.fas.harvard.edu/kb/fairshare",
        ]

    def test_unnamespaced_urlset_parsed_identically(self):
        kind, locs = ss.parse_sitemap_document(URLSET_NONS)
        assert kind == "urlset"
        assert locs == [
            "https://docs.rc.fas.harvard.edu/kb/one",
            "https://docs.rc.fas.harvard.edu/kb/two",
        ]

    def test_sitemapindex(self):
        kind, locs = ss.parse_sitemap_document(SITEMAPINDEX)
        assert kind == "sitemapindex"
        assert locs == [
            "https://docs.rc.fas.harvard.edu/child-1.xml",
            "https://docs.rc.fas.harvard.edu/child-2.xml",
        ]

    def test_empty_urlset_is_not_a_parse_error(self):
        kind, locs = ss.parse_sitemap_document(EMPTY_URLSET)
        assert kind == "urlset"
        assert locs == []

    def test_malformed_raises(self):
        with pytest.raises(ss.SitemapParseError):
            ss.parse_sitemap_document(MALFORMED)

    def test_doctype_rejected(self):
        with pytest.raises(ss.SitemapParseError):
            ss.parse_sitemap_document(DOCTYPE_BODY)

    def test_entity_rejected(self):
        with pytest.raises(ss.SitemapParseError):
            ss.parse_sitemap_document(ENTITY_BODY)

    def test_unknown_root_raises(self):
        with pytest.raises(ss.SitemapParseError):
            ss.parse_sitemap_document(RSS_BODY)

    def test_stray_non_url_child_ignored(self):
        kind, locs = ss.parse_sitemap_document(URLSET_STRAY)
        assert kind == "urlset"
        assert locs == ["https://docs.rc.fas.harvard.edu/kb/only"]


# --------------------------------------------------------------------------- #
# 1.3 normalize_page_url
# --------------------------------------------------------------------------- #
class TestNormalize:
    def test_trailing_slash_collapsed(self):
        assert (
            ss.normalize_page_url("https://docs.rc.fas.harvard.edu/kb/scp/")
            == "https://docs.rc.fas.harvard.edu/kb/scp"
        )

    def test_root_slash_preserved(self):
        assert (
            ss.normalize_page_url("https://docs.rc.fas.harvard.edu/")
            == "https://docs.rc.fas.harvard.edu/"
        )

    def test_fragment_dropped(self):
        assert (
            ss.normalize_page_url("https://docs.rc.fas.harvard.edu/kb/x#frag")
            == "https://docs.rc.fas.harvard.edu/kb/x"
        )

    def test_scheme_and_host_lowercased(self):
        assert (
            ss.normalize_page_url("HTTPS://Docs.RC.FAS.Harvard.edu/kb/X")
            == "https://docs.rc.fas.harvard.edu/kb/X"
        )

    def test_query_preserved(self):
        assert (
            ss.normalize_page_url("https://docs.rc.fas.harvard.edu/kb/x?a=1&b=2")
            == "https://docs.rc.fas.harvard.edu/kb/x?a=1&b=2"
        )

    def test_matrix_params_slash_not_collapsed(self):
        # `;v=1` belongs to the empty trailing segment; stripping the slash would
        # move it onto `x` and change the URL (mirrors LinkScraper._normalize_url).
        assert (
            ss.normalize_page_url("https://example.com/x/;v=1")
            == "https://example.com/x/;v=1"
        )

    def test_multiple_trailing_slashes_strips_only_one(self):
        # LinkScraper strips exactly one trailing slash; sitemap normalization
        # must match, so `/x//` stays `/x/` (not `/x`) and both forms of the same
        # URL agree.
        assert ss.normalize_page_url("https://host/x//") == "https://host/x/"


# --------------------------------------------------------------------------- #
# 1.8 is_url_allowed (trust policy, v1)
# --------------------------------------------------------------------------- #
class TestTrustPolicy:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://docs.rc.fas.harvard.edu/",
            "data:text/plain;base64,AA==",
            "ftp://docs.rc.fas.harvard.edu/x",
        ],
    )
    def test_non_http_scheme_rejected(self, url):
        assert ss.is_url_allowed(url, HOST, []) is False

    def test_same_host_allowed(self):
        assert ss.is_url_allowed(f"https://{HOST}/kb/x", HOST, []) is True

    def test_cross_host_rejected_by_default(self):
        assert ss.is_url_allowed("https://evil.example.com/x", HOST, []) is False

    def test_allowlisted_host_allowed(self):
        assert (
            ss.is_url_allowed("https://cdn.example.com/x", HOST, ["cdn.example.com"])
            is True
        )

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/x",
            "http://10.0.0.5/x",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/x",
        ],
    )
    def test_ip_literal_internal_rejected_even_if_allowlisted(self, url):
        from urllib.parse import urlparse

        host = urlparse(url).hostname
        # Allowlisting the internal literal must NOT bypass the IP-range reject.
        assert ss.is_url_allowed(url, host, [host]) is False

    def test_empty_host_rejected(self):
        # http(s) scheme but no host (e.g. "https:///path") is rejected.
        assert ss.is_url_allowed("https:///path", HOST, []) is False

    @pytest.mark.parametrize(
        "url",
        [
            "http://0x7f000001/x",  # hex dword -> 127.0.0.1
            "http://2130706433/x",  # decimal dword -> 127.0.0.1
            "http://0177.0.0.1/x",  # dotted octal -> 127.0.0.1
            "http://0x7f.0.0.1/x",  # dotted hex -> 127.0.0.1
            "http://127.1/x",  # short form -> 127.0.0.1
        ],
    )
    def test_obfuscated_numeric_ip_host_rejected(self, url):
        from urllib.parse import urlparse

        host = urlparse(url).hostname
        # Even self-hosted (host == sitemap_host) and allowlisted, an obfuscated
        # numeric IP form that resolvers map to loopback must be refused — the
        # canonical-only ipaddress.ip_address() check does not catch these.
        assert ss.is_url_allowed(url, host, [host]) is False

    @pytest.mark.parametrize(
        "host,expected",
        [
            ("0x7f000001", True),  # hex dword
            ("2130706433", True),  # decimal dword
            ("0177.0.0.1", True),  # dotted octal labels
            ("127.1", True),  # short form
            ("docs.rc.fas.harvard.edu", False),  # real DNS name
            ("", False),  # empty host
            ("127..1", False),  # empty label
            ("0x", False),  # bare 0x prefix, no body
            ("0xzz", False),  # non-hex body
        ],
    )
    def test_is_numeric_host_classification(self, host, expected):
        assert ss._is_numeric_host(host) is expected

    def test_public_ip_literal_still_allowed(self):
        # A canonical, non-internal IP literal remains allowable (v1 does not ban
        # all IP hosts — only loopback/private/link-local and obfuscated forms).
        assert ss.is_url_allowed("https://93.184.216.34/x", "93.184.216.34", []) is True

    @pytest.mark.parametrize(
        "url",
        [
            f"https://{HOST}:99999/kb/x",  # out-of-range port
            f"https://{HOST}:0/kb/x",  # unroutable port 0
            f"https://{HOST}:notaport/kb/x",  # non-numeric port
        ],
    )
    def test_invalid_port_rejected(self, url):
        # A same-host <loc> with a malformed/out-of-range port parses to the
        # allowed hostname but is unfetchable; it must be refused so it is never
        # emitted or counted toward the floor.
        assert ss.is_url_allowed(url, HOST, []) is False


# --------------------------------------------------------------------------- #
# 1.4 expand_sitemap_source / expand_sitemaps (happy paths + fail-open)
# --------------------------------------------------------------------------- #
class TestExpand:
    def test_flat_urlset_normalized(self):
        url = f"https://{HOST}/kb/sitemap.xml"
        fetch = FakeFetch({url: URLSET_NS})
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert pages == [
            (
                "https://docs.rc.fas.harvard.edu/kb/copying-data-to-and-from-cluster-using-scp",
                "2026-01-01",
            ),
            ("https://docs.rc.fas.harvard.edu/kb/running-jobs", "2026-01-02"),
            ("https://docs.rc.fas.harvard.edu/kb/fairshare", "2026-01-03"),
        ]
        assert fetch.calls == [url]

    def test_sitemapindex_children_fetched_once(self):
        url = f"https://{HOST}/index.xml"
        fetch = FakeFetch(
            {
                url: SITEMAPINDEX,
                f"https://{HOST}/child-1.xml": CHILD_1,
                f"https://{HOST}/child-2.xml": CHILD_2,
            }
        )
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert pages == [
            ("https://docs.rc.fas.harvard.edu/kb/1a", None),
            ("https://docs.rc.fas.harvard.edu/kb/1b", None),
            ("https://docs.rc.fas.harvard.edu/kb/2a", None),
        ]
        # index + 2 children, each exactly once
        assert fetch.calls.count(f"https://{HOST}/child-1.xml") == 1
        assert fetch.calls.count(f"https://{HOST}/child-2.xml") == 1

    def test_duplicate_child_loc_fetched_once(self):
        # A <sitemapindex> that repeats the same child <loc> (e.g. a generator
        # bug) must fetch that child exactly once, not once per occurrence — a
        # large duplicate index would otherwise cause redundant network reads.
        url = f"https://{HOST}/index.xml"
        child_url = f"https://{HOST}/child-2.xml"
        dup_index = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"  <sitemap><loc>{child_url}</loc></sitemap>\n"
            f"  <sitemap><loc>{child_url}</loc></sitemap>\n"
            "</sitemapindex>"
        )
        fetch = FakeFetch({url: dup_index, child_url: CHILD_2})
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert fetch.calls.count(child_url) == 1
        assert pages == [("https://docs.rc.fas.harvard.edu/kb/2a", None)]

    def test_nested_index_contributes_nothing(self):
        url = f"https://{HOST}/index.xml"
        fetch = FakeFetch(
            {
                url: SITEMAPINDEX,
                f"https://{HOST}/child-1.xml": NESTED_INDEX,
                f"https://{HOST}/child-2.xml": CHILD_2,
            }
        )
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert pages == [("https://docs.rc.fas.harvard.edu/kb/2a", None)]
        # grandchild is never followed
        assert f"https://{HOST}/grandchild.xml" not in fetch.calls

    def test_failed_child_fails_open_siblings_survive(self, caplog):
        url = f"https://{HOST}/index.xml"
        fetch = FakeFetch(
            {
                url: SITEMAPINDEX,
                # child-1 missing -> FakeFetch raises SitemapFetchError
                f"https://{HOST}/child-2.xml": CHILD_2,
            }
        )
        with caplog.at_level(logging.WARNING):
            pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert pages == [("https://docs.rc.fas.harvard.edu/kb/2a", None)]
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)

    def test_duplicate_locs_emitted_once_order_preserving(self):
        dup = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.rc.fas.harvard.edu/kb/x/</loc></url>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/x</loc></url>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/y</loc></url>
</urlset>"""
        url = f"https://{HOST}/s.xml"
        pages = ss.expand_sitemap_source(url, FakeFetch({url: dup}), _policy())
        assert pages == [
            ("https://docs.rc.fas.harvard.edu/kb/x", None),
            ("https://docs.rc.fas.harvard.edu/kb/y", None),
        ]

    def test_expand_sitemaps_merges_and_dedupes_across_sources(self):
        a = f"https://{HOST}/a.xml"
        b = f"https://{HOST}/b.xml"
        body_a = """<?xml version="1.0"?>
<urlset><url><loc>https://docs.rc.fas.harvard.edu/kb/shared</loc></url>
<url><loc>https://docs.rc.fas.harvard.edu/kb/only-a</loc></url></urlset>"""
        body_b = """<?xml version="1.0"?>
<urlset><url><loc>https://docs.rc.fas.harvard.edu/kb/shared</loc></url>
<url><loc>https://docs.rc.fas.harvard.edu/kb/only-b</loc></url></urlset>"""
        fetch = FakeFetch({a: body_a, b: body_b})
        pages = ss.expand_sitemaps([a, b], fetch, _policy())
        assert pages == [
            ("https://docs.rc.fas.harvard.edu/kb/shared", None),
            ("https://docs.rc.fas.harvard.edu/kb/only-a", None),
            ("https://docs.rc.fas.harvard.edu/kb/only-b", None),
        ]


# --------------------------------------------------------------------------- #
# 1.9 emitted-page validation at emit time
# --------------------------------------------------------------------------- #
class TestEmitValidation:
    def test_off_host_and_internal_loc_dropped(self, caplog):
        body = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/good</loc></url>
  <url><loc>https://evil.example.com/x</loc></url>
  <url><loc>http://169.254.169.254/latest/meta-data/</loc></url>
</urlset>"""
        url = f"https://{HOST}/s.xml"
        with caplog.at_level(logging.WARNING):
            pages = ss.expand_sitemap_source(url, FakeFetch({url: body}), _policy())
        assert pages == [("https://docs.rc.fas.harvard.edu/kb/good", None)]

    def test_off_host_child_sitemap_not_fetched(self):
        index = """<?xml version="1.0"?>
<sitemapindex>
  <sitemap><loc>https://evil.example.com/child.xml</loc></sitemap>
  <sitemap><loc>https://docs.rc.fas.harvard.edu/child-2.xml</loc></sitemap>
</sitemapindex>"""
        url = f"https://{HOST}/index.xml"
        fetch = FakeFetch({url: index, f"https://{HOST}/child-2.xml": CHILD_2})
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert pages == [("https://docs.rc.fas.harvard.edu/kb/2a", None)]
        assert "https://evil.example.com/child.xml" not in fetch.calls


# --------------------------------------------------------------------------- #
# 1.10 per-source cap  /  1.11 floor
# --------------------------------------------------------------------------- #
class TestCapAndFloor:
    def test_over_cap_fails_zero_urls(self, caplog):
        url = f"https://{HOST}/s.xml"
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ss.SitemapExpansionError) as exc:
                ss.expand_sitemap_source(
                    url, FakeFetch({url: URLSET_NS}), _policy(max_pages=2)
                )
        assert exc.value.reason == "over_cap"
        assert any(rec.levelno == logging.ERROR for rec in caplog.records)

    def test_at_cap_ok(self):
        url = f"https://{HOST}/s.xml"
        pages = ss.expand_sitemap_source(
            url, FakeFetch({url: URLSET_NS}), _policy(max_pages=3)
        )
        assert len(pages) == 3

    def test_cap_measured_across_index_children(self):
        url = f"https://{HOST}/index.xml"
        fetch = FakeFetch(
            {
                url: SITEMAPINDEX,
                f"https://{HOST}/child-1.xml": CHILD_1,  # 2 pages
                f"https://{HOST}/child-2.xml": CHILD_2,  # 1 page  -> 3 total
            }
        )
        with pytest.raises(ss.SitemapExpansionError) as exc:
            ss.expand_sitemap_source(url, fetch, _policy(max_pages=2))
        assert exc.value.reason == "over_cap"

    @pytest.mark.parametrize("body", [EMPTY_URLSET, MALFORMED])
    def test_below_floor_fails(self, body, caplog):
        url = f"https://{HOST}/s.xml"
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ss.SitemapExpansionError) as exc:
                ss.expand_sitemap_source(
                    url, FakeFetch({url: body}), _policy(min_pages=1)
                )
        assert exc.value.reason == "below_floor"

    def test_fetch_failure_is_below_floor_not_crash(self):
        url = f"https://{HOST}/s.xml"
        # missing body -> fetch raises -> zero pages -> below floor
        with pytest.raises(ss.SitemapExpansionError) as exc:
            ss.expand_sitemap_source(url, FakeFetch({}), _policy(min_pages=1))
        assert exc.value.reason == "below_floor"

    def test_wholesale_trust_rejection_is_below_floor(self):
        body = """<?xml version="1.0"?>
<urlset><url><loc>https://evil.example.com/x</loc></url></urlset>"""
        url = f"https://{HOST}/s.xml"
        with pytest.raises(ss.SitemapExpansionError) as exc:
            ss.expand_sitemap_source(url, FakeFetch({url: body}), _policy(min_pages=1))
        assert exc.value.reason == "below_floor"

    def test_at_or_above_floor_ok(self):
        url = f"https://{HOST}/s.xml"
        pages = ss.expand_sitemap_source(
            url, FakeFetch({url: URLSET_NS}), _policy(min_pages=3)
        )
        assert len(pages) == 3


# --------------------------------------------------------------------------- #
# 1.12 per-source isolation
# --------------------------------------------------------------------------- #
class TestIsolation:
    def test_one_source_below_floor_not_masked(self):
        a = f"https://{HOST}/a.xml"
        b = f"https://{HOST}/b.xml"
        fetch = FakeFetch({a: URLSET_NS, b: EMPTY_URLSET})
        with pytest.raises(ss.SitemapExpansionError) as exc:
            ss.expand_sitemaps([a, b], fetch, _policy(min_pages=1))
        assert exc.value.reason == "below_floor"

    def test_host_policy_is_per_source(self):
        # Source A on a.example.com; its document emits a <loc> on b.example.com
        # (source B's host). That is cross-host for A and must be rejected.
        assert (
            ss.is_url_allowed("https://b.example.com/x", "a.example.com", []) is False
        )


# --------------------------------------------------------------------------- #
# 1.7 fetch helper (requests-backed)
# --------------------------------------------------------------------------- #
class TestFetchHelper:
    def _resp(self, *, status=200, chunks=(b"<urlset/>",), headers=None, url=None):
        resp = MagicMock()
        resp.status_code = status
        resp.headers = headers or {}
        resp.url = url or "https://docs.rc.fas.harvard.edu/s.xml"
        resp.encoding = "utf-8"
        resp.apparent_encoding = "utf-8"
        resp.iter_content.return_value = iter(chunks)
        return resp

    def test_get_uses_verify_and_timeout_and_raise_for_status(self):
        resp = self._resp()
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            return_value=resp,
        ) as get:
            text = ss.fetch_sitemap_text(
                "https://docs.rc.fas.harvard.edu/s.xml", verify=True
            )
        assert text == "<urlset/>"
        _, kwargs = get.call_args
        assert kwargs["verify"] is True
        assert "timeout" in kwargs
        assert resp.raise_for_status.called

    def test_body_size_cap_enforced(self):
        big = self._resp(chunks=(b"x" * 10, b"y" * 10))
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            return_value=big,
        ):
            with pytest.raises(ss.SitemapFetchError):
                ss.fetch_sitemap_text(
                    "https://docs.rc.fas.harvard.edu/s.xml",
                    verify=False,
                    max_bytes=5,
                )

    def test_cross_host_redirect_not_followed(self):
        redirect = self._resp(
            status=301,
            headers={"Location": "https://evil.example.com/x"},
            url="https://docs.rc.fas.harvard.edu/s.xml",
        )
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            return_value=redirect,
        ) as get:
            with pytest.raises(ss.SitemapFetchError):
                ss.fetch_sitemap_text(
                    "https://docs.rc.fas.harvard.edu/s.xml", verify=False
                )
        # the redirect target on a different host is never fetched
        assert get.call_count == 1

    def test_connection_error_raises_fetch_error(self):
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            side_effect=requests.exceptions.ConnectionError("boom"),
        ):
            with pytest.raises(ss.SitemapFetchError):
                ss.fetch_sitemap_text(
                    "https://docs.rc.fas.harvard.edu/s.xml", verify=False
                )

    def test_read_error_raises_fetch_error(self):
        resp = self._resp()
        resp.iter_content.side_effect = requests.exceptions.ChunkedEncodingError(
            "mid-stream"
        )
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            return_value=resp,
        ):
            with pytest.raises(ss.SitemapFetchError):
                ss.fetch_sitemap_text(
                    "https://docs.rc.fas.harvard.edu/s.xml", verify=False
                )

    def test_same_host_redirect_followed(self):
        redirect = self._resp(
            status=301,
            headers={"Location": "https://docs.rc.fas.harvard.edu/final.xml"},
            url="https://docs.rc.fas.harvard.edu/s.xml",
        )
        # an empty chunk is skipped, then the body is read
        ok = self._resp(
            chunks=(b"", b"<urlset/>"), url="https://docs.rc.fas.harvard.edu/final.xml"
        )
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            side_effect=[redirect, ok],
        ) as get:
            text = ss.fetch_sitemap_text(
                "https://docs.rc.fas.harvard.edu/s.xml", verify=False
            )
        assert text == "<urlset/>"
        assert get.call_count == 2

    def test_same_host_downgrade_redirect_followed_by_default(self):
        # The ingest's behaviour is unchanged: `require_https` is opt-in, so this
        # PR cannot alter what a running ingest fetches.
        redirect = self._resp(
            status=301,
            headers={"Location": "http://docs.rc.fas.harvard.edu/final.xml"},
            url="https://docs.rc.fas.harvard.edu/s.xml",
        )
        ok = self._resp(url="http://docs.rc.fas.harvard.edu/final.xml")
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            side_effect=[redirect, ok],
        ) as get:
            text = ss.fetch_sitemap_text(
                "https://docs.rc.fas.harvard.edu/s.xml", verify=False
            )
        assert text == "<urlset/>"
        assert get.call_count == 2

    def test_require_https_refuses_a_same_host_downgrade_redirect(self):
        # The host check cannot see this: the target host matches, only the
        # scheme drops. A caller that verifies TLS on hop one still ends up
        # reading hop two in the clear unless the redirect itself is refused.
        redirect = self._resp(
            status=301,
            headers={"Location": "http://docs.rc.fas.harvard.edu/final.xml"},
            url="https://docs.rc.fas.harvard.edu/s.xml",
        )
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            return_value=redirect,
        ) as get:
            with pytest.raises(ss.SitemapFetchError):
                ss.fetch_sitemap_text(
                    "https://docs.rc.fas.harvard.edu/s.xml",
                    verify=True,
                    require_https=True,
                )
        # the plaintext target is never requested
        assert get.call_count == 1

    def test_require_https_refuses_a_plaintext_start_url_without_dialing(self):
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get"
        ) as get:
            with pytest.raises(ss.SitemapFetchError):
                ss.fetch_sitemap_text(
                    "http://docs.rc.fas.harvard.edu/s.xml",
                    verify=True,
                    require_https=True,
                )
        assert get.call_count == 0

    def test_require_https_still_follows_a_same_host_https_redirect(self):
        redirect = self._resp(
            status=301,
            headers={"Location": "https://docs.rc.fas.harvard.edu/final.xml"},
            url="https://docs.rc.fas.harvard.edu/s.xml",
        )
        ok = self._resp(url="https://docs.rc.fas.harvard.edu/final.xml")
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            side_effect=[redirect, ok],
        ) as get:
            text = ss.fetch_sitemap_text(
                "https://docs.rc.fas.harvard.edu/s.xml",
                verify=True,
                require_https=True,
            )
        assert text == "<urlset/>"
        assert get.call_count == 2

    def test_missing_charset_does_not_probe_apparent_encoding(self):
        # application/xml with no charset -> resp.encoding is None. Accessing
        # resp.apparent_encoding AFTER streaming reads resp.content, which the
        # real requests.Response raises RuntimeError on (already consumed). The
        # fetch must decode with a fixed fallback and never touch it.
        class _Resp:
            status_code = 200
            headers: dict = {}
            url = "https://docs.rc.fas.harvard.edu/s.xml"
            encoding = None

            @property
            def apparent_encoding(self):
                raise RuntimeError("content already consumed")

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=65536):
                return iter((b"<urlset/>",))

            def close(self):
                return None

        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            return_value=_Resp(),
        ):
            text = ss.fetch_sitemap_text(
                "https://docs.rc.fas.harvard.edu/s.xml", verify=False
            )
        assert text == "<urlset/>"

    def test_unsupported_charset_falls_back_to_utf8(self):
        # A response header declaring an unknown charset makes resp.encoding a
        # bogus codec name; decode() raises LookupError (errors="replace" does not
        # help for an unknown codec). The fetch must fall back to UTF-8, not crash.
        resp = self._resp(chunks=(b"<urlset/>",))
        resp.encoding = "bogus-9000"
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            return_value=resp,
        ):
            text = ss.fetch_sitemap_text(
                "https://docs.rc.fas.harvard.edu/s.xml", verify=False
            )
        assert text == "<urlset/>"

    def test_too_many_redirects_raises(self):
        redirect = self._resp(
            status=301,
            headers={"Location": "https://docs.rc.fas.harvard.edu/loop.xml"},
            url="https://docs.rc.fas.harvard.edu/loop.xml",
        )
        with patch(
            "src.data_manager.collectors.scrapers.sitemap_source.requests.get",
            return_value=redirect,
        ) as get:
            with pytest.raises(ss.SitemapFetchError):
                ss.fetch_sitemap_text(
                    "https://docs.rc.fas.harvard.edu/s.xml", verify=False
                )
        assert get.call_count == ss._MAX_REDIRECTS + 1


# --------------------------------------------------------------------------- #
# 1.5 ScraperManager._collect_urls_from_lists_by_type routing
# --------------------------------------------------------------------------- #
class TestRouting:
    def _mgr(self, lines):
        mgr = ScraperManager.__new__(ScraperManager)
        mgr.indico_enabled = False
        mgr.indico_config = {}
        mgr._collect_urls_from_lists = lambda input_lists: list(lines)
        return mgr

    def test_sitemap_line_peeled_into_bucket(self):
        mgr = self._mgr(
            [
                "https://plain.example.com/page",
                "git-https://github.com/fasrc/User_Codes",
                "sso-https://sso.example.com/x",
                "elog-https://elog.example.com/x",
                "indico-https://indico.example.com/event/1",
                "sitemap-https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml",
            ]
        )
        (
            link_urls,
            git_urls,
            sso_urls,
            elog_urls,
            indico_urls,
            sitemap_urls,
        ) = mgr._collect_urls_from_lists_by_type(["x"])
        assert sitemap_urls == [
            "https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml"
        ]
        assert git_urls == ["https://github.com/fasrc/User_Codes"]
        assert sso_urls == ["https://sso.example.com/x"]
        assert elog_urls == ["https://elog.example.com/x"]
        assert indico_urls == ["https://indico.example.com/event/1"]
        assert link_urls == ["https://plain.example.com/page"]

    def test_sitemap_prefix_beats_elog_autodetect(self):
        mgr = self._mgr(
            ["sitemap-https://docs.rc.fas.harvard.edu/elog/epkb-sitemap.xml"]
        )
        result = mgr._collect_urls_from_lists_by_type(["x"])
        assert result[5] == ["https://docs.rc.fas.harvard.edu/elog/epkb-sitemap.xml"]
        assert result[3] == []  # elog bucket empty

    def test_tuple_return_annotation_postponed_for_py37(self):
        # `from __future__ import annotations` keeps the 6-tuple return annotation
        # a string, so the bare `tuple[...]` subscript never eval-breaks on import
        # under Python 3.7/3.8 (tuple is not subscriptable before 3.9).
        ann = ScraperManager._collect_urls_from_lists_by_type.__annotations__["return"]
        assert isinstance(ann, str)


# --------------------------------------------------------------------------- #
# 1.6 / 3.7 ScraperManager.collect_all_from_config wiring
# --------------------------------------------------------------------------- #
class TestWiring:
    def _mgr(self):
        mgr = ScraperManager.__new__(ScraperManager)
        mgr.input_lists = ["x"]
        mgr.git_enabled = False
        mgr.sso_enabled = False
        return mgr

    def _patches(self, by_type, expand):
        return [
            patch.object(
                ScraperManager,
                "_collect_urls_from_lists_by_type",
                return_value=by_type,
            ),
            patch.object(ScraperManager, "collect_sso"),
            patch.object(ScraperManager, "collect_git"),
            patch.object(ScraperManager, "collect_elog"),
            patch.object(ScraperManager, "collect_indico"),
        ] + expand

    def test_expand_called_and_urls_appended(self):
        mgr = self._mgr()
        by_type = (["https://a"], [], [], [], [], ["https://s.xml"])
        with patch.object(
            ScraperManager, "_expand_sitemaps", return_value=["https://b", "https://c"]
        ) as exp, patch.object(ScraperManager, "collect_links") as cl, patch.object(
            ScraperManager, "_collect_urls_from_lists_by_type", return_value=by_type
        ), patch.object(
            ScraperManager, "collect_sso"
        ), patch.object(
            ScraperManager, "collect_git"
        ), patch.object(
            ScraperManager, "collect_elog"
        ), patch.object(
            ScraperManager, "collect_indico"
        ):
            mgr.collect_all_from_config(MagicMock())
        exp.assert_called_once_with(["https://s.xml"])
        _, kwargs = cl.call_args
        assert kwargs["link_urls"] == ["https://a", "https://b", "https://c"]

    def test_expanded_url_deduped_against_slash_variant_handlist(self):
        # A hand-listed trailing-slash variant and the normalized sitemap URL are
        # the SAME page; only one must reach collect_links. LinkScraper does not
        # dedup across seeds, so this guards the #118 slash-variant dup-chunk issue
        # during the hand-list -> sitemap migration window. A malformed hand-list
        # entry (unnormalizable) is kept verbatim as its own key.
        mgr = self._mgr()
        by_type = (
            [f"https://{HOST}/kb/page/", "https://[malformed"],
            [],
            [],
            [],
            [],
            ["https://s.xml"],
        )
        with patch.object(
            ScraperManager,
            "_expand_sitemaps",
            return_value=[f"https://{HOST}/kb/page", f"https://{HOST}/kb/new"],
        ), patch.object(ScraperManager, "collect_links") as cl, patch.object(
            ScraperManager, "_collect_urls_from_lists_by_type", return_value=by_type
        ), patch.object(
            ScraperManager, "collect_sso"
        ), patch.object(
            ScraperManager, "collect_git"
        ), patch.object(
            ScraperManager, "collect_elog"
        ), patch.object(
            ScraperManager, "collect_indico"
        ):
            mgr.collect_all_from_config(MagicMock())
        _, kwargs = cl.call_args
        # /kb/page (normalized dup of the /kb/page/ hand-list entry) is NOT re-added;
        # hand-list entries are preserved and only the genuinely new page is added.
        assert kwargs["link_urls"] == [
            f"https://{HOST}/kb/page/",
            "https://[malformed",
            f"https://{HOST}/kb/new",
        ]

    def test_no_sitemap_bucket_skips_expand(self):
        mgr = self._mgr()
        by_type = (["https://a"], [], [], [], [], [])
        with patch.object(ScraperManager, "_expand_sitemaps") as exp, patch.object(
            ScraperManager, "collect_links"
        ) as cl, patch.object(
            ScraperManager, "_collect_urls_from_lists_by_type", return_value=by_type
        ), patch.object(
            ScraperManager, "collect_sso"
        ), patch.object(
            ScraperManager, "collect_git"
        ), patch.object(
            ScraperManager, "collect_elog"
        ), patch.object(
            ScraperManager, "collect_indico"
        ):
            mgr.collect_all_from_config(MagicMock())
        exp.assert_not_called()
        _, kwargs = cl.call_args
        assert kwargs["link_urls"] == ["https://a"]

    def test_expansion_error_propagates_and_fails_ingest(self):
        mgr = self._mgr()
        by_type = ([], [], [], [], [], ["https://s.xml"])
        with patch.object(
            ScraperManager,
            "_expand_sitemaps",
            side_effect=ss.SitemapExpansionError("boom", reason="below_floor"),
        ), patch.object(ScraperManager, "collect_links"), patch.object(
            ScraperManager, "_collect_urls_from_lists_by_type", return_value=by_type
        ), patch.object(
            ScraperManager, "collect_sso"
        ), patch.object(
            ScraperManager, "collect_git"
        ), patch.object(
            ScraperManager, "collect_elog"
        ), patch.object(
            ScraperManager, "collect_indico"
        ):
            with pytest.raises(ss.SitemapExpansionError):
                mgr.collect_all_from_config(MagicMock())

    def test_expand_sitemaps_builds_policy_and_delegates(self):
        mgr = ScraperManager.__new__(ScraperManager)
        mgr.config = {"verify_urls": True}
        mgr.sitemap_config = {
            "allowed_hosts": ["cdn.example.com"],
            "min_pages": 2,
            "max_pages": 50,
        }
        captured = {}

        def fake_expand(sitemap_urls, fetch, policy):
            captured["urls"] = sitemap_urls
            captured["policy"] = policy
            captured["fetch"] = fetch
            return [("https://docs.rc.fas.harvard.edu/kb/x", "2026-01-01")]

        with patch.object(ss, "expand_sitemaps", side_effect=fake_expand):
            out = mgr._expand_sitemaps(["https://s.xml"])

        assert out == ["https://docs.rc.fas.harvard.edu/kb/x"]
        assert captured["urls"] == ["https://s.xml"]
        assert captured["policy"].allowed_hosts == ["cdn.example.com"]
        assert captured["policy"].min_pages == 2
        assert captured["policy"].max_pages == 50
        # verify flag is bound into the injected fetch callable
        assert captured["fetch"].keywords.get("verify") is True

    def test_scalar_allowed_hosts_coerced_to_list(self):
        # A YAML scalar (not a list) must not char-explode via list("host").
        mgr = ScraperManager.__new__(ScraperManager)
        mgr.config = {}
        mgr.sitemap_config = {"allowed_hosts": "cdn.example.com"}
        captured = {}

        def fake(urls, fetch, policy):
            captured["policy"] = policy
            return []

        with patch.object(ss, "expand_sitemaps", side_effect=fake):
            mgr._expand_sitemaps(["https://s.xml"])
        assert captured["policy"].allowed_hosts == ["cdn.example.com"]

    def test_null_or_empty_bounds_fall_back_to_defaults(self):
        mgr = ScraperManager.__new__(ScraperManager)
        mgr.config = {}
        mgr.sitemap_config = {"min_pages": None, "max_pages": ""}
        captured = {}

        def fake(urls, fetch, policy):
            captured["policy"] = policy
            return []

        with patch.object(ss, "expand_sitemaps", side_effect=fake):
            mgr._expand_sitemaps(["https://s.xml"])
        assert captured["policy"].min_pages == 1
        assert captured["policy"].max_pages == 20000


# --------------------------------------------------------------------------- #
# Robustness: a malformed <loc> must fail open per document, never crash ingest
# --------------------------------------------------------------------------- #
class TestRobustness:
    def test_is_url_allowed_false_on_unparseable(self):
        assert (
            ss.is_url_allowed("https://docs.rc.fas.harvard.edu[evil/x", HOST, [])
            is False
        )

    def test_unparseable_page_loc_dropped_fails_open(self, caplog):
        body = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/good</loc></url>
  <url><loc>https://docs.rc.fas.harvard.edu[evil.example.com/x</loc></url>
</urlset>"""
        url = f"https://{HOST}/s.xml"
        with caplog.at_level(logging.WARNING):
            pages = ss.expand_sitemap_source(url, FakeFetch({url: body}), _policy())
        assert pages == [("https://docs.rc.fas.harvard.edu/kb/good", None)]

    def test_unparseable_child_sitemap_loc_dropped(self):
        index = """<?xml version="1.0"?>
<sitemapindex>
  <sitemap><loc>https://docs.rc.fas.harvard.edu[bad/child.xml</loc></sitemap>
  <sitemap><loc>https://docs.rc.fas.harvard.edu/child-2.xml</loc></sitemap>
</sitemapindex>"""
        url = f"https://{HOST}/index.xml"
        fetch = FakeFetch({url: index, f"https://{HOST}/child-2.xml": CHILD_2})
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert pages == [("https://docs.rc.fas.harvard.edu/kb/2a", None)]
        assert "https://docs.rc.fas.harvard.edu[bad/child.xml" not in fetch.calls

    def test_malformed_sitemap_url_is_below_floor_not_crash(self):
        # A malformed top-level sitemap URL (stray `[`) must not crash the run:
        # _host_of yields "" and the source emits nothing -> controlled below_floor.
        bad = "https://docs.rc.fas.harvard.edu[bad/sitemap.xml"
        with pytest.raises(ss.SitemapExpansionError) as exc:
            ss.expand_sitemap_source(bad, FakeFetch({}), _policy(min_pages=1))
        assert exc.value.reason == "below_floor"

    def test_top_level_internal_ip_literal_not_fetched(self):
        # A top-level sitemap URL pointing at an internal IP literal must be
        # rejected by the trust policy BEFORE it is fetched (no SSRF request),
        # then fail below-floor. Even if the fake would return data, it is
        # never contacted.
        bad = "http://169.254.169.254/latest/meta-data/"
        fetch = FakeFetch({bad: URLSET_NS})
        with pytest.raises(ss.SitemapExpansionError) as exc:
            ss.expand_sitemap_source(bad, fetch, _policy(min_pages=1))
        assert exc.value.reason == "below_floor"
        assert fetch.calls == []  # never contacted

    def test_top_level_non_http_scheme_not_fetched(self):
        bad = "file:///etc/passwd"
        fetch = FakeFetch({bad: URLSET_NS})
        with pytest.raises(ss.SitemapExpansionError):
            ss.expand_sitemap_source(bad, fetch, _policy(min_pages=1))
        assert fetch.calls == []

    def test_top_level_trusted_host_still_fetched(self):
        # A normal first-party host is unaffected — still fetched and expanded.
        url = f"https://{HOST}/kb/sitemap.xml"
        fetch = FakeFetch({url: URLSET_NS})
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert fetch.calls == [url]
        assert len(pages) == 3


# --------------------------------------------------------------------------- #
# 2.1 expand_sitemap_source / expand_sitemaps — TDD: emit (url, lastmod|None) pairs
# --------------------------------------------------------------------------- #
class TestExpandPairs:
    """TDD: expand_sitemap_source and expand_sitemaps must return
    List[Tuple[str, Optional[str]]] — (normalized_url, lastmod|None) —
    instead of List[str]. All tests in this class fail until task 2.2 implements
    the new return shape.
    """

    def test_urlset_with_lastmod_returns_pairs(self):
        url = f"https://{HOST}/kb/sitemap.xml"
        fetch = FakeFetch({url: URLSET_NS})
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert pages == [
            (
                "https://docs.rc.fas.harvard.edu/kb/copying-data-to-and-from-cluster-using-scp",
                "2026-01-01",
            ),
            ("https://docs.rc.fas.harvard.edu/kb/running-jobs", "2026-01-02"),
            ("https://docs.rc.fas.harvard.edu/kb/fairshare", "2026-01-03"),
        ]

    def test_urlset_without_lastmod_yields_none(self):
        url = f"https://{HOST}/kb/sitemap.xml"
        fetch = FakeFetch({url: URLSET_NONS})
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert pages == [
            ("https://docs.rc.fas.harvard.edu/kb/one", None),
            ("https://docs.rc.fas.harvard.edu/kb/two", None),
        ]

    def test_mixed_urlset_correct_lastmod_per_entry(self):
        # URLSET_LASTMOD_MIXED: /a has lastmod, /b absent, /c empty lastmod.
        url = "https://example.org/sitemap.xml"
        fetch = FakeFetch({url: URLSET_LASTMOD_MIXED})
        pages = ss.expand_sitemap_source(url, fetch, _policy(min_pages=1))
        assert pages == [
            ("https://example.org/a", "2026-04-21T19:19:35+00:00"),
            ("https://example.org/b", None),
            ("https://example.org/c", None),
        ]

    def test_normalization_collision_first_url_and_lastmod_wins(self):
        # /kb/x/ and /kb/x normalize to the same URL; the first entry (lastmod
        # "2026-01-01") wins — the duplicate (lastmod "2026-02-01") is dropped.
        url = f"https://{HOST}/s.xml"
        fetch = FakeFetch({url: URLSET_DUP_WITH_LASTMOD})
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert pages == [
            ("https://docs.rc.fas.harvard.edu/kb/x", "2026-01-01"),
            ("https://docs.rc.fas.harvard.edu/kb/y", "2026-03-01"),
        ]

    def test_trust_filter_unchanged_off_host_dropped(self, caplog):
        body = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://docs.rc.fas.harvard.edu/kb/good</loc><lastmod>2026-05-01</lastmod></url>
  <url><loc>https://evil.example.com/x</loc><lastmod>2026-05-01</lastmod></url>
</urlset>"""
        url = f"https://{HOST}/s.xml"
        with caplog.at_level(logging.WARNING):
            pages = ss.expand_sitemap_source(url, FakeFetch({url: body}), _policy())
        assert pages == [("https://docs.rc.fas.harvard.edu/kb/good", "2026-05-01")]

    def test_expand_sitemaps_pairs_merged_deduped_first_wins(self):
        # shared URL appears in both sources; the first source's lastmod wins.
        a = f"https://{HOST}/a.xml"
        b = f"https://{HOST}/b.xml"
        body_a = """<?xml version="1.0"?>
<urlset><url><loc>https://docs.rc.fas.harvard.edu/kb/shared</loc><lastmod>2026-01-01</lastmod></url>
<url><loc>https://docs.rc.fas.harvard.edu/kb/only-a</loc><lastmod>2026-01-02</lastmod></url></urlset>"""
        body_b = """<?xml version="1.0"?>
<urlset><url><loc>https://docs.rc.fas.harvard.edu/kb/shared</loc><lastmod>2026-02-01</lastmod></url>
<url><loc>https://docs.rc.fas.harvard.edu/kb/only-b</loc><lastmod>2026-02-02</lastmod></url></urlset>"""
        fetch = FakeFetch({a: body_a, b: body_b})
        pages = ss.expand_sitemaps([a, b], fetch, _policy())
        assert pages == [
            ("https://docs.rc.fas.harvard.edu/kb/shared", "2026-01-01"),
            ("https://docs.rc.fas.harvard.edu/kb/only-a", "2026-01-02"),
            ("https://docs.rc.fas.harvard.edu/kb/only-b", "2026-02-02"),
        ]

    def test_sitemapindex_child_pages_carry_lastmods(self):
        # Child urlsets with lastmod — child page pairs carry correct values.
        url = f"https://{HOST}/index.xml"
        child1 = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.rc.fas.harvard.edu/kb/1a</loc><lastmod>2026-06-01</lastmod></url>
</urlset>"""
        child2 = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.rc.fas.harvard.edu/kb/2a</loc></url>
</urlset>"""
        fetch = FakeFetch(
            {
                url: SITEMAPINDEX,
                f"https://{HOST}/child-1.xml": child1,
                f"https://{HOST}/child-2.xml": child2,
            }
        )
        pages = ss.expand_sitemap_source(url, fetch, _policy())
        assert pages == [
            ("https://docs.rc.fas.harvard.edu/kb/1a", "2026-06-01"),
            ("https://docs.rc.fas.harvard.edu/kb/2a", None),
        ]
