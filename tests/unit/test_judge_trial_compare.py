"""The judge-trial comparison: rank agreement with Sonnet, unscored counts, the control
(fasrc/archi#737, step 5.3)."""

from __future__ import annotations

import json
import math

import pytest

from scripts.benchmarking.judge_trial import compare as cmp


def test_spearman_is_one_for_a_monotone_map_and_minus_one_for_a_reversal():
    xs = [0.1, 0.4, 0.2, 0.9, 0.5]
    assert cmp.spearman(xs, [x * x for x in xs]) == pytest.approx(1.0)
    assert cmp.spearman(xs, [1 - x for x in xs]) == pytest.approx(-1.0)


def test_spearman_averages_tied_ranks():
    assert cmp._ranks([1.0, 1.0, 2.0]) == [1.5, 1.5, 3.0]


def test_correlations_are_none_for_constant_or_tiny_inputs():
    assert cmp.pearson([1.0, 1.0, 1.0], [0.1, 0.2, 0.3]) is None
    assert cmp.spearman([0.1, 0.2], [0.2, 0.1]) is None


def test_pair_metric_counts_each_sides_unscored_rows_and_pairs_the_rest():
    reference = {
        "q1": {"f": 0.5},
        "q2": {"f": None},
        "q3": {"f": 1.0},
        "q9": {"f": 0.2},
    }
    scores = {"q1": {"f": 0.6}, "q2": {"f": 0.7}, "q3": {"f": float("nan")}}
    paired = cmp.pair_metric(reference, scores, "f")
    assert paired["rows_in_both_files"] == 3
    assert paired["pairs"] == [(0.5, 0.6)]
    assert paired["sonnet_unscored"] == 1
    assert paired["judge_unscored"] == 1


def test_summarize_reports_means_shift_and_agreement():
    paired = {
        "metric": "faithfulness",
        "rows_in_both_files": 4,
        "pairs": [(0.2, 0.4), (0.6, 0.7), (0.9, 0.95), (0.4, 0.6)],
        "sonnet_unscored": 0,
        "judge_unscored": 0,
    }
    s = cmp.summarize(paired)
    assert s["n"] == 4
    assert s["sonnet_mean"] == pytest.approx(0.525)
    assert s["judge_mean"] == pytest.approx(0.6625)
    assert s["mean_diff"] == pytest.approx(0.1375)
    assert s["agree_at_cut"] == pytest.approx(0.75)  # (0.4, 0.6) disagrees at 0.5
    assert s["spearman"] == pytest.approx(1.0)


def test_summarize_with_no_pairs_has_no_statistics():
    s = cmp.summarize(
        {
            "metric": "f",
            "rows_in_both_files": 2,
            "pairs": [],
            "sonnet_unscored": 2,
            "judge_unscored": 0,
        }
    )
    assert s["n"] == 0 and "spearman" not in s


def test_control_summary_reads_the_swapped_rows_against_the_originals():
    ledger = [
        {
            "key": "q1__answer_from__q2",
            "row": "q1",
            "donor": "q2",
            "original_sonnet": {
                "faithfulness": 0.9,
                "answer_relevancy": 0.8,
                "answer_correctness": 0.7,
            },
        },
        {
            "key": "q3__answer_from__q4",
            "row": "q3",
            "donor": "q4",
            "original_sonnet": {
                "faithfulness": 0.7,
                "answer_relevancy": 0.6,
                "answer_correctness": 0.5,
            },
        },
    ]
    scores = {
        "q1__answer_from__q2": {
            "faithfulness": 0.1,
            "answer_relevancy": 0.2,
            "answer_correctness": 0.6,
        },
        "q3__answer_from__q4": {
            "faithfulness": 0.0,
            "answer_relevancy": None,
            "answer_correctness": 0.4,
        },
    }
    rows = {r["metric"]: r for r in cmp.control_summary(scores, ledger)}
    assert rows["faithfulness"]["judge_mean_on_swapped"] == pytest.approx(0.05)
    assert rows["faithfulness"]["judge_share_below_cut"] == 1.0
    assert rows["faithfulness"]["sonnet_mean_on_originals"] == pytest.approx(0.8)
    assert (
        rows["answer_relevancy"]["n"] == 1 and rows["answer_relevancy"]["unscored"] == 1
    )
    assert rows["answer_correctness"]["judge_share_below_cut"] == 0.5


