"""refuse_missing_input_lists tells a missing path from an inaccessible one (#617).

Spec: openspec/changes/fix-issue-617-missing-input-list-fails-closed/specs/cli-source-registry/spec.md
Requirement: a path that does not exist stops the command; a path that exists but
cannot be read keeps the warn-and-skip of get_enabled_sources() (#636).

os.path.exists() returns False for a permission error, so the first form of the check
refused an existing list under a directory the user cannot traverse and called it
missing (Codex P2 on PR #781, 2026-11-07). A sibling file, so nothing is appended after
the last test of test_refuse_missing_input_lists.py.
"""

import logging
import os

import pytest

from src.cli.managers.config_manager import ConfigurationManager
from src.cli.source_registry import refuse_missing_input_lists


def _cfg(paths, config_path="cfg/a.yaml"):
    return {
        "_config_path": config_path,
        "data_manager": {"sources": {"links": {"input_lists": list(paths)}}},
    }


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_list_under_an_untraversable_directory_passes_and_is_still_skipped(
    tmp_path, caplog
):
    locked_dir = tmp_path / "locked"
    locked_dir.mkdir()
    hidden = locked_dir / "hidden.list"
    hidden.write_text("sso-https://example.org/\n")
    good = tmp_path / "good.list"
    good.write_text("git-https://github.com/org/repo\n")
    locked_dir.chmod(0)
    try:
        assert not os.path.exists(hidden), "the setup must make exists() say False"
        assert refuse_missing_input_lists([_cfg([str(hidden), str(good)])]) is None

        mgr = object.__new__(ConfigurationManager)
        mgr.configs = [
            {
                "name": "test",
                "data_manager": {
                    "sources": {"links": {"input_lists": [str(hidden), str(good)]}}
                },
            }
        ]
        with caplog.at_level(logging.WARNING):
            enabled = mgr.get_enabled_sources()
        assert enabled == ["git"]
        assert str(hidden) in caplog.text
    finally:
        locked_dir.chmod(0o755)


def test_a_missing_file_is_still_refused(tmp_path):
    absent = tmp_path / "absent.list"
    with pytest.raises(ValueError) as exc:
        refuse_missing_input_lists([_cfg([str(absent)])])
    assert str(absent) in str(exc.value)


def test_a_path_through_a_regular_file_is_refused(tmp_path):
    plain = tmp_path / "plain.list"
    plain.write_text("https://example.org/\n")
    through = plain / "inner.list"
    with pytest.raises(ValueError) as exc:
        refuse_missing_input_lists([_cfg([str(through)])])
    assert str(through) in str(exc.value)
