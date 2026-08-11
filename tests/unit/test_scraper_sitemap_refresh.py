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
from src.data_manager.collectors.scrapers import sitemap_source as sitemap_source_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.scrapers.sitemap_source import (
    SitemapExpansionError,
    SitemapFetchError,
)

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

    def test_expansion_error_is_logged_as_warning(
        self, refresh_harness, monkeypatch, caplog
    ):
        """A ``SitemapExpansionError`` during the scheduled refresh must emit a
        ``WARNING`` log record whose message contains the exception text (design D6).
        """
        import logging

        h = refresh_harness
        h["manager"].collect_all_from_config(h["persistence"])

        error_text = "transient DNS failure"
        monkeypatch.setattr(
            h["manager"],
            "_expand_sitemaps",
            lambda _: (_ for _ in ()).throw(
                SitemapExpansionError(error_text, reason="below_floor")
            ),
        )

        with caplog.at_level(logging.WARNING):
            h["manager"].schedule_collect_links(h["persistence"])

        warning_messages = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            error_text in msg for msg in warning_messages
        ), f"expected warning containing {error_text!r}; got {warning_messages!r}"


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


# ---------------------------------------------------------------------------
# Task 4.1 — scheduled path: hand-listed pages excluded from refreshed map
# ---------------------------------------------------------------------------


class TestScheduledHandListExclusion:
    """On the scheduled path, a page that is both hand-listed in ``input_lists``
    and present in the sitemap must get **no** entry in the refreshed map (design
    D2 / spec exclusion rule).

    The test uses a normalization-variant pair: the hand-list contains
    ``/hand/`` (trailing slash) while the sitemap emits the trailing-slash-stripped
    form ``/hand`` — they are the same page via ``normalize_page_url`` and must
    collide so the sitemap entry is excluded.  A sitemap-only page must still get
    an entry, confirming the exclusion is selective.
    """

    def test_hand_listed_page_excluded_from_scheduled_map(
        self, refresh_harness, monkeypatch
    ):
        """A page hand-listed as ``/hand/`` (trailing slash) that also appears in
        the sitemap as the normalized ``/hand`` must not appear in
        ``_sitemap_lastmod_map`` after ``schedule_collect_links``.

        A sitemap-only page must appear, confirming that only the hand-listed URL
        is excluded, not the whole map.
        """
        h = refresh_harness
        # The harness hand-list contains "https://x.example.edu/hand/" (trailing
        # slash).  The sitemap emits the one-slash-stripped variant — same page
        # after normalize_page_url, so the match must happen via normalization, not
        # raw string equality.
        monkeypatch.setattr(
            h["manager"],
            "_expand_sitemaps",
            lambda _: [
                ("https://x.example.edu/hand", "2024-03-01"),  # also hand-listed
                ("https://x.example.edu/a", "2024-01-01"),  # sitemap-only
            ],
        )

        h["manager"].schedule_collect_links(h["persistence"])

        assert "https://x.example.edu/hand" not in h["manager"]._sitemap_lastmod_map, (
            "hand-listed page (matched via normalization) must not appear in the "
            "refreshed map — its last_modified must stay NULL"
        )
        assert (
            h["manager"]._sitemap_lastmod_map.get("https://x.example.edu/a")
            == "2024-01-01"
        ), "sitemap-only page must appear in the refreshed map"


# ---------------------------------------------------------------------------
# Task 4.2 — scheduled path: wholesale map replacement (design D5)
# ---------------------------------------------------------------------------


