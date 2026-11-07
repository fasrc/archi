"""Publishing copies only ``deferred`` bundles (add-inline-bundle-judge-mode, D5)."""

from __future__ import annotations

import json

from src.utils.judge_bundle import MODE_INLINE_BUNDLE, write_judge_bundle
from tests.unit.test_judge_publish import _BASENAME, _SETTINGS, _row, _run


def _bundle(out_dir, **overrides):
    kwargs = dict(
        result_basename=_BASENAME,
        arm_index=0,
        arm_count=1,
        rows=[_row(1), _row(2)],
        keys=["q0", "q1"],
        enabled_metrics=["faithfulness"],
        ragas_settings=_SETTINGS,
        verbosity=0,
        code_version={"digest": "sha256:" + "a" * 64},
        package_digest="sha256:" + "b" * 64,
    )
    kwargs.update(overrides)
    write_judge_bundle(out_dir / "judge_bundles", **kwargs)
    return out_dir / "judge_bundles" / f"{_BASENAME}-arm0"


def test_an_inline_bundle_bundle_is_left_in_place(tmp_path):
    out_dir, queue = tmp_path / "out", tmp_path / "queue"
    out_dir.mkdir()
    queue.mkdir()
    folder = _bundle(out_dir, judge_mode=MODE_INLINE_BUNDLE)

    assert _run(out_dir, queue) == 0

    assert not (queue / folder.name).exists()
    assert sorted(p.name for p in queue.iterdir()) == []
    # The bundle itself is untouched.
    assert (folder / "BUNDLE_COMPLETE").exists()


def test_a_deferred_bundle_is_still_queued(tmp_path):
    out_dir, queue = tmp_path / "out", tmp_path / "queue"
    out_dir.mkdir()
    queue.mkdir()
    folder = _bundle(out_dir)

    assert _run(out_dir, queue) == 0

    assert (queue / folder.name / "READY").exists()


def test_a_bundle_written_before_the_key_existed_is_queued(tmp_path):
    out_dir, queue = tmp_path / "out", tmp_path / "queue"
    out_dir.mkdir()
    queue.mkdir()
    folder = _bundle(out_dir)
    manifest = json.loads((folder / "manifest.json").read_text())
    del manifest["judge_mode"]
    (folder / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))

    assert _run(out_dir, queue) == 0

    assert (queue / folder.name / "READY").exists()
