"""An input list that exists but cannot be read must be skipped, not crash (#636).

Staging already tolerates a missing input list by warning and skipping
(fasrc/archi#287). A list that exists but is unreadable -- e.g. permission
denied -- is no more usable than an absent one, so it gets the same
treatment rather than raising ``PermissionError`` out of
``prepare_deployment_files()``.
"""

import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.data_manager.collectors.scrapers.scraper_manager as sm_module
from src.cli.managers import templates_manager as tm_module
from src.cli.managers.templates_manager import TemplateManager
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_staging_skips_permission_denied_list(tmp_path, caplog):
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()

    locked = tmp_path / "locked.list"
    locked.write_text("https://example.org/locked\n")
    readable = tmp_path / "urls.txt"
    readable.write_text("https://example.org\n")

    locked.chmod(0)
    try:

        class _CM:
            config = {}

            def get_input_lists(self):
                return [str(locked), str(readable)]

        context = SimpleNamespace(config_manager=_CM(), base_dir=base_dir)

        mgr = object.__new__(TemplateManager)
        with caplog.at_level(logging.WARNING):
            mgr._copy_web_input_lists(context)
    finally:
        locked.chmod(0o644)

    staged = sorted(p.name for p in (base_dir / "weblists").iterdir())
    assert staged == ["urls.txt"], (
        f"the unreadable list should be skipped and the real list still staged, "
        f"got {staged}"
    )
    assert str(locked) in caplog.text


def test_staging_removes_a_partial_copy(tmp_path, monkeypatch, caplog):
    """A copy that fails after it opened the destination must not leave a
    truncated list in ``weblists`` for the runtime to ingest (PR #689 review)."""
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    broken = tmp_path / "broken.list"
    broken.write_text("https://example.org/one\nhttps://example.org/two\n")
    readable = tmp_path / "urls.txt"
    readable.write_text("https://example.org\n")
    real_copyfile = tm_module.shutil.copyfile

    def _copyfile(src, dst):
        if Path(src) == broken:
            Path(dst).write_text("https://example.org/o")
            raise OSError(5, "Input/output error")
        return real_copyfile(src, dst)

    monkeypatch.setattr(tm_module.shutil, "copyfile", _copyfile)

    class _CM:
        config = {}

        def get_input_lists(self):
            return [str(broken), str(readable)]

    context = SimpleNamespace(config_manager=_CM(), base_dir=base_dir)
    mgr = object.__new__(TemplateManager)
    with caplog.at_level(logging.WARNING):
        mgr._copy_web_input_lists(context)

    assert sorted(p.name for p in (base_dir / "weblists").iterdir()) == ["urls.txt"]
    assert str(broken) in caplog.text


def test_staging_failed_winner_removes_the_earlier_copy(tmp_path, monkeypatch):
    """When the lexically later of two same-named lists fails to copy, the
    earlier list's copy must not stay staged in its place: source inference
    treats the later list as the only one staged (PR #689 review)."""
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    first = tmp_path / "a" / "urls.txt"
    first.parent.mkdir()
    first.write_text("git-https://example.org/repo\n")
    winner = tmp_path / "b" / "urls.txt"
    winner.parent.mkdir()
    winner.write_text("https://example.org\n")
    real_copyfile = tm_module.shutil.copyfile

    def _copyfile(src, dst):
        if Path(src) == winner:
            raise OSError(5, "Input/output error")
        return real_copyfile(src, dst)

    monkeypatch.setattr(tm_module.shutil, "copyfile", _copyfile)

    class _CM:
        config = {}

        def get_input_lists(self):
            return [str(first), str(winner)]

    context = SimpleNamespace(config_manager=_CM(), base_dir=base_dir)
    mgr = object.__new__(TemplateManager)
    mgr._copy_web_input_lists(context)

    assert list((base_dir / "weblists").iterdir()) == []


def _stage(base_dir, paths):
    class _CM:
        config = {}

        def get_input_lists(self):
            return [str(p) for p in paths]

    mgr = object.__new__(TemplateManager)
    mgr._copy_web_input_lists(SimpleNamespace(config_manager=_CM(), base_dir=base_dir))


def test_staging_side_file_cannot_clobber_a_configured_list(tmp_path):
    """A configured list whose name looks like another list's side file is
    staged intact (PR #689 adversarial review)."""
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    lookalike = tmp_path / ".urls.txt.partial"
    lookalike.write_text("https://example.org/lookalike\n")
    readable = tmp_path / "urls.txt"
    readable.write_text("https://example.org\n")

    _stage(base_dir, [lookalike, readable])

    weblists = base_dir / "weblists"
    assert sorted(p.name for p in weblists.iterdir()) == [
        ".urls.txt.partial",
        "urls.txt",
    ]
    assert (
        weblists / ".urls.txt.partial"
    ).read_text() == "https://example.org/lookalike\n"


