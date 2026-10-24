"""Standard link path dispatches through the bounded seed pool (issue #136, task 5.1).

``ScraperManager._collect_links_from_urls`` must fan the standard (non-selenium)
seeds through ``run_seeds`` sized by the manager's configured ``scrape_workers``
and ``scrape_per_host_workers`` knobs, instead of the old one-seed-at-a-time
``for url in urls`` loop (``scraper_manager.py:346``).

Written before task 5.2 wires the pool, so it fails today: the module still runs
the sequential loop and never imports or calls ``run_seeds``, leaving the spy
untouched.
"""

import pytest

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scrape_pool import (
    reset_shared_host_limiters,
    shared_host_limiter,
)
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager


@pytest.fixture(autouse=True)
def _isolate_shared_limiters():
    reset_shared_host_limiters()
    yield
    reset_shared_host_limiters()


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


def _install_run_seeds_spy(monkeypatch, recorded, total):
    """Replace the module-level ``run_seeds`` with a recording spy.

    ``raising=False`` because the symbol only appears in ``scraper_manager`` once
    task 5.2 imports it; today the attribute is absent and the spy is simply never
    invoked, which is what makes this test fail.
    """

    def spy_run_seeds(seeds, scrape_one, workers, per_host_workers, limiter=None):
        recorded["seeds"] = list(seeds)
        recorded["scrape_one"] = scrape_one
        recorded["workers"] = workers
        recorded["per_host_workers"] = per_host_workers
        recorded["limiter"] = limiter
        return total

    monkeypatch.setattr(sm_module, "run_seeds", spy_run_seeds, raising=False)


class TestStandardPathDispatchesThroughPool:
    def test_dispatch_uses_configured_worker_and_per_host_values(
        self, make_manager, monkeypatch, tmp_path
    ):
        manager = make_manager({"scrape_workers": 5, "scrape_per_host_workers": 3})
        # Guard the legacy sequential loop: if the old path still runs today it must
        # not make real network requests, so stub the per-seed handler to a no-op.
        monkeypatch.setattr(manager, "_handle_standard_url", lambda *a, **k: 0)

        recorded = {}
        sentinel_total = 42
        _install_run_seeds_spy(monkeypatch, recorded, sentinel_total)

        urls = ["https://a.example.edu/", "https://b.example.edu/"]
        total = manager._collect_links_from_urls(
            urls, persistence=object(), output_dir=tmp_path
        )

        assert recorded, "standard link path did not dispatch through run_seeds"
        assert recorded["seeds"] == urls
        assert recorded["workers"] == 5
        assert recorded["per_host_workers"] == 3
        assert callable(recorded["scrape_one"])
        # The pool is handed the PROCESS-WIDE limiter for that cap, not a fresh one,
        # so a concurrent upload_url batch contends with this ingest instead of
        # spending the per-host budget a second time.
        assert recorded["limiter"] is shared_host_limiter(3)
        # The method returns the pool's summed total verbatim.
        assert total == sentinel_total

    def test_dispatch_uses_default_knobs_when_unset(
        self, make_manager, monkeypatch, tmp_path
    ):
        # No knobs configured: the pool must receive the resolved defaults (8/4),
        # proving the values flow from config rather than being hardcoded.
        manager = make_manager({})
        monkeypatch.setattr(manager, "_handle_standard_url", lambda *a, **k: 0)

        recorded = {}
        _install_run_seeds_spy(monkeypatch, recorded, 0)

        manager._collect_links_from_urls(
            ["https://docs.example.edu/"], persistence=object(), output_dir=tmp_path
        )

        assert recorded, "standard link path did not dispatch through run_seeds"
        assert recorded["workers"] == 8
        assert recorded["per_host_workers"] == 4


class TestCollectUrlsFromListsByType:
    """_collect_urls_from_lists_by_type routes entries by prefix then heuristic."""

    def test_all_six_entry_types_dispatched_correctly(
        self, make_manager, tmp_path, monkeypatch
    ):
        weblists = tmp_path / "weblists"
        weblists.mkdir()
        (weblists / "mixed.list").write_text(
            "git-https://x/r.git\n"
            "sso-https://x/s\n"
            "sitemap-https://x/sm.xml\n"
            "elog-https://h/elog/logbook\n"
            "https://h/elog/x\n"
            "indico-https://indico.h/event/1\n"
            "https://example.com/page\n"
        )
        manager = make_manager({})
        monkeypatch.chdir(tmp_path)
        link_urls, git_urls, sso_urls, elog_urls, indico_urls, sitemap_urls = (
            manager._collect_urls_from_lists_by_type(["mixed.list"])
        )
        assert git_urls == ["https://x/r.git"]
        assert sso_urls == ["https://x/s"]
        assert sitemap_urls == ["https://x/sm.xml"]
        assert elog_urls == ["https://h/elog/logbook", "https://h/elog/x"]
        assert indico_urls == ["https://indico.h/event/1"]
        assert link_urls == ["https://example.com/page"]

    def test_indico_prefix_beats_elog_path_heuristic(
        self, make_manager, tmp_path, monkeypatch
    ):
        """Explicit indico- prefix routes to indico even when the path contains /elog/."""
        weblists = tmp_path / "weblists"
        weblists.mkdir()
        (weblists / "mixed.list").write_text("indico-https://indico.h/elog/x\n")
        manager = make_manager({})
        monkeypatch.chdir(tmp_path)
        link_urls, git_urls, sso_urls, elog_urls, indico_urls, sitemap_urls = (
            manager._collect_urls_from_lists_by_type(["mixed.list"])
        )
        assert indico_urls == ["https://indico.h/elog/x"]
        assert elog_urls == []