def _package(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "reference").mkdir(parents=True)
    (pkg / "scores" / "judge-a").mkdir(parents=True)
    run = "benchmarking-r-20261012_000000"
    ref = [
        {"key": "q1", "faithfulness": 0.2, "answer_relevancy": 0.5},
        {"key": "q2", "faithfulness": 0.8, "answer_relevancy": 0.9},
        {"key": "q3", "faithfulness": 0.5, "answer_relevancy": 0.7},
    ]
    (pkg / "reference" / f"{run}.sonnet.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in ref)
    )
    (pkg / "reference" / "control.jsonl").write_text(
        json.dumps(
            {
                "key": "q1__answer_from__q2",
                "row": "q1",
                "donor": "q2",
                "original_sonnet": {"faithfulness": 0.2},
            }
        )
        + "\n"
    )
    doc = {
        "bundle": f"{run}-arm0",
        "result_basename": run,
        "row_count": 3,
        "metrics": ["faithfulness", "answer_relevancy"],
        "ragas_judge_identity_effective": {
            "model": "x/judge",
            "model_revision": "r1",
            "precision": "checkpoint",
        },
        "judge_execution": {
            "elapsed_seconds": 120.0,
            "pairs_per_minute": 3.0,
            "job_id": "1",
            "node": "n",
        },
        "judge_usage": {"input_tokens": 1000, "output_tokens": 100, "calls": 6},
        "scores": {
            "q1": {"faithfulness": 0.3, "answer_relevancy": 0.4},
            "q2": {"faithfulness": 0.9, "answer_relevancy": None},
            "q3": {"faithfulness": 0.6, "answer_relevancy": 0.8},
        },
        "aggregates": {},
        "nan_counts": {"faithfulness": 0, "answer_relevancy": 1},
    }
    (pkg / "scores" / "judge-a" / f"{run}-arm0.judge_scores.json").write_text(
        json.dumps(doc)
    )
    control = dict(doc)
    control.update(
        {
            "bundle": f"control-unrelated-{run}-arm0",
            "result_basename": f"control-unrelated-{run}",
            "scores": {
                "q1__answer_from__q2": {
                    "faithfulness": 0.05,
                    "answer_relevancy": 0.1,
                    "answer_correctness": 0.2,
                }
            },
        }
    )
    (
        pkg / "scores" / "judge-a" / f"control-unrelated-{run}-arm0.judge_scores.json"
    ).write_text(json.dumps(control))
    return pkg


def test_build_report_pairs_rows_counts_nans_and_reads_the_control(tmp_path):
    pkg = _package(tmp_path)
    report = cmp.build_report(pkg)
    [entry] = report["judges"]
    assert entry["judge"] == "judge-a"
    by_metric = {m["metric"]: m for m in entry["metrics"]}
    assert by_metric["faithfulness"]["n"] == 3
    assert by_metric["faithfulness"]["spearman"] == pytest.approx(1.0)
    assert by_metric["answer_relevancy"]["n"] == 2
    assert by_metric["answer_relevancy"]["judge_unscored"] == 1
    assert entry["control"][0]["judge_mean_on_swapped"] == pytest.approx(0.05)
    assert entry["usage"]["calls"] == 12
    text = cmp.render_markdown(report)
    assert "## judge-a" in text and "| faithfulness | 3 |" in text
    assert "Unrelated-answer control" in text


def test_main_writes_markdown_and_slim_json(tmp_path):
    pkg = _package(tmp_path)
    out_md, out_json = tmp_path / "r.md", tmp_path / "r.json"
    assert (
        cmp.main(["--package", str(pkg), "--out", str(out_md), "--json", str(out_json)])
        == 0
    )
    assert out_md.read_text().startswith("# Judge trial")
    slim = json.loads(out_json.read_text())
    assert "pairs" not in slim["judges"][0]["metrics"][0]


def test_main_without_scores_exits_1(tmp_path):
    (tmp_path / "reference").mkdir()
    assert cmp.main(["--package", str(tmp_path)]) == 1
