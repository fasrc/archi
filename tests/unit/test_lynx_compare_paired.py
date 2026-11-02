"""The Lynx paired test uses only questions that both arms scored (#734, D9)."""

import json

from scripts.benchmarking import compare_runs as cr

QUESTIONS = ["Q1?", "Q2?", "Q3?", "Q4?", "Q5?"]
# The arm ran the same bank in another order, so its positional keys name other
# questions: question_1 is Q2? here and Q1? in the baseline.
ARM_ORDER = ["Q2?", "Q1?", "Q3?", "Q5?", "Q4?"]
BASE = "base-20260101_000000"
ARM = "arm-20260101_000000"


def _row(question):
    return {
        "question": question,
        "reference_answer": "A",
        "answer": "x",
        "status": "ok",
        "time_elapsed": 1.0,
        "messages": [],
        "reference_sources_match_fields": ["url"],
        "reference_sources_metadata": [],
        "sources_metadata": [],
        "sources_trunc_content": [],
    }


def _artifact(tmp_path, basename, name, order):
    entry = {
        "single_question_results": {
            f"question_{i + 1}": _row(q) for i, q in enumerate(order)
        },
        "total_results": {},
        "configuration_file": f"{name}.yaml",
        "configuration": {"services": {"benchmarking": {"name": name}}},
        "config_version": {"digest": "sha256:c", "divergence_from_selected_file": []},
        "corpus_fingerprint": "sha256:corpus",
        "corpus_fingerprint_before": "sha256:corpus",
        "corpus_unchanged_at_endpoints": True,
    }
    path = tmp_path / f"{basename}.json"
    path.write_text(
        json.dumps(
            {
                "benchmarking_results": [entry],
                "metadata": {"corpus_fingerprint": "sha256:corpus", "code_version": {}},
            }
        )
    )
    return path


def _write_lynx(tmp_path, basename, verdicts, arm_index=0):
    rows = {}
    for key, value in verdicts.items():
        scored = value in ("PASS", "FAIL")
        rows[key] = {
            "verdict": value if scored else None,
            "status": "scored" if scored else value,
            "reasoning": None,
            "prompt_tokens": 10,
        }
    values = list(verdicts.values())
    scored = [v for v in values if v in ("PASS", "FAIL")]
    counts = {
        "lynx_scored": len(scored),
        "lynx_pass": scored.count("PASS"),
        "lynx_skipped_overflow": values.count("skipped_overflow"),
        "lynx_parse_errors": values.count("parse_error"),
    }
    body = {
        "model_id": "lynx",
        "model_revision": "r",
        "backend": "vllm",
        "result_basename": basename,
        "arm_index": arm_index,
        "bundle_rows_sha256": "sha256:rows",
        "token_budget": {"context_tokens": 8000, "max_new_tokens": 600},
        "rows": rows,
        "counts": counts,
        "lynx_faithfulness": scored.count("PASS") / len(scored) if scored else None,
    }
    (tmp_path / f"{basename}-arm{arm_index}.lynx.json").write_text(json.dumps(body))


BASE_VERDICTS = {
    "question_1": "PASS",  # Q1?
    "question_2": "PASS",  # Q2?
    "question_3": "PASS",  # Q3?
    "question_4": "PASS",  # Q4?
    "question_5": "FAIL",  # Q5?
}
ARM_VERDICTS = {
    "question_1": "FAIL",  # Q2?
    "question_2": "PASS",  # Q1?
    "question_3": "skipped_overflow",  # Q3?
    "question_4": "PASS",  # Q5?
    "question_5": "parse_error",  # Q4?
}


