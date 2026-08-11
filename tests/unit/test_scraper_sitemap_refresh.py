"""Tests for the sitemap lastmod map refresh contract (issue #181).

Task 1.1: baseline fixture that reproduces today's correct behavior.

The ``refresh_harness`` fixture builds a ``ScraperManager`` with all network/IO
patched so every assertion is deterministic:
- ``_collect_urls_from_lists_by_type`` returns controlled lists (no file I/O)
- ``_expand_sitemaps`` returns controlled ``(url, lastmod|None)`` pairs (no HTTP)
- ``persistence.catalog.get_metadata_by_filter`` returns controlled catalog URLs
- ``collect_links`` / ``collect_sso`` / ``collect_git`` / ``collect_elog`` /
  ``collect_indico`` are silenced so only map-building behavior is observed
"""

import pytest

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.scrapers.sitemap_source import SitemapExpansionError

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def make_manager(tmp_path, monkeypatch):
    """ScraperManager factory with a stubbed global config (DATA_PATH → tmp_path)."""

    def _factory(dm_config=None):
        monkeypatch.setattr(
            sm_module,
            "get_global_config",
            lambda: {"DATA_PATH": str(tmp_path)},
        )
        return ScraperManager(dm_config)

    return _factory


@pytest.fixture
def refresh_harness(make_manager, monkeypatch):
    """Full harness: controllable expand/catalog state, silenced collectors.

    Scenario:
    - hand_list: one page with a trailing slash (``/hand/``)
    - sitemap: two distinct pages (``/a``, ``/b``) with lastmod dates
    - catalog: same two pages (as if a prior ingest stored them)

    Returns a dict with:
    - ``manager``        – the ScraperManager instance
    - ``set_expand_pairs`` – callable(list[(url, lastmod|None)]) to swap what
      ``_expand_sitemaps`` returns on the *next* call
    - ``persistence``    – minimal fake that satisfies both ``collect_all_from_config``
      and ``schedule_collect_links``
    - ``initial_pairs``  – the (url, lastmod) list used for the first expansion
    """
    hand_list = ["https://x.example.edu/hand/"]
    sitemap_marker = ["sitemap-https://x.example.edu/sitemap.xml"]
    initial_pairs = [
        ("https://x.example.edu/a", "2024-01-01"),
        ("https://x.example.edu/b", "2024-02-01"),
    ]
    catalog_pages = ["https://x.example.edu/a", "https://x.example.edu/b"]

    manager = make_manager({})
    state = {"expand_pairs": list(initial_pairs)}

    monkeypatch.setattr(
        manager,
        "_collect_urls_from_lists_by_type",
        lambda _lists: (list(hand_list), [], [], [], [], list(sitemap_marker)),
    )
    monkeypatch.setattr(
        manager,
        "_expand_sitemaps",
        lambda _sitemap_urls: list(state["expand_pairs"]),
    )

    class FakeCatalog:
        def get_metadata_by_filter(self, key, source_type=None, metadata_keys=None):
            return [("id", {"url": u}) for u in catalog_pages]

    class FakePersistence:
        catalog = FakeCatalog()

    monkeypatch.setattr(manager, "collect_links", lambda *a, **k: 0)
    monkeypatch.setattr(manager, "collect_sso", lambda *a, **k: None)
    monkeypatch.setattr(manager, "collect_git", lambda *a, **k: None)
    monkeypatch.setattr(manager, "collect_elog", lambda *a, **k: 0)
    monkeypatch.setattr(manager, "collect_indico", lambda *a, **k: None)

    def set_expand_pairs(pairs):
        state["expand_pairs"] = list(pairs)

    return {
        "manager": manager,
        "set_expand_pairs": set_expand_pairs,
        "persistence": FakePersistence(),
        "initial_pairs": initial_pairs,
    }


# ---------------------------------------------------------------------------
# Task 1.1 — baseline: fixture reproduces today's (correct) behavior
# ---------------------------------------------------------------------------


