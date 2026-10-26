"""Tests for the one-off script `scripts/maintenance/repair_scraper_markers.py` (#640).

Spec: openspec/changes/fix-issue-640-scraper-marker-survives-refetch/design.md (D8)

The script is a thin wrapper over
`src.data_manager.collectors.utils.scraper_marker_repair`: it parses arguments, reads
the input lists before it connects, prints the plan, and closes the connection. These
tests drive `main()` with a fake `psycopg2.connect`, so no database is needed.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import psycopg2
import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "maintenance"
    / "repair_scraper_markers.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("repair_scraper_markers", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_connect(monkeypatch):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [("h1", "https://x/lb/12")]
    cursor.rowcount = 1
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return conn

    monkeypatch.setattr(psycopg2, "connect", fake)
    return {"conn": conn, "cursor": cursor, "calls": calls}


@pytest.fixture
def list_path(tmp_path):
    path = tmp_path / "sources.list"
    path.write_text("elog-https://x/lb\n", encoding="utf-8")
    return str(path)


def test_dry_run_is_the_default(fake_connect, list_path, capsys):
    script = _load_script()
    assert script.main(["--pg-dsn", "dsn", "--input-list", list_path]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "dry run: nothing written; rerun with --apply to write"
    executed = [str(c.args[0]) for c in fake_connect["cursor"].execute.call_args_list]
    assert not [sql for sql in executed if "UPDATE" in sql]
    fake_connect["conn"].commit.assert_not_called()
    fake_connect["conn"].close.assert_called_once()


def test_apply_writes_and_reports(fake_connect, list_path, capsys):
    script = _load_script()
    argv = ["--pg-dsn", "dsn", "--input-list", list_path, "--apply"]
    assert script.main(argv) == 0
    assert "wrote 1 of 1 marker(s)" in capsys.readouterr().out.splitlines()
    fake_connect["conn"].commit.assert_called_once()
    fake_connect["conn"].close.assert_called_once()


def test_missing_input_list_exits_2_before_connecting(fake_connect, tmp_path, capsys):
    script = _load_script()
    missing = str(tmp_path / "absent.list")
    assert script.main(["--pg-dsn", "dsn", "--input-list", missing]) == 2
    assert capsys.readouterr().err.startswith("error: cannot read input list:")
    assert fake_connect["calls"] == []


def test_dry_run_and_apply_are_mutually_exclusive(fake_connect, list_path):
    script = _load_script()
    argv = ["--pg-dsn", "dsn", "--input-list", list_path, "--dry-run", "--apply"]
    with pytest.raises(SystemExit) as exc:
        script.main(argv)
    assert exc.value.code == 2
    assert fake_connect["calls"] == []