class TestScheduledMapWholesaleReplacement:
    """Design D5: the scheduled refresh wholly replaces ``_sitemap_lastmod_map``
    from the new expansion result — it does not merge with or augment the previous
    map.

    A page present in the first (initial-ingest) expansion but absent from the
    second (scheduled-refresh) expansion must have no entry after
    ``schedule_collect_links`` returns, so ``_handle_standard_url`` will not stamp
    it and its stored ``last_modified`` returns to NULL.

    This distinguishes wholesale replacement from an incremental update where
    old entries survive a refresh that doesn't mention them.
    """

    def test_page_dropped_from_sitemap_is_removed_from_map(
        self, refresh_harness, monkeypatch
    ):
        """A page that was in the initial map but is absent from the scheduled
        expansion must not appear in ``_sitemap_lastmod_map`` after
        ``schedule_collect_links``.

        Scenario:
        - Initial expansion: ``/a`` at ``2024-01-01``, ``/b`` at ``2024-02-01``
          → both enter the map.
        - Scheduled expansion: only ``/a`` at ``2025-06-01`` — ``/b`` is gone.
        - After ``schedule_collect_links``: map holds ``/a`` at ``2025-06-01``
          and has NO entry for ``/b``.
        """
        h = refresh_harness
        h["manager"].collect_all_from_config(h["persistence"])

        # Precondition: both pages are in the map after initial ingest.
        assert "https://x.example.edu/a" in h["manager"]._sitemap_lastmod_map
        assert "https://x.example.edu/b" in h["manager"]._sitemap_lastmod_map

        # Second expansion: /b is no longer in the sitemap.
        monkeypatch.setattr(
            h["manager"],
            "_expand_sitemaps",
            lambda _: [("https://x.example.edu/a", "2025-06-01")],
        )

        h["manager"].schedule_collect_links(h["persistence"])

        assert "https://x.example.edu/b" not in h["manager"]._sitemap_lastmod_map, (
            "page dropped from the sitemap must not survive in the map; "
            "the refresh must wholesale-replace, not incrementally update"
        )
        assert h["manager"]._sitemap_lastmod_map.get("https://x.example.edu/a") == (
            "2025-06-01"
        ), "page still present in the sitemap must appear with its new lastmod"


# ---------------------------------------------------------------------------
# Task 4.3 — scheduled crawl set unchanged: new sitemap pages skip collect_links
# ---------------------------------------------------------------------------


class TestScheduledCrawlSetUnchanged:
    """A page newly present in the sitemap but absent from the catalog gains a
    map entry after ``schedule_collect_links`` but is **not** passed to
    ``collect_links`` — the crawl set is the catalog's result, not the sitemap
    expansion result.

    This pins the separation between the lastmod map (all current sitemap pages)
    and the crawl target list (only already-ingested catalog pages).
    """

    def test_new_sitemap_page_enters_map_but_not_crawl_set(
        self, refresh_harness, monkeypatch
    ):
        """A page ``/c`` newly present in the sitemap but absent from the catalog
        must appear in ``_sitemap_lastmod_map`` (so its lastmod can be stamped if
        it is ever ingested) but must **not** appear in the ``link_urls`` argument
        passed to ``collect_links`` (so the crawl set does not silently grow).

        Scenario:
        - Catalog holds ``/a`` and ``/b`` (set up by the harness).
        - Scheduled expansion returns ``/a``, ``/b``, and a new ``/c``.
        - After ``schedule_collect_links``: ``/c`` is in the map, and the
          ``link_urls`` seen by ``collect_links`` contains only catalog URLs
          (``/a``, ``/b``), not ``/c``.
        """
        h = refresh_harness
        manager = h["manager"]

        new_page = "https://x.example.edu/c"

        monkeypatch.setattr(
            manager,
            "_expand_sitemaps",
            lambda _: [
                ("https://x.example.edu/a", "2025-06-01"),
                ("https://x.example.edu/b", "2025-07-01"),
                (new_page, "2025-08-01"),
            ],
        )

        crawled_urls = []

        def spy_collect_links(persistence, link_urls=(), **kwargs):
            crawled_urls.extend(link_urls)
            return 0

        monkeypatch.setattr(manager, "collect_links", spy_collect_links)

        manager.schedule_collect_links(h["persistence"])

        assert new_page in manager._sitemap_lastmod_map, (
            f"{new_page!r} must enter _sitemap_lastmod_map so its lastmod can be "
            "stamped; it is missing"
        )
        assert manager._sitemap_lastmod_map[new_page] == "2025-08-01"

        assert new_page not in crawled_urls, (
            f"{new_page!r} must not be passed to collect_links — the crawl set "
            "is the catalog result, not the sitemap expansion"
        )
        assert "https://x.example.edu/a" in crawled_urls
        assert "https://x.example.edu/b" in crawled_urls