class TestInitialMapPopulation:
    """After ``collect_all_from_config``, ``_sitemap_lastmod_map`` holds the
    sitemap-derived lastmod values for every page not also hand-listed.
    """

    def test_sitemap_only_pages_are_in_map(self, refresh_harness):
        h = refresh_harness
        h["manager"].collect_all_from_config(h["persistence"])

        assert h["manager"]._sitemap_lastmod_map == {
            "https://x.example.edu/a": "2024-01-01",
            "https://x.example.edu/b": "2024-02-01",
        }

    def test_hand_listed_page_absent_from_map(self, refresh_harness, monkeypatch):
        """A page in both the hand-list and the sitemap must not enter the map
        (its ``last_modified`` must stay NULL per the spec).
        """
        h = refresh_harness
        # The hand-list has "/hand/" (trailing slash); the sitemap emits the
        # normalized "/hand" — they must collide via normalize_page_url.
        monkeypatch.setattr(
            h["manager"],
            "_expand_sitemaps",
            lambda _: [
                ("https://x.example.edu/hand", "2024-03-01"),
                ("https://x.example.edu/a", "2024-01-01"),
            ],
        )
        h["manager"].collect_all_from_config(h["persistence"])

        assert "https://x.example.edu/hand" not in h["manager"]._sitemap_lastmod_map
        assert h["manager"]._sitemap_lastmod_map == {
            "https://x.example.edu/a": "2024-01-01",
        }

    def test_page_without_lastmod_not_in_map(self, refresh_harness, monkeypatch):
        """A sitemap pair whose lastmod is None contributes the URL to the crawl
        set but leaves the map entry absent (nothing to stamp).
        """
        h = refresh_harness
        monkeypatch.setattr(
            h["manager"],
            "_expand_sitemaps",
            lambda _: [
                ("https://x.example.edu/a", None),
                ("https://x.example.edu/b", "2024-02-01"),
            ],
        )
        h["manager"].collect_all_from_config(h["persistence"])

        assert "https://x.example.edu/a" not in h["manager"]._sitemap_lastmod_map
        assert h["manager"]._sitemap_lastmod_map == {
            "https://x.example.edu/b": "2024-02-01",
        }


# ---------------------------------------------------------------------------
# Task 1.2 — headline bug: scheduled path must refresh the map
# ---------------------------------------------------------------------------


class TestScheduledMapRefresh:
    """``schedule_collect_links`` must re-expand sitemaps and rebuild
    ``_sitemap_lastmod_map`` so an advanced ``<lastmod>`` reaches the map
    without a process restart (spec scenario: "A changed lastmod reaches the
    map without a restart").

    These tests are RED against the current implementation (the scheduled path
    never calls ``_expand_sitemaps``).
    """

    def test_scheduled_refresh_updates_lastmod_map(self, refresh_harness):
        """After ``collect_all_from_config`` seeds the map, a subsequent
        ``schedule_collect_links`` with updated sitemap values must produce a
        map that holds the new lastmod values, not the original ones.
        """
        h = refresh_harness
        h["manager"].collect_all_from_config(h["persistence"])

        # Precondition: map was seeded from the initial expansion.
        assert h["manager"]._sitemap_lastmod_map == {
            "https://x.example.edu/a": "2024-01-01",
            "https://x.example.edu/b": "2024-02-01",
        }

        # Simulate the sitemap advancing while the process is running.
        h["set_expand_pairs"](
            [
                ("https://x.example.edu/a", "2025-06-01"),
                ("https://x.example.edu/b", "2025-07-01"),
            ]
        )

        h["manager"].schedule_collect_links(h["persistence"])

        assert h["manager"]._sitemap_lastmod_map == {
            "https://x.example.edu/a": "2025-06-01",
            "https://x.example.edu/b": "2025-07-01",
        }


# ---------------------------------------------------------------------------
# Task 1.3 — refresh happens BEFORE collect_links is invoked
# ---------------------------------------------------------------------------


class TestRefreshPrecedesCollectLinks:
    """``schedule_collect_links`` must refresh ``_sitemap_lastmod_map`` before
    it invokes ``collect_links``, not after.  A spy on ``collect_links``
    captures the map's contents at call time; the test asserts those contents
    are the *new* values, not the original ones.

    This test is RED against the current implementation (the scheduled path
    never calls ``_expand_sitemaps`` at all, so the map is never updated).
    """

    def test_map_is_refreshed_before_collect_links_is_called(self, refresh_harness):
        """The map seen by ``collect_links`` must contain the *updated* lastmod
        values, not the values seeded by ``collect_all_from_config``.

        This distinguishes "refresh happens before the call" from "attribute is
        updated somewhere after the call returns" — the spy reads the map at the
        exact moment ``collect_links`` is entered.
        """
        h = refresh_harness
        manager = h["manager"]

        h["manager"].collect_all_from_config(h["persistence"])

        # Advance the sitemap before the scheduled run.
        updated_pairs = [
            ("https://x.example.edu/a", "2025-06-01"),
            ("https://x.example.edu/b", "2025-07-01"),
        ]
        h["set_expand_pairs"](updated_pairs)

        # Spy: capture a *copy* of the map at the moment collect_links is entered.
        map_at_collect_links_call = {}

        original_collect_links = manager.__class__.collect_links

        def spy_collect_links(self, *args, **kwargs):
            map_at_collect_links_call.update(self._sitemap_lastmod_map)
            return original_collect_links(self, *args, **kwargs)

        manager.collect_links = lambda *a, **k: (
            map_at_collect_links_call.update(manager._sitemap_lastmod_map) or 0
        )

        manager.schedule_collect_links(h["persistence"])

        assert map_at_collect_links_call == {
            "https://x.example.edu/a": "2025-06-01",
            "https://x.example.edu/b": "2025-07-01",
        }, (
            "collect_links was invoked with the stale map; "
            f"got {map_at_collect_links_call!r}"
        )


