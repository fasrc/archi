"""publish.py never replaces a file in a queue folder (adversarial review, PR #747).

Two publishers can meet at one queue folder before READY exists. If one could
replace a file after the other created READY, a reader could see a READY bundle
whose files do not match. So every queue file is created with link_exclusive,
and an existing file must already hold the same bytes.
"""

from __future__ import annotations

import json
import os

import pytest

from scripts.benchmarking.judge import publish
from src.utils.judge_bundle import write_judge_bundle

_BASENAME = "bench-20260101_000000"


def _bundle(out_dir):
    rows = [
        {
            "user_input": "question 1",
            "retrieved_contexts": ["context 1"],
            "response": "answer 1",
            "reference": "reference 1",
        }
    ]
    return write_judge_bundle(
        out_dir / "judge_bundles",
        result_basename=_BASENAME,
        arm_index=0,
        arm_count=1,
        rows=rows,
        keys=["q1"],
        enabled_metrics=["faithfulness"],
        ragas_settings={"evaluator_model": "m", "evaluator_model_revision": "r"},
        verbosity=0,
        code_version={"digest": "sha256:" + "a" * 64},
        package_digest="sha256:" + "b" * 64,
    )


@pytest.fixture()
def dirs(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    queue = tmp_path / "queue"
    queue.mkdir()
    return out_dir, queue


def _run(out_dir, queue):
    return publish.main(["--out-dir", str(out_dir), "--queue", str(queue)])


def test_publish_never_calls_os_replace(dirs, monkeypatch):
    out_dir, queue = dirs
    _bundle(out_dir)

    def _no_replace(*args, **kwargs):
        raise AssertionError("publish must never replace a queue file")

    monkeypatch.setattr(os, "replace", _no_replace)
    monkeypatch.setattr(os, "rename", _no_replace)

    assert _run(out_dir, queue) == 0

    assert (queue / f"{_BASENAME}-arm0" / "READY").exists()


def test_other_publishers_file_is_kept_and_no_ready(dirs, capsys):
    out_dir, queue = dirs
    _bundle(out_dir)
    dst = queue / f"{_BASENAME}-arm0"
    dst.mkdir()
    (dst / "rows.jsonl").write_bytes(b'{"key": "other"}\n')

    assert _run(out_dir, queue) == 1

    assert (dst / "rows.jsonl").read_bytes() == b'{"key": "other"}\n'
    assert sorted(p.name for p in dst.iterdir()) == ["rows.jsonl"]
    assert "rows.jsonl" in capsys.readouterr().err


def test_identical_leftover_file_from_a_crashed_run_is_accepted(dirs):
    out_dir, queue = dirs
    _bundle(out_dir)
    src = out_dir / "judge_bundles" / f"{_BASENAME}-arm0"
    dst = queue / f"{_BASENAME}-arm0"
    dst.mkdir()
    (dst / "rows.jsonl").write_bytes((src / "rows.jsonl").read_bytes())

    assert _run(out_dir, queue) == 0

    assert sorted(p.name for p in dst.iterdir()) == [
        "READY",
        "manifest.json",
        "rows.jsonl",
    ]
    assert json.loads((dst / "manifest.json").read_text())["row_count"] == 1
