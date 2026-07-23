"""Unit tests for ScraperManager's scrape-concurrency config knobs (issue #136).

Covers the ``data_manager.scrape_workers`` / ``data_manager.scrape_per_host_workers``
parsing added to ``ScraperManager.__init__``: defaults when unset, tolerant fallback
with a warning on junk values, clamping to a minimum of 1, and independence from the
embedding-phase ``parallel_workers`` knob (no cross-talk).
"""

import logging

import pytest

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager


@pytest.fixture
def make_manager(tmp_path, monkeypatch):
    """Build a ScraperManager with a stubbed global config (DATA_PATH -> tmp_path)."""

    def _factory(dm_config):
        monkeypatch.setattr(
            sm_module,
            "get_global_config",
            lambda: {"DATA_PATH": str(tmp_path)},
        )
        return ScraperManager(dm_config)

    return _factory


class TestScrapeWorkerDefaults:
    def test_defaults_when_both_unset(self, make_manager):
        manager = make_manager({})
        assert manager.scrape_workers == 8
        assert manager.scrape_per_host_workers == 4

    def test_defaults_when_dm_config_is_none(self, make_manager):
        manager = make_manager(None)
        assert manager.scrape_workers == 8
        assert manager.scrape_per_host_workers == 4

    def test_explicit_values_are_honored(self, make_manager):
        manager = make_manager({"scrape_workers": 12, "scrape_per_host_workers": 3})
        assert manager.scrape_workers == 12
        assert manager.scrape_per_host_workers == 3

    def test_string_integers_are_coerced(self, make_manager):
        manager = make_manager({"scrape_workers": "6", "scrape_per_host_workers": "2"})
        assert manager.scrape_workers == 6
        assert manager.scrape_per_host_workers == 2


class TestScrapeWorkerTolerantParse:
    def test_invalid_scrape_workers_falls_back_and_warns(self, make_manager, caplog):
        with caplog.at_level(logging.WARNING):
            manager = make_manager({"scrape_workers": "many"})
        assert manager.scrape_workers == 8
        assert "scrape_workers" in caplog.text

    def test_invalid_per_host_workers_falls_back_and_warns(self, make_manager, caplog):
        with caplog.at_level(logging.WARNING):
            manager = make_manager({"scrape_per_host_workers": ["junk"]})
        assert manager.scrape_per_host_workers == 4
        assert "scrape_per_host_workers" in caplog.text

    def test_zero_clamps_to_one(self, make_manager):
        manager = make_manager({"scrape_workers": 0, "scrape_per_host_workers": 0})
        assert manager.scrape_workers == 1
        assert manager.scrape_per_host_workers == 1

    def test_negative_clamps_to_one(self, make_manager):
        manager = make_manager({"scrape_workers": -5, "scrape_per_host_workers": -1})
        assert manager.scrape_workers == 1
        assert manager.scrape_per_host_workers == 1


class TestNoKnobCrossTalk:
    def test_scrape_workers_does_not_touch_parallel_workers(self, make_manager):
        # The embedding-phase knob lives on VectorStoreManager, not here. Setting the
        # scrape knob must not create or consume a parallel_workers value on the scraper.
        manager = make_manager({"scrape_workers": 5, "parallel_workers": 32})
        assert manager.scrape_workers == 5
        assert getattr(manager, "parallel_workers", None) is None

    def test_parallel_workers_does_not_set_scrape_workers(self, make_manager):
        # An embedding-only config leaves the scrape knob at its default, unaffected.
        manager = make_manager({"parallel_workers": 16})
        assert manager.scrape_workers == 8
