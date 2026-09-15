"""Determinism of the parallel link path vs. the sequential path (issue #136, task 7.1).

The parallel scrape phase MUST produce an identical corpus regardless of the
``scrape_workers`` setting: for the same seed inputs, the *set* of persisted
resource URLs and the returned total count have to match between a sequential
run (``scrape_workers: 1``) and a parallel run (``scrape_workers: 8``). Nothing
may be dropped or duplicated just because the pool fanned the seeds out across
threads.

This exercises the real code path end to end — ``_collect_links_from_urls`` ->
``run_seeds`` -> per-worker ``LinkScraper.crawl_iter`` -> persistence — with an
injected in-memory fetch, so the assertion is about the observable persisted
result, not about internal wiring.

Fixture seeds each live on their own host and expand to two leaf children, so a
seed's page set is disjoint from every other seed's. That keeps the expected
corpus unambiguous (``LinkScraper`` does not dedup across seeds), which is
exactly what lets the workers=1 and workers=8 runs be compared for equality.
"""

import threading

import pytest
import requests

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager

# Six independent single-host sites. Each seed links to two leaf children with no
# further links, so each crawl performs exactly three fetches (seed + two
# children) and the full expected corpus is 18 distinct URLs.
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

    The parallel path persists from worker threads, so appends are guarded by a
    lock; the test compares the recorded URL sets, not their order.
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


class TestParallelAndSequentialRunsAgree:
    def test_persisted_url_set_and_total_are_identical(
        self, make_manager, monkeypatch, tmp_path
    ):
        _install_fake_fetch(monkeypatch)

        seq_manager = make_manager({"scrape_workers": 1})
        seq_persistence = _RecordingPersistence()
        seq_total = seq_manager._collect_links_from_urls(
            list(SEEDS), seq_persistence, tmp_path
        )

        par_manager = make_manager({"scrape_workers": 8})
        par_persistence = _RecordingPersistence()
        par_total = par_manager._collect_links_from_urls(
            list(SEEDS), par_persistence, tmp_path
        )

        # The set of persisted resource URLs is identical between the two runs...
        assert set(seq_persistence.urls) == set(par_persistence.urls)
        # ...and equals the full expected corpus, so neither run silently dropped a page.
        assert set(par_persistence.urls) == EXPECTED_URLS
        # Both runs return the same total, which is the exact page count.
        assert seq_total == par_total == len(EXPECTED_URLS)