# ---------------------------------------------------------------------------
# Review findings on PR #230
# ---------------------------------------------------------------------------


class TestAllSitemapSourcesRemoved:
    """Dropping the last ``sitemap-`` entry must clear the map, not freeze it.

    The scheduled refresh is guarded by ``if sitemap_urls:``. When an input list is
    edited at runtime to remove or reclassify its final sitemap source, that guard
    skips the refresh and the previous map survives, so the ensuing catalog crawl
    keeps stamping pages with timestamps from a sitemap that is no longer
    configured. Removal of a whole source should behave like the removal of an
    individual page, which the wholesale-replacement semantics already handle.
    """

    def test_map_is_cleared_when_no_sitemap_sources_remain(
        self, refresh_harness, monkeypatch
    ):
        manager = refresh_harness["manager"]
        persistence = refresh_harness["persistence"]

        manager.collect_all_from_config(persistence)
        assert manager._sitemap_lastmod_map, "precondition: map populated by ingest"

        # The sitemap source is gone from the lists on the next scheduled pass.
        monkeypatch.setattr(
            manager,
            "_collect_urls_from_lists_by_type",
            lambda _lists: (["https://x.example.edu/hand/"], [], [], [], [], []),
        )
        manager.schedule_collect_links(persistence)

        assert manager._sitemap_lastmod_map == {}, (
            "a configuration with no sitemap sources must leave an empty map; "
            f"got {manager._sitemap_lastmod_map!r}"
        )


class TestPartialExpansionRetainsPreviousMap:
    """A truncated expansion is not a successful refresh.

    ``_fetch_and_parse`` fails open for a per-document fetch/parse error: it logs a
    WARNING and contributes zero pairs. For a ``<sitemapindex>`` whose child fails
    while its siblings succeed, the source can still clear ``min_pages``, so
    ``expand_sitemaps`` returns a TRUNCATED list and raises nothing. Publishing that
    as the new map discards every timestamp belonging to the failed child, and the
    catalog crawl then conflict-upserts NULL over those rows.
    """

    def test_incomplete_expansion_does_not_replace_a_good_map(
        self, refresh_harness, monkeypatch
    ):
        manager = refresh_harness["manager"]
        persistence = refresh_harness["persistence"]

        manager.collect_all_from_config(persistence)
        good_map = dict(manager._sitemap_lastmod_map)
        assert set(good_map) == {
            "https://x.example.edu/a",
            "https://x.example.edu/b",
        }, "precondition: both pages carry timestamps"

        # /b's child sitemap fails to fetch: it contributes nothing, /a still
        # clears min_pages, so expansion returns truncated WITHOUT raising.
        refresh_harness["set_expand_pairs"]([("https://x.example.edu/a", "2024-01-01")])
        manager._sitemap_expansion_incomplete = True

        manager.schedule_collect_links(persistence)

        assert manager._sitemap_lastmod_map == good_map, (
            "an incomplete expansion must leave the previous map intact; "
            f"got {manager._sitemap_lastmod_map!r}"
        )

    def test_complete_expansion_still_replaces_wholesale(
        self, refresh_harness, monkeypatch
    ):
        """The retention above must not weaken genuine removals.

        When expansion completes, a page that has genuinely left the sitemap must
        still drop out of the map — otherwise the fix for a truncated fetch would
        quietly reintroduce the stale-timestamp bug it was meant to prevent.
        """
        manager = refresh_harness["manager"]
        persistence = refresh_harness["persistence"]

        manager.collect_all_from_config(persistence)
        refresh_harness["set_expand_pairs"]([("https://x.example.edu/a", "2024-01-01")])
        manager._sitemap_expansion_incomplete = False

        manager.schedule_collect_links(persistence)

        assert manager._sitemap_lastmod_map == {
            "https://x.example.edu/a": "2024-01-01"
        }, f"a complete expansion replaces wholesale; got {manager._sitemap_lastmod_map!r}"