# ---------------------------------------------------------------------------
# Task 2.5 — initial ingest fails fast on SitemapExpansionError
# ---------------------------------------------------------------------------


class TestInitialIngestFailsFast:
    """``collect_all_from_config`` must propagate ``SitemapExpansionError`` from
    ``_expand_sitemaps`` immediately, without calling any collector.

    This pins design D1: the initial-ingest path never catches the error so a
    below-floor / over-cap expansion aborts the ingest rather than proceeding
    with a bad (or empty) corpus.
    """

    def test_expansion_error_propagates_from_collect_all(
        self, make_manager, monkeypatch
    ):
        """``SitemapExpansionError`` raised by ``_expand_sitemaps`` during
        ``collect_all_from_config`` propagates to the caller unchanged.
        """
        manager = make_manager({})
        sitemap_marker = ["sitemap-https://x.example.edu/sitemap.xml"]

        monkeypatch.setattr(
            manager,
            "_collect_urls_from_lists_by_type",
            lambda _lists: ([], [], [], [], [], list(sitemap_marker)),
        )

        def _raise_below_floor(_urls):
            raise SitemapExpansionError("below floor", reason="below_floor")

        monkeypatch.setattr(manager, "_expand_sitemaps", _raise_below_floor)

        class FakePersistence:
            pass

        with pytest.raises(SitemapExpansionError, match="below floor"):
            manager.collect_all_from_config(FakePersistence())

    def test_no_collection_proceeds_after_expansion_error(
        self, make_manager, monkeypatch
    ):
        """When ``_expand_sitemaps`` raises, none of the collector methods are
        called — the ingest stops before any data is written.
        """
        manager = make_manager({})
        sitemap_marker = ["sitemap-https://x.example.edu/sitemap.xml"]
        calls = []

        monkeypatch.setattr(
            manager,
            "_collect_urls_from_lists_by_type",
            lambda _lists: ([], [], [], [], [], list(sitemap_marker)),
        )

        def _raise_over_cap(_urls):
            raise SitemapExpansionError("over cap", reason="over_cap")

        monkeypatch.setattr(manager, "_expand_sitemaps", _raise_over_cap)
        for name in (
            "collect_links",
            "collect_sso",
            "collect_git",
            "collect_elog",
            "collect_indico",
        ):
            monkeypatch.setattr(
                manager, name, lambda *a, _n=name, **k: calls.append(_n)
            )

        class FakePersistence:
            pass

        with pytest.raises(SitemapExpansionError):
            manager.collect_all_from_config(FakePersistence())

        assert calls == [], f"collectors were called after expansion error: {calls}"


# ---------------------------------------------------------------------------
# Task 3.2 — degraded path: SitemapExpansionError during schedule_collect_links
# ---------------------------------------------------------------------------


