"""No resource is dropped or duplicated under concurrency (issue #136, task 7.2).

Task 7.1 (``test_scraper_determinism.py``) asserts that the *set* of persisted
resource URLs matches between a sequential and a parallel run. That guards
against a URL being dropped or a spurious URL appearing, but a set comparison is
blind to *multiplicity*: if a worker persisted the same page twice, the set
would still be equal.

This test closes that gap by asserting the persisted URL **multiset** under
concurrency has no unexpected repeats — every expected page is persisted exactly
once, and the returned total equals the number of persisted resources. A double
count from a race between workers (or a leaked, shared crawler visiting a page
twice) would show up here as a URL with a multiplicity greater than one, even
though the set of URLs stayed correct.

Like the determinism test, this drives the real path
(``_collect_links_from_urls`` -> ``run_seeds`` -> per-worker
``LinkScraper.crawl_iter`` -> persistence) with an injected in-memory fetch, so
the assertion is about the observable persisted corpus, not internal wiring.
"""

import threading
from collections import Counter

import pytest
import requests

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager

# Independent single-host sites. Each seed links to two leaf children with no
# further links, so each crawl performs exactly three fetches (seed + two
# children) and every page across the fixture is distinct. With disjoint pages,
# the correct persisted corpus contains each URL exactly once.
_HOSTS = [f"h{i}.example.edu" for i in range(6)]
SEEDS = [f"https://{host}/" for host in _HOSTS]

_PAGES: dict[str, str] = {}
EXPECTED_URLS: set[str] = set()
for _host in _HOSTS:
    _seed = f"https://{_host}/"
    _child1 = f"https://{_host}/p1"
    _child2 = f"https://{_host}/p2"
    _PAGES[_seed] = (
        f'<html><body><a href="{_child1}">1</a>'
        f'<a href="{_child2}">2</a></body></html>'
    )
    _PAGES[_child1] = "<html><body>leaf</body></html>"
    _PAGES[_child2] = "<html><body>leaf</body></html>"
    EXPECTED_URLS.update({_seed, _child1, _child2})


class _FakeResponse:
    def __init__(self, url, html):
        self.url = url
        self.text = html
        self.content = html
        self.encoding = "utf-8"
        self.headers = {"Content-type": "text/html"}

    def raise_for_status(self):
        return None


class _RecordingPersistence:
    """Thread-safe stand-in that records the URL of every persisted resource.

    Appends are guarded by a lock because the parallel path persists from worker
    threads; the test compares URL multiplicities, so every append must be
    recorded without loss.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.urls: list[str] = []

    def persist_resource(self, resource, output_dir):
        with self._lock:
            self.urls.append(resource.url)


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


def _install_fake_fetch(monkeypatch):
    """Serve ``_PAGES`` for every crawl fetch, in place of real network I/O."""

    def fake_get(self, url, *args, **kwargs):
        return _FakeResponse(url, _PAGES[url])

    monkeypatch.setattr(requests.Session, "get", fake_get, raising=True)


class TestNoResourceDroppedOrDuplicated:
    def test_persisted_url_multiset_has_no_unexpected_repeats(
        self, make_manager, monkeypatch, tmp_path
    ):
        _install_fake_fetch(monkeypatch)

        manager = make_manager({"scrape_workers": 8, "scrape_per_host_workers": 4})
        persistence = _RecordingPersistence()
        total = manager._collect_links_from_urls(list(SEEDS), persistence, tmp_path)

        counts = Counter(persistence.urls)

        # Every expected page is present...
        assert set(counts) == EXPECTED_URLS
        # ...exactly once — no URL was persisted more than once by a racing worker
        # or a leaked crawler revisiting a page.
        duplicates = {url: n for url, n in counts.items() if n != 1}
        assert not duplicates, f"URLs persisted more than once: {duplicates}"

        # Nothing was dropped: one persisted resource per expected page, and the
        # returned total matches the number of persisted resources exactly.
        assert len(persistence.urls) == len(EXPECTED_URLS)
        assert total == len(persistence.urls)