class TestNoDegradeWithoutAPriorMap:
    """Swallowing the expansion error is only safe when a map already exists.

    ``service_data_manager`` keeps running after an asynchronous initial ingest
    reports an error, so the scheduled pass can arrive with no map at all while the
    catalog still holds rows from an earlier process. Continuing then crawls with an
    empty map and conflict-upserts NULL into every affected ``last_modified``.
    """

    def test_expansion_error_without_a_map_skips_the_crawl(
        self, refresh_harness, monkeypatch
    ):
        manager = refresh_harness["manager"]
        persistence = refresh_harness["persistence"]

        def _boom(_sitemap_urls):
            raise SitemapExpansionError("sitemap below min_pages", reason="below_floor")

        monkeypatch.setattr(manager, "_expand_sitemaps", _boom)

        crawled = []
        monkeypatch.setattr(
            manager,
            "collect_links",
            lambda *a, **k: crawled.append(k.get("link_urls")),
        )

        manager.schedule_collect_links(persistence)

        assert crawled == [], (
            "with no usable map, the crawl must be skipped rather than stamping "
            f"every page NULL; collect_links got {crawled!r}"
        )

    def test_expansion_error_with_a_good_map_still_degrades_gracefully(
        self, refresh_harness, monkeypatch
    ):
        """With a usable map in hand, the documented degrade path is preserved."""
        manager = refresh_harness["manager"]
        persistence = refresh_harness["persistence"]

        manager.collect_all_from_config(persistence)
        good_map = dict(manager._sitemap_lastmod_map)

        def _boom(_sitemap_urls):
            raise SitemapExpansionError(
                "sitemap temporarily unreachable", reason="below_floor"
            )

        monkeypatch.setattr(manager, "_expand_sitemaps", _boom)

        crawled = []
        monkeypatch.setattr(
            manager,
            "collect_links",
            lambda *a, **k: crawled.append(k.get("link_urls")),
        )

        manager.schedule_collect_links(persistence)

        assert len(crawled) == 1, "the crawl still runs when a good map is available"
        assert manager._sitemap_lastmod_map == good_map, "the prior map is preserved"


class TestExpansionCompletenessSignal:
    """The incompleteness flag must come from real expansion, not just be settable.

    The retention tests above set ``_sitemap_expansion_incomplete`` directly, which
    would pass even if nothing ever set it in production. This exercises the whole
    path: a ``<sitemapindex>`` whose child 404s, through the real
    ``expand_sitemaps``, must mark the expansion incomplete while still returning
    the surviving sibling's pages.
    """

    def test_failed_child_sets_the_incomplete_flag(self, make_manager, monkeypatch):
        index_xml = (
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://x.example.edu/good.xml</loc></sitemap>"
            "<sitemap><loc>https://x.example.edu/bad.xml</loc></sitemap>"
            "</sitemapindex>"
        )
        good_xml = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://x.example.edu/a</loc>"
            "<lastmod>2024-01-01</lastmod></url>"
            "</urlset>"
        )

        def fake_fetch(url, **_kwargs):
            if url.endswith("/sitemap.xml"):
                return index_xml
            if url.endswith("/good.xml"):
                return good_xml
            raise SitemapFetchError(f"404 fetching {url}")

        monkeypatch.setattr(
            sitemap_source_module, "fetch_sitemap_text", fake_fetch, raising=True
        )

        manager = make_manager({})
        pairs = manager._expand_sitemaps(["https://x.example.edu/sitemap.xml"])

        assert (
            "https://x.example.edu/a",
            "2024-01-01",
        ) in pairs, "the surviving sibling's pages must still be returned"
        assert (
            manager._sitemap_expansion_incomplete is True
        ), "a child that failed to fetch must mark the expansion incomplete"

    def test_all_children_succeed_leaves_the_flag_clear(
        self, make_manager, monkeypatch
    ):
        index_xml = (
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://x.example.edu/good.xml</loc></sitemap>"
            "</sitemapindex>"
        )
        good_xml = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://x.example.edu/a</loc>"
            "<lastmod>2024-01-01</lastmod></url>"
            "</urlset>"
        )

        def fake_fetch(url, **_kwargs):
            return index_xml if url.endswith("/sitemap.xml") else good_xml

        monkeypatch.setattr(
            sitemap_source_module, "fetch_sitemap_text", fake_fetch, raising=True
        )

        manager = make_manager({})
        manager._expand_sitemaps(["https://x.example.edu/sitemap.xml"])

        assert manager._sitemap_expansion_incomplete is False


