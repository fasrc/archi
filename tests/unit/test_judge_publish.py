"""The judge bundle publish script (task 6.2, design D11, parent task 2.6)."""

from __future__ import annotations

import hashlib
import json

import pytest

from scripts.benchmarking.judge import publish
from src.utils.judge_bundle import write_judge_bundle

_BASENAME = "bench-20260101_000000"
_SETTINGS = {
    "batch_size": False,
    "embedding_model": "OpenAI",
    "evaluator_model": "judge/model",
    "evaluator_model_revision": "0123abc",
}


def _row(i):
    return {
        "user_input": f"question {i}",
        "retrieved_contexts": [f"context {i}"],
        "response": f"answer {i}",
        "reference": f"reference {i}",
    }


def _bundle(out_dir, *, arm_index=0, arm_count=1, rows=None, basename=_BASENAME):
    rows = [_row(1), _row(2)] if rows is None else rows
    return write_judge_bundle(
        out_dir / "judge_bundles",
        result_basename=basename,
        arm_index=arm_index,
        arm_count=arm_count,
        rows=rows,
        keys=[f"q{i}" for i in range(len(rows))],
        enabled_metrics=["faithfulness"],
        ragas_settings=_SETTINGS,
        verbosity=0,
        code_version={"digest": "sha256:" + "a" * 64},
        package_digest="sha256:" + "b" * 64,
    )


def _result(out_dir, bundles, *, status="pending", complete=True, basename=_BASENAME):
    entries = [{"judge_status": status, "judge_bundle": b} for b in bundles]
    path = out_dir / f"{basename}.json"
    path.write_text(json.dumps({"benchmarking_results": entries, "metadata": {}}))
    if complete:
        (out_dir / f"{basename}.RESULT_COMPLETE").write_bytes(b"")
    return path


def _run(out_dir, queue):
    return publish.main(["--out-dir", str(out_dir), "--queue", str(queue)])