def _run(tmp_path, base_verdicts=BASE_VERDICTS, arm_verdicts=ARM_VERDICTS):
    base = _artifact(tmp_path, BASE, "base", QUESTIONS)
    arm = _artifact(tmp_path, ARM, "arm", ARM_ORDER)
    if base_verdicts is not None:
        _write_lynx(tmp_path, BASE, base_verdicts)
    if arm_verdicts is not None:
        _write_lynx(tmp_path, ARM, arm_verdicts)
    anchors = tmp_path / "anchors.json"
    anchors.write_text("[]")
    out = tmp_path / "report.json"
    code = cr.main([str(base), str(arm), "--anchors", str(anchors), "--json", str(out)])
    return code, json.loads(out.read_text())


def test_pairs_on_question_text_over_rows_both_arms_scored(tmp_path):
    code, report = _run(tmp_path)

    assert code == 0
    [entry] = report["lynx"]["paired"]
    assert entry["label"] == ARM
    # Q1? PASS/PASS, Q2? PASS->FAIL (b), Q5? FAIL->PASS (c); Q3? and Q4? excluded.
    assert (entry["pairs"], entry["b"], entry["c"], entry["n"]) == (3, 1, 1, 2)
    assert entry["p"] == 1.0
    assert entry["direction"] == "no difference"
    assert entry["excluded"] == {
        "arm skipped_overflow": ["Q3?"],
        "arm parse_error": ["Q4?"],
    }


def test_excluded_questions_are_listed_by_each_reason(tmp_path):
    base = dict(BASE_VERDICTS, question_3="parse_error")
    del base["question_1"]
    arm = dict(ARM_VERDICTS, question_9="PASS")

    _, report = _run(tmp_path, base_verdicts=base, arm_verdicts=arm)

    [entry] = report["lynx"]["paired"]
    assert entry["excluded"] == {
        "baseline no Lynx row": ["Q1?"],
        "baseline parse_error": ["Q3?"],
        "arm skipped_overflow": ["Q3?"],
        "arm parse_error": ["Q4?"],
        "arm key not in result": ["question_9"],
    }
    assert entry["pairs"] == 2


def test_an_arm_with_no_lynx_row_for_a_question_is_excluded(tmp_path):
    arm = dict(ARM_VERDICTS)
    del arm["question_2"]  # Q1?
    _, report = _run(tmp_path, arm_verdicts=arm)
    [entry] = report["lynx"]["paired"]
    assert entry["excluded"]["arm no Lynx row"] == ["Q1?"]


def test_the_lynx_entry_is_not_holm_adjusted_or_a_paired_test(tmp_path):
    _, report = _run(tmp_path)
    [entry] = report["lynx"]["paired"]
    assert not [key for key in entry if "holm" in key or key == "primary"]
    assert all("lynx" not in test for test in report["paired_tests"])


def test_no_paired_entry_without_both_files(tmp_path):
    _, report = _run(tmp_path, base_verdicts=None)
    assert report["lynx"]["paired"] == []
    assert [entry["label"] for entry in report["lynx"]["arms"]] == [ARM]


def test_no_paired_entry_when_one_file_is_refused(tmp_path):
    base = _artifact(tmp_path, BASE, "base", QUESTIONS)
    _write_lynx(tmp_path, BASE, BASE_VERDICTS, arm_index=0)
    arm = _artifact(tmp_path, ARM, "arm", ARM_ORDER)
    _write_lynx(tmp_path, ARM, ARM_VERDICTS, arm_index=0)
    body = json.loads((tmp_path / f"{ARM}-arm0.lynx.json").read_text())
    body["arm_index"] = 3
    (tmp_path / f"{ARM}-arm0.lynx.json").write_text(json.dumps(body))
    anchors = tmp_path / "anchors.json"
    anchors.write_text("[]")
    out = tmp_path / "report.json"

    code = cr.main([str(base), str(arm), "--anchors", str(anchors), "--json", str(out)])

    assert code == 0
    report = json.loads(out.read_text())
    assert report["lynx"]["paired"] == []


def test_the_paired_table_is_rendered(tmp_path, capsys):
    _run(tmp_path)
    out = capsys.readouterr().out
    assert "### Lynx paired test against the baseline" in out
    assert "arm skipped_overflow 1; arm parse_error 1" in out
