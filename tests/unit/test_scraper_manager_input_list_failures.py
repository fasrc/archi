"""Tests for collect_all_from_config recording input list and sitemap failures.

Task 3.3: Design D3 "Input lists" and "Sitemaps".
"""

import pytest

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.utils.catalog_reconcile import (
    CollectionPass,
    find_uncollected,
)


@pytest.fixture
def make_manager(tmp_path, monkeypatch):
    def _factory(dm_config=None):
        monkeypatch.setattr(
            sm_module,
            "get_global_config",
            lambda: {"DATA_PATH": str(tmp_path)},
        )
        return ScraperManager(dm_config)

    return _factory


class _FakePersistence:
    def __init__(self):
        self.collection_pass = CollectionPass()


def _silence_collectors(manager, monkeypatch):
    for name in (
        "collect_links",
        "collect_sso",
        "collect_git",
        "collect_elog",
        "collect_indico",
    ):
        monkeypatch.setattr(manager, name, lambda *a, **k: None)


# ---------------------------------------------------------------------------
# Input list failure recording (D3 "Input lists")
# ---------------------------------------------------------------------------


class TestInputListFailureRecording:
    def test_missing_input_list_records_web_sso_git_when_all_enabled(
        self, make_manager, monkeypatch, tmp_path
    ):
        """A missing input list records whole-type failures for web, sso, and git."""
        (tmp_path / "weblists").mkdir()
        monkeypatch.chdir(tmp_path)

        manager = make_manager({})
        manager.input_lists = ["missing.txt"]
        manager.links_enabled = True
        manager.sso_enabled = True
        manager.git_enabled = True
        _silence_collectors(manager, monkeypatch)

        fake = _FakePersistence()
        manager.collect_all_from_config(fake)

        cp = fake.collection_pass
        assert "web" in cp.failed_types
        assert "sso" in cp.failed_types
        assert "git" in cp.failed_types

    def test_missing_input_list_with_git_disabled_records_no_git(
        self, make_manager, monkeypatch, tmp_path
    ):
        """When git_enabled=False, a missing input list does not record a git failure."""
        (tmp_path / "weblists").mkdir()
        monkeypatch.chdir(tmp_path)

        manager = make_manager({})
        manager.input_lists = ["missing.txt"]
        manager.links_enabled = True
        manager.sso_enabled = True
        manager.git_enabled = False
        _silence_collectors(manager, monkeypatch)

        fake = _FakePersistence()
        manager.collect_all_from_config(fake)

        cp = fake.collection_pass
        assert "web" in cp.failed_types
        assert "sso" in cp.failed_types
        assert "git" not in cp.failed_types

    def test_readable_input_lists_records_nothing(
        self, make_manager, monkeypatch, tmp_path
    ):
        """All input lists readable → no failure recorded in the pass."""
        weblists = tmp_path / "weblists"
        weblists.mkdir()
        (weblists / "mylist.txt").write_text("https://example.com/page\n")
        monkeypatch.chdir(tmp_path)

        manager = make_manager({})
        manager.input_lists = ["mylist.txt"]
        manager.links_enabled = True
        manager.sso_enabled = True
        manager.git_enabled = True
        _silence_collectors(manager, monkeypatch)

        fake = _FakePersistence()
        manager.collect_all_from_config(fake)

        cp = fake.collection_pass
        assert "web" not in cp.failed_types
        assert "sso" not in cp.failed_types
        assert "git" not in cp.failed_types


# ---------------------------------------------------------------------------
# Sitemap failure recording (D3 "Sitemaps")
# ---------------------------------------------------------------------------


class TestSitemapFailureRecording:
    def test_sitemap_failure_records_top_level_url_and_allowed_host(
        self, make_manager, monkeypatch
    ):
        """Sitemap expansion failure records failures for top-level sitemap host and allowed_hosts."""
        manager = make_manager(
            {"sources": {"links": {"sitemap": {"allowed_hosts": "b.example.org"}}}}
        )

        monkeypatch.setattr(
            manager,
            "_collect_urls_from_lists_by_type",
            lambda _lists: ([], [], [], [], [], ["https://a.example.org/sitemap.xml"]),
        )

        def fake_expand(urls):
            manager._sitemap_expansion_failures = [
                ("https://b.example.org/child.xml", Exception("fetch failed"))
            ]
            manager._sitemap_expansion_incomplete = True
            return []

        monkeypatch.setattr(manager, "_expand_sitemaps", fake_expand)
        _silence_collectors(manager, monkeypatch)

        fake = _FakePersistence()
        manager.collect_all_from_config(fake)

        cp = fake.collection_pass
        assert ("web", "a.example.org") in cp.failed
        assert ("web", "b.example.org") in cp.failed

    def test_sitemap_failure_skips_both_hosts_in_find_uncollected(
        self, make_manager, monkeypatch
    ):
        """Rows on both failed sitemap hosts yield no candidates after a sitemap failure."""
        manager = make_manager(
            {"sources": {"links": {"sitemap": {"allowed_hosts": "b.example.org"}}}}
        )

        monkeypatch.setattr(
            manager,
            "_collect_urls_from_lists_by_type",
            lambda _lists: ([], [], [], [], [], ["https://a.example.org/sitemap.xml"]),
        )

        def fake_expand(urls):
            manager._sitemap_expansion_failures = [
                ("https://b.example.org/child.xml", Exception("fetch failed"))
            ]
            manager._sitemap_expansion_incomplete = True
            return []

        monkeypatch.setattr(manager, "_expand_sitemaps", fake_expand)
        _silence_collectors(manager, monkeypatch)

        fake = _FakePersistence()
        # One page collected on each host, so the "collected 0" rule cannot hide
        # a missing failure record.
        for resource_hash, url in (
            ("a0", "https://a.example.org/kept"),
            ("b0", "https://b.example.org/kept"),
        ):
            fake.collection_pass.record_collected(
                resource_hash, {"source_type": "web", "url": url}
            )
        manager.collect_all_from_config(fake)

        rows = [
            ("a0", {"source_type": "web", "url": "https://a.example.org/kept"}),
            ("b0", {"source_type": "web", "url": "https://b.example.org/kept"}),
            ("r1", {"source_type": "web", "url": "https://a.example.org/page"}),
            ("r2", {"source_type": "web", "url": "https://b.example.org/page"}),
        ]
        report = find_uncollected(rows, fake.collection_pass)
        assert report.candidates == []
        assert sorted((scope, reason) for scope, reason, _count in report.skipped) == [
            (("web", "a.example.org"), "sitemap expansion failed"),
            (("web", "b.example.org"), "sitemap expansion failed"),
        ]

    def test_no_sitemap_failure_records_nothing(self, make_manager, monkeypatch):
        """No sitemap expansion failures → no scope failure recorded in the pass."""
        manager = make_manager(
            {"sources": {"links": {"sitemap": {"allowed_hosts": "b.example.org"}}}}
        )

        monkeypatch.setattr(
            manager,
            "_collect_urls_from_lists_by_type",
            lambda _lists: ([], [], [], [], [], ["https://a.example.org/sitemap.xml"]),
        )

        def fake_expand_ok(urls):
            manager._sitemap_expansion_failures = []
            manager._sitemap_expansion_incomplete = False
            return [("https://a.example.org/page1", None)]

        monkeypatch.setattr(manager, "_expand_sitemaps", fake_expand_ok)
        _silence_collectors(manager, monkeypatch)

        fake = _FakePersistence()
        manager.collect_all_from_config(fake)

        cp = fake.collection_pass
        assert not cp.failed
        assert not cp.failed_types
