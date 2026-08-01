"""A cross-host redirect must move the crawl onto the destination's slot.

The per-host cap is taken on the *seed* URL's host, but ``requests`` follows
redirects and ``LinkScraper.reap`` resolves every subsequent link against the
response's final URL. A seed on ``a.example`` that redirects to ``dest.example``
therefore spends its whole crawl requesting ``dest.example`` while holding
``a.example``'s slot. Two such seeds on different origin hosts take two different
semaphores and hammer one destination at up to ``scrape_workers`` concurrency
instead of ``scrape_per_host_workers``.

This drives the real path — ``_collect_links_from_urls`` -> ``run_seeds`` ->
per-worker ``LinkScraper.crawl_iter`` -> the limiter — with an injected fetch
whose responses report a redirected final URL, and gates on the observable
outcome: with ``scrape_per_host_workers`` of 1, no two crawls are ever inside a
``dest.example`` request at the same time.
"""

import threading

import pytest
import requests

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scrape_pool import reset_shared_host_limiters
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager

SEEDS = ["https://a.example/", "https://b.example/"]

# requested URL -> (final URL after redirects, HTML body)
_ROUTES = {
    "https://a.example/": (
        "https://dest.example/a",
        '<html><body><a href="https://dest.example/a1">a1</a></body></html>',
    ),
    "https://b.example/": (
        "https://dest.example/b",
        '<html><body><a href="https://dest.example/b1">b1</a></body></html>',
    ),
    "https://dest.example/a1": (
        "https://dest.example/a1",
        "<html><body>leaf</body></html>",
    ),
    "https://dest.example/b1": (
        "https://dest.example/b1",
        "<html><body>leaf</body></html>",
    ),
}


@pytest.fixture(autouse=True)
def _isolate_shared_limiters():
    reset_shared_host_limiters()
    yield
    reset_shared_host_limiters()


class _FakeResponse:
    def __init__(self, final_url, html):
        self.url = final_url
        self.text = html
        self.content = html
        self.encoding = "utf-8"
        self.headers = {"Content-type": "text/html"}

    def raise_for_status(self):
        return None


class _RecordingPersistence:
    def __init__(self):
        self._lock = threading.Lock()
        self.urls = []

    def persist_resource(self, resource, output_dir):
        with self._lock:
            self.urls.append(resource.url)


class _DestinationGate:
    """Blocks the first request that lands on ``dest.example``.

    A second concurrent request to that host sets ``entered_second``, which is
    exactly the cap violation under test.
    """

    def __init__(self):
        self.entered_first = threading.Event()
        self.entered_second = threading.Event()
        self.release = threading.Event()
        self.hits = []
        self._lock = threading.Lock()

    def fetch(self, url):
        final_url, html = _ROUTES[url]
        if url.startswith("https://dest.example/"):
            with self._lock:
                is_first = not self.hits
                self.hits.append(url)
            if is_first:
                self.entered_first.set()
                assert self.release.wait(10)
            else:
                self.entered_second.set()
        return _FakeResponse(final_url, html)


def test_two_seeds_redirecting_to_one_host_respect_its_cap(tmp_path, monkeypatch):
    gate = _DestinationGate()
    monkeypatch.setattr(
        requests.Session,
        "get",
        lambda self, url, *a, **kw: gate.fetch(url),
        raising=True,
    )
    monkeypatch.setattr(
        sm_module, "get_global_config", lambda: {"DATA_PATH": str(tmp_path)}
    )

    manager = ScraperManager({"scrape_workers": 4, "scrape_per_host_workers": 1})
    persistence = _RecordingPersistence()
    totals = []
    runner = threading.Thread(
        target=lambda: totals.append(
            manager._collect_links_from_urls(list(SEEDS), persistence, tmp_path)
        )
    )
    runner.start()

    assert gate.entered_first.wait(10), "no crawl reached dest.example"
    assert not gate.entered_second.wait(0.5), (
        "a second crawl requested dest.example while another held it — the "
        "per-host slot is still keyed on the pre-redirect seed host"
    )

    gate.release.set()
    runner.join(10)
    assert not runner.is_alive()

    # Both crawls still complete: seed page + one child each.
    assert totals == [4]
    assert sorted(persistence.urls) == sorted(
        [
            "https://a.example/",
            "https://b.example/",
            "https://dest.example/a1",
            "https://dest.example/b1",
        ]
    )
