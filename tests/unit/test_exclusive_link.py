"""The no-replace link primitive (task 6.1, design D10)."""

from __future__ import annotations

import ast
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import src.utils.exclusive_link as el
from src.utils.exclusive_link import link_exclusive


def _tmp_files(folder):
    return sorted(p.name for p in Path(folder).iterdir() if p.name.startswith(".tmp."))


def test_new_name_gets_the_full_bytes(tmp_path):
    data = b"payload " * 4096
    target = tmp_path / "READY"

    assert link_exclusive(target, data) is True

    assert target.read_bytes() == data
    assert _tmp_files(tmp_path) == []


def test_second_writer_loses_and_the_first_bytes_stay(tmp_path):
    target = tmp_path / "READY"
    assert link_exclusive(target, b"first") is True

    assert link_exclusive(target, b"second") is False

    assert target.read_bytes() == b"first"
    assert _tmp_files(tmp_path) == []


def test_link_that_succeeded_but_reported_eexist_is_a_win(tmp_path, monkeypatch):
    real_link = os.link

    def _resent_link(src, dst):
        real_link(src, dst)
        raise FileExistsError(dst)

    monkeypatch.setattr(el.os, "link", _resent_link)
    target = tmp_path / "READY"

    assert link_exclusive(target, b"mine") is True

    assert target.read_bytes() == b"mine"
    assert _tmp_files(tmp_path) == []


def test_name_never_exists_without_its_full_content(tmp_path, monkeypatch):
    data = b"complete content " * 1000
    real_link = os.link
    real_fsync = os.fsync
    fsynced_inodes = []
    links = []

    def _recording_fsync(fd):
        fsynced_inodes.append(os.fstat(fd).st_ino)
        real_fsync(fd)

    def _checking_link(src, dst):
        assert Path(src).read_bytes() == data
        assert os.stat(src).st_ino in fsynced_inodes
        assert not Path(dst).exists()
        links.append(dst)
        real_link(src, dst)

    monkeypatch.setattr(el.os, "fsync", _recording_fsync)
    monkeypatch.setattr(el.os, "link", _checking_link)

    assert link_exclusive(tmp_path / "READY", data) is True
    assert len(links) == 1


def test_eight_racing_writers_give_exactly_one_winner(tmp_path):
    target = tmp_path / "READY"
    payloads = [f"writer {i} ".encode() * 2000 for i in range(8)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(link_exclusive, target, p) for p in payloads]
        results = [f.result(timeout=10) for f in futures]

    assert results.count(True) == 1
    winner = results.index(True)
    assert target.read_bytes() == payloads[winner]
    assert _tmp_files(tmp_path) == []


def test_module_imports_nothing_from_src():
    tree = ast.parse(Path(el.__file__).read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    assert imported, "the module must import its standard library modules"
    assert [name for name in imported if name.split(".")[0] == "src"] == []
