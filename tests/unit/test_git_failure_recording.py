"""
Tests for GitScraper.last_failures and ScraperManager._collect_git_resources failure forwarding.

Design: openspec/changes/fix-issue-534-report-uncollected-catalog-rows/design.md (D3, git section)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch as _patch

import pytest

# git_scraper.py calls get_global_config() at module level; patch before import.
with _patch(
    "src.utils.config_access.get_global_config", return_value={"DATA_PATH": "/tmp"}
):
    from src.data_manager.collectors.scrapers.integrations.git_scraper import GitScraper

from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.utils.catalog_reconcile import CollectionPass


def _make_git_scraper(tmp_path: Path) -> GitScraper:
    """Create a GitScraper bypassing __init__ to avoid real config/git deps."""
    scraper = object.__new__(GitScraper)
    scraper.data_path = str(tmp_path)
    scraper.git_dir = tmp_path / "raw_git_repos"
    scraper.git_dir.mkdir(parents=True, exist_ok=True)
    scraper.code_suffixes = {".py", ".md", ".txt"}
    scraper.exclude_dirs = {".git"}
    scraper.max_file_size_bytes = 1_000_000
    scraper.git_username = None
    scraper.git_token = None
    scraper._credentials_available = False
    scraper.last_failures = []
    scraper.manager = MagicMock()
    return scraper


def _fake_repo_info(repo_path: Path, repo_name: str = "my-repo") -> dict:
    return {
        "repo_path": repo_path,
        "repo_name": repo_name,
        "mkdocs_site_url": None,
        "ref": "main",
        "web_base_url": f"https://github.com/org/{repo_name}",
    }


def _make_persistence_with_pass():
    """Fake persistence with an open CollectionPass."""
    cp = CollectionPass()
    persistence = MagicMock()
    persistence.collection_pass = cp
    return persistence, cp


# ---------------------------------------------------------------------------
# collect() level: URL parse failures and clone failures
# ---------------------------------------------------------------------------


def test_bad_url_records_none_scope(tmp_path):
    """A URL that fails _parse_url appends (None, reason) to last_failures."""
    scraper = _make_git_scraper(tmp_path)
    scraper.collect(["not-a-valid-github-url"])
    assert len(scraper.last_failures) == 1
    name, reason = scraper.last_failures[0]
    assert name is None
    assert reason


def test_clone_error_records_repo_name(tmp_path, monkeypatch):
    """A clone error after a successful URL parse appends (repo_name, reason)."""
    scraper = _make_git_scraper(tmp_path)
    monkeypatch.setattr(
        GitScraper,
        "_clone_repo",
        lambda self, url_dict: (_ for _ in ()).throw(Exception("connection refused")),
    )
    scraper.collect(["https://github.com/org/my-repo"])
    assert len(scraper.last_failures) == 1
    name, reason = scraper.last_failures[0]
    assert name == "my-repo"
    assert "connection refused" in reason


def test_last_failures_cleared_at_start_of_collect(tmp_path):
    """last_failures is reset to [] at the start of each collect call."""
    scraper = _make_git_scraper(tmp_path)
    scraper.last_failures = [("stale", "old")]
    scraper.collect(["not-a-valid-url"])
    assert all(n != "stale" for n, _ in scraper.last_failures)
    assert len(scraper.last_failures) == 1


# ---------------------------------------------------------------------------
# _harvest_code level: stat, read, binary-open errors
# ---------------------------------------------------------------------------


def test_stat_error_in_harvest_records_repo(tmp_path, monkeypatch):
    """An OSError from file_path.stat() appends (repo_name, reason) to last_failures."""
    scraper = _make_git_scraper(tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "code.py").write_text("print('hello')")

    monkeypatch.setattr(
        Path, "stat", lambda self: (_ for _ in ()).throw(OSError("permission denied"))
    )
    scraper._harvest_code(_fake_repo_info(repo_path))

    assert len(scraper.last_failures) == 1
    name, _reason = scraper.last_failures[0]
    assert name == "my-repo"


def test_read_error_in_harvest_records_repo(tmp_path, monkeypatch):
    """An Exception from read_text appends (repo_name, reason) to last_failures."""
    scraper = _make_git_scraper(tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "code.py").write_text("x = 1")

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, **kwargs: (_ for _ in ()).throw(OSError("read failed")),
    )
    scraper._harvest_code(_fake_repo_info(repo_path))

    assert any(n == "my-repo" for n, _ in scraper.last_failures)


def test_walk_onerror_records_repo(tmp_path, monkeypatch):
    """An os.walk onerror callback appends (repo_name, reason) to last_failures."""
    scraper = _make_git_scraper(tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    def _walk_with_error(path, onerror=None, **kwargs):
        if onerror:
            onerror(OSError("permission denied walking subdir"))
        return iter([])

    monkeypatch.setattr(os, "walk", _walk_with_error)
    scraper._harvest_code(_fake_repo_info(repo_path))

    assert len(scraper.last_failures) == 1
    name, _reason = scraper.last_failures[0]
    assert name == "my-repo"


def test_dangling_symlink_skipped_not_recorded(tmp_path):
    """A dangling symlink is skipped silently; last_failures stays empty and real files still yield."""
    scraper = _make_git_scraper(tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "a.py").write_text("x = 1")
    dangling = repo_path / "dangling.py"
    dangling.symlink_to(repo_path / "missing_target.py")

    resources = scraper._harvest_code(_fake_repo_info(repo_path))

    assert scraper.last_failures == []
    assert len(resources) == 1
    assert resources[0].file_name == "a.py"


def test_binary_open_error_records_repo_not_binary(tmp_path, monkeypatch, caplog):
    """An open error in _looks_binary records the repo; file is not silently skipped as binary.

    The real ``_looks_binary`` runs; only the binary-mode open fails.
    """
    scraper = _make_git_scraper(tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "code.py").write_text("x = 1")

    real_open = Path.open

    def _failing_binary_open(self, mode="r", *args, **kwargs):
        if "b" in mode:
            raise OSError("cannot open for binary read")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _failing_binary_open)
    with caplog.at_level(logging.WARNING):
        resources = scraper._harvest_code(_fake_repo_info(repo_path))

    assert scraper.last_failures == [("my-repo", "cannot open for binary read")]
    assert resources == []
    assert not any("likely binary" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# _collect_git_resources: forwarding last_failures to record_failure
# ---------------------------------------------------------------------------


def test_collect_git_resources_forwards_scope_failure(tmp_path):
    """_collect_git_resources calls record_failure for each (name, reason) in last_failures."""
    fake_scraper = object.__new__(GitScraper)

    def _fake_collect(urls):
        fake_scraper.last_failures = [("my-repo", "clone failed")]
        return []

    fake_scraper.collect = _fake_collect

    manager = object.__new__(ScraperManager)
    manager._git_scraper = fake_scraper

    persistence, cp = _make_persistence_with_pass()

    manager._collect_git_resources(
        ["https://github.com/org/my-repo"], persistence, tmp_path / "git"
    )

    assert ("git", "my-repo") in cp.failed
    assert cp.failed[("git", "my-repo")] == "clone failed"


def test_collect_git_resources_forwards_none_scope_as_failed_type(tmp_path):
    """_collect_git_resources forwards scope_key=None failures as failed_types."""
    fake_scraper = object.__new__(GitScraper)

    def _fake_collect(urls):
        fake_scraper.last_failures = [(None, "bad url")]
        return []

    fake_scraper.collect = _fake_collect

    manager = object.__new__(ScraperManager)
    manager._git_scraper = fake_scraper

    persistence, cp = _make_persistence_with_pass()

    manager._collect_git_resources(["not-a-url"], persistence, tmp_path / "git")

    assert "git" in cp.failed_types
    assert cp.failed_types["git"] == "bad url"
