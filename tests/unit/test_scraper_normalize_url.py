"""
Unit tests for LinkScraper._normalize_url trailing-slash canonicalization (issue #118).

These assert that a URL ending in a single trailing slash and the otherwise-identical
form without it normalize to the same string, while preserving the existing contract
(site root, empty path, query/params, empty/None, schemeless input).
"""

from src.data_manager.collectors.scrapers.scraper import LinkScraper


def _scraper():
    return LinkScraper()


class TestSlashCollapse:

    def test_slash_and_no_slash_variants_collapse(self):
        scraper = _scraper()
        with_slash = scraper._normalize_url("https://docs.rc.fas.harvard.edu/kb/x/")
        without_slash = scraper._normalize_url("https://docs.rc.fas.harvard.edu/kb/x")
        assert with_slash == without_slash

    def test_deep_path_trailing_slash_stripped(self):
        scraper = _scraper()
        assert scraper._normalize_url("https://host/a/b/c/") == "https://host/a/b/c"


class TestRootPreservation:

    def test_root_slash_preserved(self):
        scraper = _scraper()
        assert scraper._normalize_url("https://host/") == "https://host/"

    def test_empty_path_not_given_or_stripped_slash(self):
        scraper = _scraper()
        # https://host has no path; trailing-slash handling must not add or remove one.
        result = scraper._normalize_url("https://host")
        assert result == "https://host"


class TestQueryConsistency:

    def test_query_survives_path_canonicalization(self):
        scraper = _scraper()
        with_slash = scraper._normalize_url("https://host/x/?a=1")
        without_slash = scraper._normalize_url("https://host/x?a=1")
        assert with_slash == without_slash
        assert "a=1" in with_slash


class TestMatrixParams:

    def test_matrix_params_not_corrupted_by_slash_strip(self):
        scraper = _scraper()
        # In `/x/;v=1`, the `;v=1` matrix parameter belongs to the empty trailing
        # segment. Stripping the slash would reattach it to `x` (`/x;v=1`), which
        # is a different resource. Skip the strip when params are present.
        result = scraper._normalize_url("https://host/x/;v=1?a=1")
        assert result == "https://host/x/;v=1?a=1"


class TestPreservedContract:

    def test_empty_string_returns_none(self):
        assert _scraper()._normalize_url("") is None

    def test_none_returns_none(self):
        assert _scraper()._normalize_url(None) is None

    def test_schemeless_relative_url_does_not_raise(self):
        # A schemeless (relative) URL is returned without raising.
        _scraper()._normalize_url("/kb/x/")