def test_staging_cleanup_failure_still_skips(tmp_path, monkeypatch, caplog):
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    broken = tmp_path / "broken.list"
    broken.write_text("https://example.org/one\n")
    readable = tmp_path / "urls.txt"
    readable.write_text("https://example.org\n")
    real_copyfile = tm_module.shutil.copyfile

    def _copyfile(src, dst):
        if Path(src) == broken:
            raise OSError(5, "Input/output error")
        return real_copyfile(src, dst)

    def _unlink(self, missing_ok=False):
        raise IsADirectoryError(21, "Is a directory")

    monkeypatch.setattr(tm_module.shutil, "copyfile", _copyfile)
    monkeypatch.setattr(Path, "unlink", _unlink)
    with caplog.at_level(logging.WARNING):
        _stage(base_dir, [broken, readable])

    assert (base_dir / "weblists" / "urls.txt").exists()
    assert str(broken) in caplog.text


def test_staging_long_basename_is_staged(tmp_path):
    """A list whose basename is near the 255-byte component limit is staged: the
    side file's name must not grow with the list's name (PR #689 review)."""
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    long_list = tmp_path / ("u" * 246 + ".txt")
    long_list.write_text("https://example.org\n")

    _stage(base_dir, [long_list])

    assert [p.name for p in (base_dir / "weblists").iterdir()] == [long_list.name]


def test_staging_failure_never_deletes_the_configured_source(tmp_path, monkeypatch):
    """A configured list that is its own staged copy (a restart whose config
    points into the deployment's weblists) survives a failed copy (PR #689 review)."""
    base_dir = tmp_path / "deploy"
    weblists = base_dir / "weblists"
    weblists.mkdir(parents=True)
    source = weblists / "urls.txt"
    source.write_text("https://example.org\n")

    def _copyfile(src, dst):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(tm_module.shutil, "copyfile", _copyfile)
    _stage(base_dir, [source])

    assert source.read_text() == "https://example.org\n"


def test_staging_failed_winner_never_stages_the_loser(tmp_path, monkeypatch):
    """When the later of two same-named lists fails and the cleanup cannot
    remove files either, the earlier list is not left staged in its place: it
    was never copied, because the later list wins its name (PR #689 review)."""
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    first = tmp_path / "a" / "urls.txt"
    first.parent.mkdir()
    first.write_text("git-https://example.org/repo\n")
    winner = tmp_path / "b" / "urls.txt"
    winner.parent.mkdir()
    winner.write_text("https://example.org\n")
    real_copyfile = tm_module.shutil.copyfile

    def _copyfile(src, dst):
        if Path(src) == winner:
            raise OSError(5, "Input/output error")
        return real_copyfile(src, dst)

    def _unlink(self, missing_ok=False):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(tm_module.shutil, "copyfile", _copyfile)
    monkeypatch.setattr(Path, "unlink", _unlink)
    _stage(base_dir, [first, winner])

    assert list((base_dir / "weblists").iterdir()) == []


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


def _weblists_with_good_list(tmp_path, monkeypatch):
    weblists = tmp_path / "weblists"
    weblists.mkdir()
    (weblists / "good.list").write_text("https://x.example.edu/one\n")
    monkeypatch.chdir(tmp_path)
    return weblists


def test_runtime_skips_non_utf8_list(tmp_path, monkeypatch, make_manager, caplog):
    weblists = _weblists_with_good_list(tmp_path, monkeypatch)
    (weblists / "bad.list").write_bytes(b"\xff\xfe\x00git-https://x\n")
    manager = make_manager()

    with caplog.at_level(logging.WARNING):
        urls = manager._collect_urls_from_lists(["bad.list", "good.list"])

    assert urls == ["https://x.example.edu/one"]
    assert manager._input_lists_complete is False
    assert str(Path("weblists") / "bad.list") in caplog.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_runtime_skips_permission_denied_list(
    tmp_path, monkeypatch, make_manager, caplog
):
    weblists = _weblists_with_good_list(tmp_path, monkeypatch)
    locked = weblists / "locked.list"
    locked.write_text("https://x.example.edu/locked\n")
    manager = make_manager()

    locked.chmod(0)
    try:
        with caplog.at_level(logging.WARNING):
            urls = manager._collect_urls_from_lists(["locked.list", "good.list"])
    finally:
        locked.chmod(0o644)

    assert urls == ["https://x.example.edu/one"]
    assert manager._input_lists_complete is False
    assert str(Path("weblists") / "locked.list") in caplog.text
