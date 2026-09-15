"""
Unit tests for LinkScraper relative-link resolution (issue #118 review, PR #128).

After `_normalize_url` began collapsing trailing slashes, directory-style pages
(``…/kb/``) reached the crawl loop as their slash-stripped form (``…/kb``). Because
`get_links_with_same_hostname` re-normalized its base before `urljoin`, a path-relative
href like ``child.html`` resolved against ``/kb`` (file semantics) and yielded
``/child.html`` instead of ``/kb/child.html`` — silently mis-crawling whole sections.

These assert the base is the raw, slash-bearing URL (the fetched response's final,
post-redirect URL), so relative links resolve correctly, while discovered links are
still normalized for dedup and the same-host filter.
"""

from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource
from src.data_manager.collectors.scrapers.scraper import LinkScraper


def _html_page(html: str) -> ScrapedResource:
    return ScrapedResource(
        url="x", content=html, suffix="html", source_type="web", metadata={}
    )


class _FakeResponse:
    """Minimal stand-in for a requests.Response along the html crawl path."""

    def __init__(self, text: str, url: str):
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.encoding = "utf-8"
        self.headers = {"Content-type": "text/html"}


class TestRelativeLinkResolution:

    def test_relative_link_resolves_against_directory_base(self):
        scraper = LinkScraper()
        page = _html_page('<html><body><a href="child.html">c</a></body></html>')
        links = scraper.get_links_with_same_hostname("https://host/kb/", page)
        assert "https://host/kb/child.html" in links
        assert "https://host/child.html" not in links

    def test_off_host_link_still_dropped(self):
        scraper = LinkScraper()
        page = _html_page(
            '<html><body><a href="https://other.test/x">o</a>'
            '<a href="child.html">c</a></body></html>'
        )
        links = scraper.get_links_with_same_hostname("https://host/kb/", page)
        assert "https://other.test/x" not in links
        assert "https://host/kb/child.html" in links


class TestReapUsesFinalUrl:

    def test_reap_resolves_relative_against_post_redirect_url(self):
        # The frontier feeds `current_url` as the slash-STRIPPED form, but the
        # server 301-redirects to the trailing-slash form. Relative links must
        # resolve against the response's FINAL url, not the requested one.
        scraper = LinkScraper()
        html = '<html><body><a href="child.html">c</a></body></html>'
        response = _FakeResponse(html, url="https://host/kb/")
        new_links, _resources = scraper.reap(response, "https://host/kb")
        assert "https://host/kb/child.html" in new_links
        assert "https://host/child.html" not in new_links
