"""Tests for refuse_missing_input_lists (design E4, tests 1-8).

The helper raises ValueError naming every configured input_lists path that does
not exist. Paths that exist but cannot be read pass the check.
"""

import logging
import os

import pytest

from src.cli.managers.config_manager import ConfigurationManager
from src.cli.source_registry import refuse_missing_input_lists


def _cfg(input_lists, config_path="cfg/a.yaml"):
    return {
        "_config_path": config_path,
        "data_manager": {"sources": {"links": {"input_lists": input_lists}}},
    }


# ── Test 1 ──────────────────────────────────────────────────────────────────


def test_missing_relative_path_names_path_config_and_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cwd = os.getcwd()
    cfg = _cfg(["lists/missing.list"])
    with pytest.raises(ValueError) as exc_info:
        refuse_missing_input_lists([cfg])
    msg = str(exc_info.value)
    assert "lists/missing.list" in msg
    assert "cfg/a.yaml" in msg
    assert cwd in msg


# ── Test 2 ──────────────────────────────────────────────────────────────────


def test_every_missing_path_in_every_config_is_named(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ok = tmp_path / "ok.list"
    ok.write_text("https://example.com\n")

    cfg_a = _cfg(
        ["missing-1.list", str(ok), "missing-1.list"],
        config_path="a.yaml",
    )
    cfg_b = _cfg(
        ["missing-2.list", str(tmp_path / "nope" / "missing-3.list")],
        config_path="b.yaml",
    )

    with pytest.raises(ValueError) as exc_info:
        refuse_missing_input_lists([cfg_a, cfg_b])

    msg = str(exc_info.value)
    lines_with_dash = [ln for ln in msg.splitlines() if ln.startswith("  - ")]
    assert len(lines_with_dash) == 3

    # order: missing-1 (a.yaml), missing-2 (b.yaml), missing-3 (b.yaml)
    assert "missing-1.list" in lines_with_dash[0]
    assert "a.yaml" in lines_with_dash[0]
    assert "missing-2.list" in lines_with_dash[1]
    assert "b.yaml" in lines_with_dash[1]
    assert "missing-3.list" in lines_with_dash[2]
    assert "b.yaml" in lines_with_dash[2]

    assert str(ok) not in msg


# ── Test 3 ──────────────────────────────────────────────────────────────────


def test_existing_relative_path_passes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lists").mkdir()
    (tmp_path / "lists" / "ok.list").write_text("https://example.com\n")
    cfg = _cfg(["lists/ok.list"])
    assert refuse_missing_input_lists([cfg]) is None


# ── Test 4 ──────────────────────────────────────────────────────────────────


def test_existing_absolute_path_passes_from_any_cwd(monkeypatch, tmp_path):
    ok = tmp_path / "ok.list"
    ok.write_text("https://example.com\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    cfg = _cfg([str(ok)])
    assert refuse_missing_input_lists([cfg]) is None


# ── Test 5 ──────────────────────────────────────────────────────────────────


def test_relative_path_resolves_from_cwd_not_from_config_dir(monkeypatch, tmp_path):
    cfgdir = tmp_path / "cfgdir"
    cfgdir.mkdir()
    (cfgdir / "x.list").write_text("https://example.com\n")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    cfg = _cfg(["x.list"], config_path=str(cfgdir / "a.yaml"))
    with pytest.raises(ValueError) as exc_info:
        refuse_missing_input_lists([cfg])
    assert "x.list" in str(exc_info.value)


# ── Test 6 ──────────────────────────────────────────────────────────────────


def test_undecodable_list_passes_and_is_still_skipped_with_warning(tmp_path, caplog):
    binary = tmp_path / "binary.list"
    binary.write_bytes(b"\xff\xfe\x00git-https://x\n")
    good = tmp_path / "good.list"
    good.write_text("git-https://github.com/org/repo\n")

    cfg = _cfg([str(binary), str(good)])
    assert refuse_missing_input_lists([cfg]) is None

    mgr = object.__new__(ConfigurationManager)
    mgr.configs = [
        {
            "name": "test",
            "data_manager": {
                "sources": {"links": {"input_lists": [str(binary), str(good)]}}
            },
        }
    ]
    with caplog.at_level(logging.WARNING):
        enabled = mgr.get_enabled_sources()
    assert enabled == ["git"]
    assert str(binary) in caplog.text


# ── Test 7 ──────────────────────────────────────────────────────────────────


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_permission_denied_list_passes_and_is_still_skipped_with_warning(
    tmp_path, caplog
):
    locked = tmp_path / "locked.list"
    locked.write_text("sso-https://example.org/\n")
    locked.chmod(0)
    good = tmp_path / "good.list"
    good.write_text("git-https://github.com/org/repo\n")

    try:
        cfg = _cfg([str(locked), str(good)])
        assert refuse_missing_input_lists([cfg]) is None

        mgr = object.__new__(ConfigurationManager)
        mgr.configs = [
            {
                "name": "test",
                "data_manager": {
                    "sources": {"links": {"input_lists": [str(locked), str(good)]}}
                },
            }
        ]
        with caplog.at_level(logging.WARNING):
            enabled = mgr.get_enabled_sources()
        assert enabled == ["git"]
        assert str(locked) in caplog.text
    finally:
        locked.chmod(0o644)


# ── Test 8 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cfg_body",
    [
        {},
        {"data_manager": None},
        {"data_manager": {"sources": None}},
        {"data_manager": {"sources": {"links": True}}},
        {"data_manager": {"sources": {"links": {"input_lists": None}}}},
        {"data_manager": {"sources": {"links": {"input_lists": []}}}},
        {"data_manager": {"sources": {"links": {"input_lists": "a-string.list"}}}},
        {"data_manager": {"sources": {"links": {"input_lists": [None, 987654]}}}},
    ],
)
def test_entries_and_shapes_that_are_not_paths_are_ignored(cfg_body):
    cfg = {**cfg_body, "_config_path": "cfg/a.yaml"}
    assert refuse_missing_input_lists([cfg]) is None
