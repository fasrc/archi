"""`get_enabled_sources` warns about and skips an unreadable input list (#460).

The spec says a configured list path that is not a readable file is warned
about and skipped, not raised. A missing path was already handled; these
cover a file that exists but cannot be read or decoded, and a list entry that
is not a path at all.
"""

import logging
import os

import pytest

from src.cli.managers.config_manager import ConfigurationManager


def _manager(input_lists):
    mgr = object.__new__(ConfigurationManager)
    mgr.configs = [
        {
            "name": "test",
            "data_manager": {"sources": {"links": {"input_lists": input_lists}}},
        }
    ]
    return mgr


def _good_list(tmp_path):
    good = tmp_path / "good.list"
    good.write_text("git-https://github.com/org/repo\n")
    return str(good)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_permission_denied_list_is_skipped_with_warning(tmp_path, caplog):
    locked = tmp_path / "locked.list"
    locked.write_text("sso-https://example.org/\n")
    locked.chmod(0)
    try:
        with caplog.at_level(logging.WARNING):
            enabled = _manager(
                [str(locked), _good_list(tmp_path)]
            ).get_enabled_sources()
    finally:
        locked.chmod(0o644)
    assert enabled == ["git"]
    assert str(locked) in caplog.text


def test_non_utf8_list_is_skipped_with_warning(tmp_path, caplog):
    binary = tmp_path / "binary.list"
    binary.write_bytes(b"\xff\xfe\x00git-https://x\n")
    with caplog.at_level(logging.WARNING):
        enabled = _manager([str(binary), _good_list(tmp_path)]).get_enabled_sources()
    assert enabled == ["git"]
    assert str(binary) in caplog.text


@pytest.mark.parametrize("bad_entry", [None, 5])
def test_non_path_list_entry_is_skipped_with_warning(tmp_path, caplog, bad_entry):
    with caplog.at_level(logging.WARNING):
        enabled = _manager([bad_entry, _good_list(tmp_path)]).get_enabled_sources()
    assert enabled == ["git"]
    assert repr(bad_entry) in caplog.text