class TestEmptyButValidMap:
    """A successfully-published empty map is not the same as "no map yet".

    Every page in a sitemap may legitimately omit the optional ``<lastmod>``, in
    which case a fully successful refresh publishes ``{}``. Testing the map for
    truthiness conflates that with "initial ingest never built one", so a later
    expansion error would skip the whole scheduled crawl instead of degrading with
    the valid empty map.
    """

    def test_expansion_error_after_a_valid_empty_map_still_crawls(
        self, refresh_harness, monkeypatch
    ):
        manager = refresh_harness["manager"]
        persistence = refresh_harness["persistence"]

        # Initial ingest succeeds, but no page carries a lastmod → map is {}.
        refresh_harness["set_expand_pairs"](
            [("https://x.example.edu/a", None), ("https://x.example.edu/b", None)]
        )
        manager.collect_all_from_config(persistence)
        assert manager._sitemap_lastmod_map == {}, "precondition: valid but empty map"

        def _boom(_sitemap_urls):
            raise SitemapExpansionError("temporarily unreachable", reason="below_floor")

        monkeypatch.setattr(manager, "_expand_sitemaps", _boom)
        crawled = []
        monkeypatch.setattr(
            manager, "collect_links", lambda *a, **k: crawled.append(k.get("link_urls"))
        )

        manager.schedule_collect_links(persistence)

        assert len(crawled) == 1, (
            "a previously published empty map is a successful refresh, so the crawl "
            "must still run rather than being skipped as 'no map'"
        )


class TestMissingInputListDoesNotClearTheMap:
    """An unreadable list is not a configuration that dropped its sitemap sources.

    ``_collect_urls_from_lists`` warns and skips a configured file that is missing,
    so ``sitemap_urls`` comes back empty for a transient IO reason. Clearing the map
    on that is indistinguishable from an intentional removal, and the ensuing crawl
    re-persists every page with no timestamp — which the unconditional upsert turns
    into NULL.
    """

    def test_unreadable_list_preserves_the_map(self, refresh_harness, monkeypatch):
        manager = refresh_harness["manager"]
        persistence = refresh_harness["persistence"]

        manager.collect_all_from_config(persistence)
        good_map = dict(manager._sitemap_lastmod_map)
        assert good_map, "precondition: map populated"

        # The list file vanished this cycle: no sitemap sources seen, and the read
        # was incomplete.
        monkeypatch.setattr(
            manager,
            "_collect_urls_from_lists_by_type",
            lambda _lists: (["https://x.example.edu/hand/"], [], [], [], [], []),
        )
        manager._input_lists_complete = False

        manager.schedule_collect_links(persistence)

        assert manager._sitemap_lastmod_map == good_map, (
            "an unreadable input list must not be treated as an intentional removal "
            f"of every sitemap source; got {manager._sitemap_lastmod_map!r}"
        )

    def test_missing_file_marks_the_read_incomplete(self, make_manager, tmp_path):
        """The flag the guard above depends on is really set by the read path."""
        manager = make_manager({})
        manager._collect_urls_from_lists(["definitely-not-there.list"])
        assert manager._input_lists_complete is False

    def test_all_lists_readable_marks_the_read_complete(
        self, make_manager, tmp_path, monkeypatch
    ):
        manager = make_manager({})
        weblists = tmp_path / "weblists"
        weblists.mkdir()
        (weblists / "a.list").write_text("https://x.example.edu/one\n")
        monkeypatch.chdir(tmp_path)
        manager._collect_urls_from_lists(["a.list"])
        assert manager._input_lists_complete is True
