"""Codex review of fasrc/archi#793 on compare.py: a metric a row is ineligible for (no
reference answer, so neither file has a cell) is not a judge failure; and score files of
one judge must share the judge-defining identity, or the comparison refuses."""

from __future__ import annotations

import json

import pytest

from scripts.benchmarking.judge_trial import compare as cmp

BASENAME = "run-20261012_000000"


def _doc(bundle, revision="rev1", request_options=None, scores=None):
    return {
        "bundle": bundle,
        "result_basename": BASENAME,
        "row_count": len(scores or {}),
        "metrics": ["faithfulness"],
        "ragas_judge_identity_effective": {
            "model": "org/judge",
            "model_revision": revision,
            "precision": "checkpoint",
            "server_image_digest": "sha256:vllm",
            "decoding": {"request_options": request_options or {"max_tokens": 8}},
            "max_workers": 6,
            "chunk_size": 8,
        },
        "judge_execution": {"elapsed_seconds": 60.0, "pairs_per_minute": 1.0},
        "judge_usage": {"calls": 1, "input_tokens": 10, "output_tokens": 5},
        "scores": scores or {},
    }


def _package(tmp_path, docs):
    pkg = tmp_path / "pkg"
    (pkg / "reference").mkdir(parents=True)
    (pkg / "reference" / f"{BASENAME}.sonnet.jsonl").write_text(
        json.dumps({"key": "q1", "faithfulness": 0.5}) + "\n"
    )
    folder = pkg / "scores" / "judge"
    folder.mkdir(parents=True)
    for doc in docs:
        (folder / f"{doc['bundle']}.judge_scores.json").write_text(json.dumps(doc))
    return pkg


# --- ineligible cells ---------------------------------------------------------------------


def test_a_metric_neither_file_has_a_cell_for_is_not_unscored():
    reference = {"q1": {"f": 0.5}, "q2": {"other": 0.1}, "q3": {"f": None}}
    scores = {"q1": {"f": 0.6}, "q2": {"other": 0.2}, "q3": {"f": 0.4}}
    paired = cmp.pair_metric(reference, scores, "f")
    assert paired["rows_in_both_files"] == 2  # q2 is ineligible for f on both sides
    assert paired["pairs"] == [(0.5, 0.6)]
    assert paired["sonnet_unscored"] == 1  # q3: present, null
    assert paired["judge_unscored"] == 0


def test_a_metric_only_one_file_lacks_is_skipped_not_counted():
    # The judge's bundle marked q2 ineligible (no cell) while the reference has a value:
    # no score was expected from the judge, so it is not a judge failure.
    reference = {"q1": {"f": 0.5}, "q2": {"f": 0.9}}
    scores = {"q1": {"f": 0.6}, "q2": {}}
    paired = cmp.pair_metric(reference, scores, "f")
    assert paired["rows_in_both_files"] == 1
    assert paired["judge_unscored"] == 0
    assert paired["sonnet_unscored"] == 0


# --- mixed judge definitions ---------------------------------------------------------------


def test_build_report_refuses_score_files_of_two_judge_definitions(tmp_path):
    pkg = _package(
        tmp_path,
        [
            _doc(
                f"{BASENAME}-arm0",
                revision="rev1",
                scores={"q1": {"faithfulness": 0.6}},
            ),
            _doc("other-arm0", revision="rev2", scores={}),
        ],
    )
    with pytest.raises(cmp.CompareError, match="rev1.*rev2|rev2.*rev1"):
        cmp.build_report(pkg)


def test_request_options_are_judge_defining_but_speed_knobs_are_not(tmp_path):
    a = _doc(f"{BASENAME}-arm0", request_options={"reasoning_effort": "low"})
    b = _doc("other-arm0", request_options={"reasoning_effort": "high"})
    with pytest.raises(cmp.CompareError, match="reasoning_effort"):
        cmp.build_report(_package(tmp_path, [a, b]))
    c = _doc(f"{BASENAME}-arm0")
    d = _doc("other-arm0")
    d["ragas_judge_identity_effective"]["max_workers"] = 32
    d["ragas_judge_identity_effective"]["chunk_size"] = 32
    report = cmp.build_report(_package(tmp_path / "ok", [c, d]))
    assert report["judges"][0]["bundles_scored"] == ["other-arm0", f"{BASENAME}-arm0"]


def test_main_reports_the_refusal_and_exits_1(tmp_path, capsys):
    pkg = _package(
        tmp_path,
        [
            _doc(f"{BASENAME}-arm0", revision="rev1"),
            _doc("other-arm0", revision="rev2"),
        ],
    )
    code = cmp.main(["--package", str(pkg), "--out", str(tmp_path / "r.md")])
    assert code == 1
    err = capsys.readouterr().err
    assert "refused" in err and "rev2" in err
    assert not (tmp_path / "r.md").exists()
