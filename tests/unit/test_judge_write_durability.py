"""Directory fsyncs at the bundle and result markers (adversarial review, PR #747).

A marker is a promise that the files before it are complete. The file data is
fsynced, but a new name lives in its folder, so the folder is fsynced too:
after the bundle files and before BUNDLE_COMPLETE, and after the result JSON
and before RESULT_COMPLETE.
"""

from __future__ import annotations

import os

import pytest

import src.utils.judge_bundle as jb
from src.utils.judge_bundle import write_judge_bundle, write_result_complete


def _write_bundle(root):
    return write_judge_bundle(
        root,
        result_basename="bench-20260101_000000",
        arm_index=0,
        arm_count=1,
        rows=[],
        keys=[],
        enabled_metrics=[],
        ragas_settings={},
        verbosity=0,
        code_version={"digest": "sha256:" + "a" * 64},
        package_digest="sha256:" + "b" * 64,
    )


def test_bundle_folder_is_fsynced_before_and_after_bundle_complete(
    tmp_path, monkeypatch
):
    events = []
    real_write = jb._write_file
    real_fsync_dir = jb._fsync_dir

    def _recording_write(path, data):
        events.append(("write", path.name))
        real_write(path, data)

    def _recording_fsync_dir(folder):
        events.append(("fsync_dir", folder.name))
        real_fsync_dir(folder)

    monkeypatch.setattr(jb, "_write_file", _recording_write)
    monkeypatch.setattr(jb, "_fsync_dir", _recording_fsync_dir)

    _write_bundle(tmp_path / "bundles")

    assert events == [
        ("fsync_dir", "bundles"),
        ("write", "rows.jsonl"),
        ("write", "manifest.json"),
        ("fsync_dir", "bench-20260101_000000-arm0"),
        ("write", "BUNDLE_COMPLETE"),
        ("fsync_dir", "bench-20260101_000000-arm0"),
    ]


def test_result_json_and_folder_are_fsynced_before_the_marker(tmp_path, monkeypatch):
    result = tmp_path / "bench-20260101_000000.json"
    result.write_text("{}")
    marker = tmp_path / "bench-20260101_000000.RESULT_COMPLETE"
    events = []
    real_fsync = os.fsync

    def _recording_fsync(fd):
        inode = os.fstat(fd).st_ino
        name = "result" if inode == result.stat().st_ino else "other"
        events.append(("fsync", name, marker.exists()))
        real_fsync(fd)

    def _recording_fsync_dir(folder):
        # Records only: the real one calls the patched os.fsync.
        events.append(("fsync_dir", folder == tmp_path, marker.exists()))

    monkeypatch.setattr(jb.os, "fsync", _recording_fsync)
    monkeypatch.setattr(jb, "_fsync_dir", _recording_fsync_dir)

    write_result_complete(tmp_path, "bench-20260101_000000")

    assert events == [
        ("fsync", "result", False),
        ("fsync_dir", True, False),
        ("fsync", "other", True),
        ("fsync_dir", True, True),
    ]
    assert marker.read_bytes() == b""


def test_no_marker_without_the_result_json(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_result_complete(tmp_path, "bench-20260101_000000")

    assert list(tmp_path.iterdir()) == []


def test_fsync_dir_syncs_a_real_folder(tmp_path):
    jb._fsync_dir(tmp_path)