class TestScheduledDegradedPath:
    """When ``_expand_sitemaps`` raises ``SitemapExpansionError`` during
    ``schedule_collect_links``, the call must not propagate the exception,
    ``collect_links`` must still run over the catalog URLs, and the map from
    the last successful refresh must be intact entry-for-entry.

    These tests are RED against the current implementation, which does not
    catch ``SitemapExpansionError`` in the scheduled path.
    """

    @pytest.mark.xfail(
        strict=True,
        reason="degraded fallback not yet implemented (task 3.4)",
    )
    def test_expansion_error_does_not_propagate_from_schedule(
        self, refresh_harness, monkeypatch
    ):
        """``SitemapExpansionError`` raised by ``_expand_sitemaps`` during
        ``schedule_collect_links`` must not propagate to the caller.
        """
        h = refresh_harness
        h["manager"].collect_all_from_config(h["persistence"])

        monkeypatch.setattr(
            h["manager"],
            "_expand_sitemaps",
            lambda _: (_ for _ in ()).throw(
                SitemapExpansionError("transient DNS failure", reason="below_floor")
            ),
        )

        # Must not raise — the degraded path swallows the error.
        h["manager"].schedule_collect_links(h["persistence"])

    @pytest.mark.xfail(
        strict=True,
        reason="degraded fallback not yet implemented (task 3.4)",
    )
    def test_collect_links_runs_after_expansion_error(
        self, refresh_harness, monkeypatch
    ):
        """When the refresh fails, ``collect_links`` must still be invoked
        so the scheduled scrape of catalog URLs proceeds.
        """
        h = refresh_harness
        h["manager"].collect_all_from_config(h["persistence"])

        monkeypatch.setattr(
            h["manager"],
            "_expand_sitemaps",
            lambda _: (_ for _ in ()).throw(
                SitemapExpansionError("over cap", reason="over_cap")
            ),
        )

        collect_links_called = []

        def spy_collect_links(*args, **kwargs):
            collect_links_called.append(True)
            return 0

        monkeypatch.setattr(h["manager"], "collect_links", spy_collect_links)

        h["manager"].schedule_collect_links(h["persistence"])

        assert (
            collect_links_called
        ), "collect_links was not called after expansion error"

    @pytest.mark.xfail(
        strict=True,
        reason="degraded fallback not yet implemented (task 3.4)",
    )
    def test_previous_map_intact_after_expansion_error(
        self, refresh_harness, monkeypatch
    ):
        """The map from the last successful refresh must be preserved entry-for-entry
        after a failed refresh — neither empty nor partially rebuilt.
        """
        h = refresh_harness
        h["manager"].collect_all_from_config(h["persistence"])

        expected_map = dict(h["manager"]._sitemap_lastmod_map)
        assert expected_map, "precondition: map must be non-empty after initial ingest"

        monkeypatch.setattr(
            h["manager"],
            "_expand_sitemaps",
            lambda _: (_ for _ in ()).throw(
                SitemapExpansionError("server error", reason="below_floor")
            ),
        )

        h["manager"].schedule_collect_links(h["persistence"])

        assert h["manager"]._sitemap_lastmod_map == expected_map, (
            f"map was modified by a failed refresh; "
            f"expected {expected_map!r}, got {h['manager']._sitemap_lastmod_map!r}"
        )


# ---------------------------------------------------------------------------
# Task 3.3 — map is never blanked: atomic replacement (design D3)
# ---------------------------------------------------------------------------


class TestMapAtomicReplacement:
    """Design D3: ``_sitemap_lastmod_map`` is replaced atomically, never cleared
    in place before expansion is attempted.

    A clear-then-populate implementation would blank the map *before* calling
    ``_expand_sitemaps``; if expansion then raises, the map is left empty and
    every page is silently un-stamped.  This test pins the absence of that
    pattern by reading the map from *inside* the expansion spy — distinct from
    the 3.2 tests, which only verify the final map state after the call returns.

    The test is RED against the current implementation because
    ``schedule_collect_links`` does not yet catch ``SitemapExpansionError``
    (task 3.4).  Once that fallback is wired, the spy assertion drives away the
    clear-then-populate antipattern.
    """

    @pytest.mark.xfail(
        strict=True,
        reason="degraded fallback not yet implemented (task 3.4)",
    )
    def test_map_is_not_cleared_before_expansion(self, refresh_harness, monkeypatch):
        """The old map must be intact at the moment ``_expand_sitemaps`` is called.

        A clear-then-populate implementation clears ``_sitemap_lastmod_map``
        *before* calling ``_expand_sitemaps``; the spy would then see an empty
        map.  With the local-dict approach (design D3), the assignment only
        happens after expansion succeeds, so the spy sees the full previous map.
        """
        h = refresh_harness
        manager = h["manager"]

        h["manager"].collect_all_from_config(h["persistence"])

        expected_map = dict(manager._sitemap_lastmod_map)
        assert expected_map, "precondition: map must be non-empty after initial ingest"

        map_state_during_expansion = {}

        def _raising_spy(_sitemap_urls):
            # Read the live attribute — empty here means the map was cleared
            # before expansion, which is the antipattern design D3 forbids.
            map_state_during_expansion.update(manager._sitemap_lastmod_map)
            raise SitemapExpansionError("transient failure", reason="below_floor")

        monkeypatch.setattr(manager, "_expand_sitemaps", _raising_spy)

        # Must not raise — the degraded path (task 3.4) swallows the error.
        manager.schedule_collect_links(h["persistence"])

        assert map_state_during_expansion == expected_map, (
            "the map was cleared before _expand_sitemaps was called; "
            f"expected {expected_map!r} inside the spy, "
            f"got {map_state_during_expansion!r}"
        )
