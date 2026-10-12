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

from src.cli.managers.templates_manager import TemplateManager


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
