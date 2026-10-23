"""The judge bundle writer of a deferred arm (task 4.1, design D5)."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict

import pytest

import src.utils.judge_bundle as jb
from src.utils.benchmark_schema import RAGAS_METRIC_NAMES, ragas_run_config_kwargs
from src.utils.judge_bundle import write_judge_bundle

_CODE_VERSION = {"digest": "sha256:" + "a" * 64, "source": "content digest", "x": 1}
_PACKAGE_DIGEST = "sha256:" + "b" * 64
_SETTINGS = {
    "timeout": 90,
    "max_workers": 4,
    "batch_size": 4,
    "embedding_model": "OpenAI",
    "evaluator_model": "judge/model",
    "evaluator_model_revision": "0123abc",
}
_MANIFEST_KEYS = {
    "bundle_format",
    "result_basename",
    "arm_index",
    "arm_count",
    "row_count",
    "rows_sha256",
    "enabled_metrics",
    "ragas_run_config_kwargs",
    "batch_size",
    "embedding_model",
    "judge_model",
    "code_version",
    "package_digest",
}


def _row(i):
    return {
        "user_input": f"question {i}",
        "retrieved_contexts": [f"context {i} " + "x" * 5000, f"second context {i}"],
        "response": f"answer {i}",
        "reference": f"reference {i}",
    }


_ROWS = [_row(1), _row(2), _row(3)]
_KEYS = ["q1", "q2", "q3"]


def _write(root, *, rows=_ROWS, keys=_KEYS, settings=_SETTINGS, **overrides):
    kwargs: Dict[str, Any] = dict(
        result_basename="bench-20260101_000000",
        arm_index=0,
        arm_count=2,
        rows=rows,
        keys=keys,
        enabled_metrics=["faithfulness", "unknown_metric", "answer_relevancy"],
        ragas_settings=settings,
        verbosity=0,
        code_version=_CODE_VERSION,
        package_digest=_PACKAGE_DIGEST,
    )
    kwargs.update(overrides)
    return write_judge_bundle(root, **kwargs)


def _lines(folder):
    return (folder / "rows.jsonl").read_bytes().decode("ascii").splitlines()


def test_rows_jsonl_holds_each_row_with_its_key(tmp_path):
    out = _write(tmp_path / "bundles")

    folder = tmp_path / "bundles" / "bench-20260101_000000-arm0"
    assert out["folder"] == "bench-20260101_000000-arm0"
    assert sorted(p.name for p in folder.iterdir()) == [
        "BUNDLE_COMPLETE",
        "manifest.json",
        "rows.jsonl",
    ]
    lines = _lines(folder)
    assert len(lines) == 3
    records = [json.loads(line) for line in lines]
    assert [r.pop("key") for r in records] == _KEYS
    assert records == _ROWS


def test_manifest_has_exactly_the_d5_keys_and_values(tmp_path):
    out = _write(tmp_path)

    folder = tmp_path / "bench-20260101_000000-arm0"
    manifest = json.loads((folder / "manifest.json").read_text())
    expected_digest = (
        "sha256:" + hashlib.sha256((folder / "rows.jsonl").read_bytes()).hexdigest()
    )
    assert set(manifest) == _MANIFEST_KEYS
    assert manifest["bundle_format"] == 1
    assert manifest["result_basename"] == "bench-20260101_000000"
    assert manifest["arm_index"] == 0
    assert manifest["arm_count"] == 2
    assert manifest["row_count"] == 3
    assert manifest["rows_sha256"] == expected_digest
    assert out == {"folder": folder.name, "digest": expected_digest}
    assert manifest["enabled_metrics"] == ["answer_relevancy", "faithfulness"]
    assert manifest["ragas_run_config_kwargs"] == ragas_run_config_kwargs(_SETTINGS, 0)
    assert manifest["batch_size"] == 4
    assert manifest["embedding_model"] == "OpenAI"
    assert manifest["judge_model"] == {"id": "judge/model", "revision": "0123abc"}
    assert manifest["code_version"] == _CODE_VERSION
    assert manifest["package_digest"] == _PACKAGE_DIGEST


def test_enabled_metrics_follow_ragas_metric_names_order(tmp_path):
    reversed_names = list(reversed(RAGAS_METRIC_NAMES))
    _write(tmp_path, enabled_metrics=reversed_names + ["nope"])

    manifest = json.loads(
        (tmp_path / "bench-20260101_000000-arm0" / "manifest.json").read_text()
    )
    assert manifest["enabled_metrics"] == list(RAGAS_METRIC_NAMES)


def test_batch_size_false_records_none(tmp_path):
    _write(tmp_path, settings={**_SETTINGS, "batch_size": False}, verbosity=4)

    manifest = json.loads(
        (tmp_path / "bench-20260101_000000-arm0" / "manifest.json").read_text()
    )
    assert manifest["batch_size"] is None
    assert manifest["ragas_run_config_kwargs"]["log_tenacity"] is True


def test_bundle_complete_comes_after_both_files_are_complete(tmp_path, monkeypatch):
    events = []
    real_write = jb._write_file
    real_fsync = os.fsync
    folder = tmp_path / "bench-20260101_000000-arm0"

    def _recording_write(path, data):
        names = sorted(p.name for p in folder.iterdir())
        events.append(("write", path.name, names))
        real_write(path, data)

    def _recording_fsync(fd):
        events.append(("fsync",))
        real_fsync(fd)

    monkeypatch.setattr(jb, "_write_file", _recording_write)
    monkeypatch.setattr(jb.os, "fsync", _recording_fsync)

    _write(tmp_path)

    # Each file is fsynced after its write; the bare fsyncs before the first
    # write, before BUNDLE_COMPLETE and at the end are the folder fsyncs
    # (tests/unit/test_judge_write_durability.py names them).
    assert events == [
        ("fsync",),
        ("write", "rows.jsonl", []),
        ("fsync",),
        ("write", "manifest.json", ["rows.jsonl"]),
        ("fsync",),
        ("fsync",),
        ("write", "BUNDLE_COMPLETE", ["manifest.json", "rows.jsonl"]),
        ("fsync",),
        ("fsync",),
    ]
    assert (folder / "BUNDLE_COMPLETE").read_bytes() == b""


def test_second_call_for_the_same_folder_raises_and_changes_nothing(tmp_path):
    _write(tmp_path)
    folder = tmp_path / "bench-20260101_000000-arm0"
    before = {p.name: p.read_bytes() for p in folder.iterdir()}

    with pytest.raises(FileExistsError):
        _write(tmp_path, rows=[_row(9)] * 3)

    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_mismatched_rows_and_keys_raise_and_write_nothing(tmp_path):
    with pytest.raises(ValueError):
        _write(tmp_path / "bundles", keys=["q1", "q2"])

    assert not (tmp_path / "bundles" / "bench-20260101_000000-arm0").exists()


def test_row_missing_a_field_raises_and_writes_nothing(tmp_path):
    broken = [{k: v for k, v in _row(1).items() if k != "reference"}]

    with pytest.raises(KeyError):
        _write(tmp_path / "bundles", rows=broken, keys=["q1"])

    assert not (tmp_path / "bundles" / "bench-20260101_000000-arm0").exists()


def test_zero_rows_give_an_empty_rows_file_and_a_complete_bundle(tmp_path):
    out = _write(tmp_path, rows=[], keys=[])

    folder = tmp_path / "bench-20260101_000000-arm0"
    assert (folder / "rows.jsonl").read_bytes() == b""
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["row_count"] == 0
    assert manifest["rows_sha256"] == "sha256:" + hashlib.sha256(b"").hexdigest()
    assert out["digest"] == manifest["rows_sha256"]
    assert (folder / "BUNDLE_COMPLETE").exists()


def test_lone_surrogate_and_non_ascii_text_round_trip(tmp_path):
    row = {
        "user_input": "Où est le cluster?",
        "retrieved_contexts": ["naïve ☃ context"],
        "response": "Réponse — 日本語",
        "reference": "abc\ud83d",
    }

    _write(tmp_path, rows=[row], keys=["q1"])

    raw = (tmp_path / "bench-20260101_000000-arm0" / "rows.jsonl").read_bytes()
    assert raw.isascii()
    record = json.loads(raw.decode("ascii"))
    assert record.pop("key") == "q1"
    assert record == row


def test_arm_index_names_the_folder(tmp_path):
    out = _write(tmp_path, arm_index=1)

    assert out["folder"] == "bench-20260101_000000-arm1"
    manifest = json.loads(
        (tmp_path / "bench-20260101_000000-arm1" / "manifest.json").read_text()
    )
    assert manifest["arm_index"] == 1
