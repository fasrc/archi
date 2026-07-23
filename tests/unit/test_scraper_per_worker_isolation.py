"""Per-worker crawler isolation for the parallel scrape phase (issue #136).

``ScraperManager`` holds a single shared ``self.web_scraper``
(``scraper_manager.py:100``). ``LinkScraper.crawl_iter`` resets its per-instance
``visited_urls`` / ``seen_urls`` / ``page_data`` at the top of every call
(``scraper.py:194-196``). Handing that one shared instance to concurrent seed
crawls turns the per-seed reset into the *cause* of a race: one seed entering
``crawl_iter`` wipes another in-flight seed's frontier, so pages get re-visited,
dropped, or attributed to the wrong crawl.

The fix (task 4.3) is a scraper-factory seam on the manager that returns a fresh
``LinkScraper`` per seed crawl. This test exercises that seam: two crawls run
concurrently through their own factory-built scrapers, forced to overlap on every
fetch via a barrier, and each must yield exactly its own URLs with no
cross-contamination of crawler state.

Written before the seam exists, so it fails today (``_new_link_scraper`` is
absent) and turns green once task 4.3 lands.
"""

import threading

import pytest
import requests

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager

# Two independent single-host sites: each seed links to one child page that has
# no further links, so each crawl performs exactly two fetches (seed + child).
HOST_A_SEED = "https://a.example.edu/"
HOST_A_CHILD = "https://a.example.edu/p1"
HOST_B_SEED = "https://b.example.edu/"
HOST_B_CHILD = "https://b.example.edu/p1"

_PAGES = {
    HOST_A_SEED: f'<html><body><a href="{HOST_A_CHILD}">a1</a></body></html>',
    HOST_A_CHILD: "<html><body>no links here</body></html>",
    HOST_B_SEED: f'<html><body><a href="{HOST_B_CHILD}">b1</a></body></html>',
    HOST_B_CHILD: "<html><body>no links here</body></html>",
}

HOST_A_URLS = {HOST_A_SEED, HOST_A_CHILD}
HOST_B_URLS = {HOST_B_SEED, HOST_B_CHILD}


class _FakeResponse:
    def __init__(self, url, html):
        self.url = url
        self.text = html
        self.content = html
        self.encoding = "utf-8"
        self.headers = {"Content-type": "text/html"}

    def raise_for_status(self):
        return None


@pytest.fixture
def make_manager(tmp_path, monkeypatch):
    """Build a ScraperManager with a stubbed global config (DATA_PATH -> tmp_path)."""

    def _factory(dm_config=None):
        monkeypatch.setattr(
            sm_module,
            "get_global_config",
            lambda: {"DATA_PATH": str(tmp_path)},
        )
        return ScraperManager(dm_config)

    return _factory


def _install_fake_fetch(monkeypatch, barrier):
    """Patch ``requests.Session.get`` to serve ``_PAGES`` and rendezvous on ``barrier``.

    The barrier forces both concurrent crawls to sit inside a fetch at the same
    time, so if the two crawls shared one ``LinkScraper`` their interleaved
    ``crawl_iter`` state resets would corrupt each other's yielded URLs.
    """

    def fake_get(self, url, *args, **kwargs):
        barrier.wait(timeout=5)
        return _FakeResponse(url, _PAGES[url])

    monkeypatch.setattr(requests.Session, "get", fake_get, raising=True)


class TestPerWorkerCrawlerIsolation:
    def test_concurrent_crawls_yield_only_their_own_urls(
        self, make_manager, monkeypatch
    ):
        manager = make_manager()

        # Each concurrent crawl must get its OWN scraper from the factory seam.
        scraper_a = manager._new_link_scraper()
        scraper_b = manager._new_link_scraper()
        assert scraper_a is not scraper_b
        assert scraper_a is not manager.web_scraper
        assert scraper_b is not manager.web_scraper

        # barrier(2): both crawls overlap on every fetch (2 fetches each).
        _install_fake_fetch(monkeypatch, threading.Barrier(2, timeout=5))

        results = {}
        errors = {}

        def run(name, scraper, seed):
            try:
                results[name] = [
                    resource.url for resource in scraper.crawl_iter(seed, max_depth=5)
                ]
            except BaseException as exc:  # surface barrier timeouts as failures
                errors[name] = exc

        threads = [
            threading.Thread(target=run, args=("a", scraper_a, HOST_A_SEED)),
            threading.Thread(target=run, args=("b", scraper_b, HOST_B_SEED)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"crawl raised under concurrency: {errors}"

        # Each crawl yields exactly its own host's URLs, no more and no fewer.
        assert set(results["a"]) == HOST_A_URLS
        assert set(results["b"]) == HOST_B_URLS
        # No duplication within a crawl (frontier dedup survived the overlap).
        assert len(results["a"]) == len(HOST_A_URLS)
        assert len(results["b"]) == len(HOST_B_URLS)

        # Neither scraper's per-instance state observed the other's reset/URLs.
        assert not (scraper_a.seen_urls & HOST_B_URLS)
        assert not (scraper_b.seen_urls & HOST_A_URLS)
        assert not (scraper_a.visited_urls & HOST_B_URLS)
        assert not (scraper_b.visited_urls & HOST_A_URLS)


class TestPerWorkerScraperInheritsConfig:
    """Each factory-built scraper mirrors the manager's html_scraper config."""

    def _dm_config(self, verify_urls, enable_warnings):
        return {
            "sources": {
                "links": {
                    "html_scraper": {
                        "verify_urls": verify_urls,
                        "enable_warnings": enable_warnings,
                    }
                }
            }
        }

    def test_scraper_inherits_configured_flags(self, make_manager):
        manager = make_manager(self._dm_config(verify_urls=True, enable_warnings=True))
        scraper = manager._new_link_scraper()
        assert scraper.verify_urls is True
        assert scraper.enable_warnings is True

    def test_scraper_defaults_match_shared_scraper(self, make_manager):
        # Unset html_scraper flags default to False, and the per-worker scraper
        # is constructed with the same values as the shared sequential one.
        manager = make_manager({})
        scraper = manager._new_link_scraper()
        assert scraper.verify_urls is False
        assert scraper.enable_warnings is False
        assert scraper.verify_urls == manager.web_scraper.verify_urls
        assert scraper.enable_warnings == manager.web_scraper.enable_warnings
