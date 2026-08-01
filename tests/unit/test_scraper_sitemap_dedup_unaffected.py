"""Pre-loop sitemap dedup is unaffected by the parallel pool (issue #136, task 7.3).

The sitemap expansion in ``collect_all_from_config`` deduplicates expanded page
URLs against the NORMALIZED hand-list keys *before* handing the merged list to
the link-collection path (``scraper_manager.py`` sitemap block). Wiring the
bounded seed pool into the standard link path must not change that: the pool has
to receive an already-deduped list, and the pool itself must NOT re-dedup (it
scrapes exactly the seeds it is given, in order, duplicates and all).

Two guarantees are pinned here:

1. The list that reaches ``collect_links`` (and therefore ``run_seeds``) is the
   deduped merge of hand-list + expanded sitemap pages — a slash-variant that
   normalizes onto a hand-list entry is dropped once, before the pool.
2. ``_collect_links_from_urls`` forwards its ``urls`` to ``run_seeds`` verbatim,
   so the pool contributes no dedup of its own.
"""

import pytest

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager


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


class TestSitemapDedupHappensBeforeThePool:
    def test_pool_receives_already_deduped_list(self, make_manager, monkeypatch):
        """The sitemap merge dedups against normalized hand-list keys up front, so
        ``collect_links`` (the pool's entry point) gets one copy of the shared page."""
        manager = make_manager({})

        # Hand-listed with a trailing slash; the sitemap emits the normalized,
        # slash-less form of the same page plus one genuinely new page.
        hand_list = ["https://x.example.edu/a/"]
        sitemap_marker = ["sitemap-https://x.example.edu/sitemap.xml"]
        expanded = ["https://x.example.edu/a", "https://x.example.edu/b"]

        monkeypatch.setattr(
            manager,
            "_collect_urls_from_lists_by_type",
            lambda _lists: (list(hand_list), [], [], [], [], list(sitemap_marker)),
        )
        monkeypatch.setattr(
            manager, "_expand_sitemaps", lambda _sitemap_urls: list(expanded)
        )
        # Silence the other collectors so only the link path is observed.
        monkeypatch.setattr(manager, "collect_sso", lambda *a, **k: None)
        monkeypatch.setattr(manager, "collect_git", lambda *a, **k: None)
        monkeypatch.setattr(manager, "collect_elog", lambda *a, **k: 0)
        monkeypatch.setattr(manager, "collect_indico", lambda *a, **k: None)

        captured = {}

        def spy_collect_links(persistence, link_urls=None, max_depth=None):
            captured["link_urls"] = list(link_urls)
            return 0

        monkeypatch.setattr(manager, "collect_links", spy_collect_links)

        manager.collect_all_from_config(persistence=object())

        # The slash-variant "/a" collapses onto the hand-listed "/a/" exactly once,
        # and "/b" is appended: the pool never sees the duplicate page.
        assert captured["link_urls"] == [
            "https://x.example.edu/a/",
            "https://x.example.edu/b",
        ]


class TestPoolDoesNotReDedup:
    def test_collect_links_forwards_urls_to_run_seeds_verbatim(
        self, make_manager, monkeypatch, tmp_path
    ):
        """A duplicate that slips into the seed list is passed through untouched:
        the pool scrapes what it is given and adds no dedup of its own."""
        manager = make_manager({"scrape_workers": 4, "scrape_per_host_workers": 2})

        recorded = {}

        def spy_run_seeds(seeds, scrape_one, workers, per_host_workers, limiter=None):
            recorded["seeds"] = list(seeds)
            return 0

        monkeypatch.setattr(sm_module, "run_seeds", spy_run_seeds, raising=True)

        urls = [
            "https://x.example.edu/a",
            "https://x.example.edu/b",
            "https://x.example.edu/a",  # intentional duplicate
        ]
        manager._collect_links_from_urls(
            list(urls), persistence=object(), output_dir=tmp_path
        )

        assert recorded["seeds"] == urls