def _tree(root):
    """Every file under *root* with its bytes and mtime, for a no-change check."""
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture()
def dirs(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    queue = tmp_path / "queue"
    queue.mkdir()
    return out_dir, queue


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def test_queue_is_required(dirs):
    out_dir, _ = dirs
    with pytest.raises(SystemExit) as exc_info:
        publish.main(["--out-dir", str(out_dir)])
    assert exc_info.value.code == 2


def test_nothing_to_do_exits_zero(dirs):
    out_dir, queue = dirs
    assert _run(out_dir, queue) == 0
    assert list(queue.iterdir()) == []


# ---------------------------------------------------------------------------
# Step 1: bundles
# ---------------------------------------------------------------------------


def test_complete_bundle_becomes_ready_with_equal_files(dirs, monkeypatch):
    out_dir, queue = dirs
    _bundle(out_dir)
    real_link = publish.link_exclusive
    ready_calls = []

    def _recording_link(path, data):
        ready_calls.append(sorted(p.name for p in path.parent.iterdir()))
        return real_link(path, data)

    monkeypatch.setattr(publish, "link_exclusive", _recording_link)

    assert _run(out_dir, queue) == 0

    src = out_dir / "judge_bundles" / f"{_BASENAME}-arm0"
    dst = queue / f"{_BASENAME}-arm0"
    assert sorted(p.name for p in dst.iterdir()) == [
        "READY",
        "manifest.json",
        "rows.jsonl",
    ]
    assert (dst / "rows.jsonl").read_bytes() == (src / "rows.jsonl").read_bytes()
    assert (dst / "manifest.json").read_bytes() == (src / "manifest.json").read_bytes()
    assert (dst / "READY").read_bytes() == b""
    assert ready_calls == [["manifest.json", "rows.jsonl"]]


def test_bundle_without_bundle_complete_is_not_copied(dirs):
    out_dir, queue = dirs
    _bundle(out_dir)
    (out_dir / "judge_bundles" / f"{_BASENAME}-arm0" / "BUNDLE_COMPLETE").unlink()

    assert _run(out_dir, queue) == 0

    assert list(queue.iterdir()) == []


def test_corrupted_copy_exits_non_zero_with_no_ready(dirs, monkeypatch, capsys):
    out_dir, queue = dirs
    _bundle(out_dir)
    real_copy = publish._copy_file

    def _corrupting_copy(src, dst):
        real_copy(src, dst)
        if dst.name == "rows.jsonl":
            data = bytearray(dst.read_bytes())
            data[0] ^= 1
            dst.write_bytes(bytes(data))

    monkeypatch.setattr(publish, "_copy_file", _corrupting_copy)

    assert _run(out_dir, queue) == 1

    assert not (queue / f"{_BASENAME}-arm0" / "READY").exists()
    assert "conflict" in capsys.readouterr().err


def test_copy_that_raises_leaves_no_ready_and_the_next_run_completes(dirs, monkeypatch):
    out_dir, queue = dirs
    _bundle(out_dir)
    real_copy = publish._copy_file

    def _crashing_copy(src, dst):
        if dst.name == "manifest.json":
            raise OSError("disk went away")
        real_copy(src, dst)

    monkeypatch.setattr(publish, "_copy_file", _crashing_copy)
    with pytest.raises(OSError, match="disk went away"):
        _run(out_dir, queue)
    dst = queue / f"{_BASENAME}-arm0"
    assert (dst / "rows.jsonl").exists()
    assert not (dst / "READY").exists()

    monkeypatch.setattr(publish, "_copy_file", real_copy)
    assert _run(out_dir, queue) == 0

    assert sorted(p.name for p in dst.iterdir()) == [
        "READY",
        "manifest.json",
        "rows.jsonl",
    ]


def test_second_run_over_a_ready_bundle_changes_nothing(dirs):
    out_dir, queue = dirs
    _bundle(out_dir)
    assert _run(out_dir, queue) == 0
    before = (_tree(queue), _tree(out_dir))

    assert _run(out_dir, queue) == 0

    assert (_tree(queue), _tree(out_dir)) == before


def test_ready_queue_folder_with_a_different_digest_is_a_conflict(dirs, capsys):
    out_dir, queue = dirs
    _bundle(out_dir)
    assert _run(out_dir, queue) == 0
    dst = queue / f"{_BASENAME}-arm0"
    manifest = json.loads((dst / "manifest.json").read_text())
    manifest["rows_sha256"] = "sha256:" + "f" * 64
    (dst / "manifest.json").write_text(json.dumps(manifest))
    before = (_tree(queue), _tree(out_dir))

    assert _run(out_dir, queue) == 1

    assert (_tree(queue), _tree(out_dir)) == before
    assert f"{_BASENAME}-arm0" in capsys.readouterr().err


def test_folder_name_that_does_not_match_the_manifest_is_a_conflict(dirs, capsys):
    out_dir, queue = dirs
    _bundle(out_dir)
    root = out_dir / "judge_bundles"
    (root / f"{_BASENAME}-arm0").rename(root / "renamed-arm0")

    assert _run(out_dir, queue) == 1

    assert list(queue.iterdir()) == []
    assert "renamed-arm0" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Step 2: the pending result
# ---------------------------------------------------------------------------


def test_result_without_result_complete_is_not_copied(dirs):
    out_dir, queue = dirs
    bundle = _bundle(out_dir)
    _result(out_dir, [bundle], complete=False)

    assert _run(out_dir, queue) == 0

    assert not (queue / "results").exists()


def test_result_that_does_not_load_is_not_copied(dirs):
    out_dir, queue = dirs
    bundle = _bundle(out_dir)
    path = _result(out_dir, [bundle])
    text = path.read_text()
    path.write_text(text[: len(text) // 2])

    assert _run(out_dir, queue) == 0

    assert not (queue / "results").exists()


def test_complete_pending_result_is_copied(dirs):
    out_dir, queue = dirs
    bundles = [_bundle(out_dir, arm_index=i, arm_count=2) for i in (0, 1)]
    path = _result(out_dir, bundles)

    assert _run(out_dir, queue) == 0

    copied = queue / "results" / f"{_BASENAME}.json"
    assert copied.read_bytes() == path.read_bytes()
    assert sorted(p.name for p in (queue / "results").iterdir()) == [
        f"{_BASENAME}.json"
    ]


def test_result_with_a_wrong_digest_is_a_conflict(dirs, capsys):
    out_dir, queue = dirs
    bundle = _bundle(out_dir)
    _result(out_dir, [{**bundle, "digest": "sha256:" + "e" * 64}])

    assert _run(out_dir, queue) == 1

    assert not (queue / "results").exists()
    assert "digest" in capsys.readouterr().err


def test_result_whose_arm_is_not_pending_is_a_conflict(dirs, capsys):
    out_dir, queue = dirs
    bundle = _bundle(out_dir)
    _result(out_dir, [bundle], status="scored")

    assert _run(out_dir, queue) == 1

    assert not (queue / "results").exists()
    assert "pending" in capsys.readouterr().err


def test_result_with_the_wrong_arm_count_is_a_conflict(dirs, capsys):
    out_dir, queue = dirs
    bundle = _bundle(out_dir)
    _result(out_dir, [bundle, bundle])

    assert _run(out_dir, queue) == 1

    assert not (queue / "results").exists()
    assert "arm" in capsys.readouterr().err


def test_result_with_a_missing_arm_bundle_is_a_conflict(dirs, capsys):
    out_dir, queue = dirs
    bundle = _bundle(out_dir, arm_index=0, arm_count=2)
    _result(out_dir, [bundle, {"folder": f"{_BASENAME}-arm1", "digest": "x"}])

    assert _run(out_dir, queue) == 1

    assert not (queue / "results").exists()
    err = capsys.readouterr().err
    assert "missing" in err
    assert "1" in err.split("missing", 1)[1]


def test_result_already_queued_with_the_same_bytes_changes_nothing(dirs):
    out_dir, queue = dirs
    bundle = _bundle(out_dir)
    _result(out_dir, [bundle])
    assert _run(out_dir, queue) == 0
    before = _tree(queue)

    assert _run(out_dir, queue) == 0

    assert _tree(queue) == before


def test_result_queued_with_different_bytes_is_a_conflict(dirs, capsys):
    out_dir, queue = dirs
    bundle = _bundle(out_dir)
    _result(out_dir, [bundle])
    assert _run(out_dir, queue) == 0
    (queue / "results" / f"{_BASENAME}.json").write_text("{}")
    before = _tree(queue)

    assert _run(out_dir, queue) == 1

    assert _tree(queue) == before
    assert "results" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Step 3: the judged copy-back
# ---------------------------------------------------------------------------


def test_judged_result_is_copied_back_only_after_every_arm_is_merged(dirs):
    out_dir, queue = dirs
    bundles = [_bundle(out_dir, arm_index=i, arm_count=2) for i in (0, 1)]
    _result(out_dir, bundles)
    assert _run(out_dir, queue) == 0
    judged = queue / "results" / f"{_BASENAME}.judged.json"
    judged.write_text(json.dumps({"benchmarking_results": [], "judged": True}))
    (queue / f"{_BASENAME}-arm0" / "MERGED").write_bytes(b"")

    assert _run(out_dir, queue) == 0
    assert not (out_dir / f"{_BASENAME}.judged.json").exists()

    (queue / f"{_BASENAME}-arm1" / "MERGED").write_bytes(b"")
    assert _run(out_dir, queue) == 0

    assert (out_dir / f"{_BASENAME}.judged.json").read_bytes() == judged.read_bytes()


def test_judged_copy_with_different_bytes_is_a_conflict(dirs, capsys):
    out_dir, queue = dirs
    bundle = _bundle(out_dir)
    _result(out_dir, [bundle])
    assert _run(out_dir, queue) == 0
    (queue / "results" / f"{_BASENAME}.judged.json").write_text('{"a": 1}')
    (queue / f"{_BASENAME}-arm0" / "MERGED").write_bytes(b"")
    (out_dir / f"{_BASENAME}.judged.json").write_text('{"a": 2}')

    assert _run(out_dir, queue) == 1

    assert (out_dir / f"{_BASENAME}.judged.json").read_text() == '{"a": 2}'
    assert "judged" in capsys.readouterr().err


def test_copied_rows_hash_to_the_manifest_digest(dirs):
    out_dir, queue = dirs
    out = _bundle(out_dir)
    assert _run(out_dir, queue) == 0

    rows = (queue / f"{_BASENAME}-arm0" / "rows.jsonl").read_bytes()
    assert "sha256:" + hashlib.sha256(rows).hexdigest() == out["digest"]
