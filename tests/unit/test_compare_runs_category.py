"""compare_runs wiring for the rung-0 sweep (change category-slice-in-compare-runs).

Arm selectors resolve by printed label or recorded benchmarking name; the paired
tests and the category slice are reported per arm; a voided comparison reports
no numbers; a QA run joins only with matching map readings and the arm's prompt.
"""

import json

import pytest

from scripts.benchmarking import compare_runs as cr
from scripts.benchmarking.paired_tests import holm_adjust
from src.utils.benchmark_provenance import (
    category_map_digest,
    category_map_records,
    category_map_text,
)

KB = "https://docs.rc.fas.harvard.edu/kb"
PAIRS = [(f"{KB}/a{i}", "A") for i in range(3)] + [
    (f"{KB}/b{i}", "B") for i in range(3)
]
RECORDS = category_map_records(PAIRS)
DIGEST = category_map_digest(RECORDS)
NAMES = ["fasrc-docs", "fasrc-docs-r0a-category", "fasrc-docs-r0b-icl"]


def _rows(hits, statuses=None):
    rows = {}
    urls = [url for url, _ in PAIRS]
    for i, url in enumerate(urls):
        rows[f"question_{i}"] = {
            "question": f"Q{i}?",
            "reference_answer": f"A{i}",
            "answer": "x",
            "status": (statuses or ["ok"] * len(urls))[i],
            "time_elapsed": 1.0,
            "messages": [],
            "reference_sources_match_fields": ["url"],
            "reference_sources_metadata": [{"url": url, "matched": hits[i]}],
            "sources_metadata": [],
            "sources_trunc_content": [],
        }
    return rows


def _arm_entry(name, rows, index, tmp_path, *, digest=DIGEST, spec="spec-" + "0" * 59):
    snapshot = f"bench-20260924_000000_category_map_{index}.tsv"
    (tmp_path / snapshot).write_text(category_map_text(RECORDS))
    return {
        "single_question_results": rows,
        "total_results": {},
        "configuration_file": f"{name}.yaml",
        "configuration": {"services": {"benchmarking": {"name": name}}},
        "config_version": {"digest": "sha256:c", "divergence_from_selected_file": []},
        "corpus_fingerprint": "sha256:corpus",
        "corpus_fingerprint_before": "sha256:corpus",
        "corpus_unchanged_at_endpoints": True,
        "category_map_sha256_start": digest,
        "category_map_sha256_end": digest,
        "category_map_unchanged_at_endpoints": True,
        "category_map_file": snapshot,
        "agent_md_sha256": spec,
    }


def _sweep(tmp_path, *, r0a_digest=DIGEST, r0b_digest=DIGEST, r0a_hits=None):
    entries = [
        _arm_entry(
            NAMES[0], _rows([True, True, False, True, False, True]), 1, tmp_path
        ),
        _arm_entry(
            NAMES[1],
            _rows(r0a_hits or [True, True, True, True, True, True]),
            2,
            tmp_path,
            digest=r0a_digest,
        ),
        _arm_entry(
            NAMES[2],
            _rows([True, True, False, True, False, True], ["ok"] * 5 + ["degraded"]),
            3,
            tmp_path,
            digest=r0b_digest,
        ),
    ]
    path = tmp_path / "bench-20260924_000000.json"
    path.write_text(
        json.dumps(
            {
                "benchmarking_results": entries,
                "metadata": {"corpus_fingerprint": "sha256:corpus", "code_version": {}},
            }
        )
    )
    anchors = tmp_path / "anchors.json"
    anchors.write_text("[]")
    return path, anchors


def _run(tmp_path, *extra, **sweep):
    path, anchors = _sweep(tmp_path, **sweep)
    out = tmp_path / "report.json"
    code = cr.main([str(path), "--anchors", str(anchors), "--json", str(out), *extra])
    return code, (json.loads(out.read_text()) if out.exists() else None)


# --- selectors ----------------------------------------------------------------------------


def test_baseline_selected_by_recorded_name(tmp_path):
    code, report = _run(tmp_path, "--baseline", NAMES[0])
    assert code == 0
    assert report["baseline"].endswith("@1")


def test_unknown_selector_lists_candidates(tmp_path, capsys):
    code, _ = _run(tmp_path, "--primary", "r0a=source")
    assert code == cr.EXIT_USAGE
    assert NAMES[1] in capsys.readouterr().err


def test_bad_test_name_is_rejected(tmp_path):
    code, _ = _run(tmp_path, "--primary", f"{NAMES[1]}=atoms")
    assert code == cr.EXIT_USAGE


def test_report_header_names_label_and_recorded_name(tmp_path):
    _, report = _run(tmp_path)
    assert [arm["name"] for arm in report["arms"]] == NAMES


# --- paired tests ------------------------------------------------------------------------


def test_paired_tests_with_primaries_and_holm(tmp_path):
    code, report = _run(
        tmp_path,
        "--primary",
        f"{NAMES[1]}=source",
        "--primary",
        f"{NAMES[2]}=completion",
    )
    assert code == 0
    tests = {entry["name"]: entry for entry in report["paired_tests"]}
    r0a, r0b = tests[NAMES[1]], tests[NAMES[2]]
    assert r0a["primary"] == "source" and r0a["source"]["c"] == 2
    assert r0a["source"]["direction"] == "arm better"
    assert r0b["primary"] == "completion" and r0b["completion"]["b"] == 1
    # Two secondaries, Holm-adjusted as a family of two.
    expected = holm_adjust([r0a["completion"]["p"], r0b["source"]["p"]])
    assert r0a["secondary_p_holm"] == pytest.approx(expected[0])
    assert r0b["secondary_p_holm"] == pytest.approx(expected[1])


def test_no_primary_is_labelled(tmp_path):
    _, report = _run(tmp_path)
    assert all(e["primary"] is None for e in report["paired_tests"])


def test_no_tool_call_count_is_descriptive(tmp_path):
    _, report = _run(tmp_path)
    assert report["paired_tests"][0]["no_tool_call"] == 6


# --- category slice and map rule -----------------------------------------------------------


def test_category_slice_per_arm(tmp_path):
    _, report = _run(tmp_path)
    [entry] = [e for e in report["category"] if e["name"] == NAMES[1]]
    assert entry["map_rule"] == "ok"
    assert entry["slice"]["categories"]["A"]["gold_rows"] == 3


def test_routed_arm_with_a_different_map_is_dropped_and_named(tmp_path):
    code, report = _run(
        tmp_path, "--routes-on-category", NAMES[1], r0a_digest="sha256:other"
    )
    assert code == 0
    names = [arm["name"] for arm in report["arms"]]
    assert NAMES[1] not in names
    [gate] = [g for g in report["gates"] if g["id"] == "G9"]
    assert gate["status"] == "void" and NAMES[1] in gate["detail"]


def test_unrouted_arm_with_a_different_map_loses_only_the_slice(tmp_path):
    _, report = _run(tmp_path, r0b_digest="sha256:other")
    [entry] = [e for e in report["category"] if e["name"] == NAMES[2]]
    assert entry["map_rule"] == "slice-only"
    assert entry["slice"] is None
    assert NAMES[2] in [arm["name"] for arm in report["arms"]]


def test_markdown_has_the_new_sections(tmp_path, capsys):
    _run(tmp_path, "--primary", f"{NAMES[1]}=source")
    out = capsys.readouterr().out
    assert "## Paired tests" in out and "## Category slice" in out


# --- QA join -------------------------------------------------------------------------------


def _qa_dir(tmp_path, name, *, start=DIGEST, end=DIGEST, spec="spec-" + "0" * 59):
    qa = tmp_path / name
    qa.mkdir()
    (qa / "summary.json").write_text(
        json.dumps({"items": [], "provenance": {"agent_spec_sha256": spec}})
    )
    (qa / "answers.jsonl").write_text(
        json.dumps(
            {"item_id": "x", "tool_calls": [{"name": "search_vectorstore_hybrid"}]}
        )
        + "\n"
    )
    (qa / "evaluation_results.jsonl").write_text("")
    if start is not None:
        (qa / "category_map_readings.json").write_text(
            json.dumps({"start": start, "end": end})
        )
    return qa


def test_qa_run_with_matching_readings_joins(tmp_path):
    qa = _qa_dir(tmp_path, "qa-ok")
    code, _ = _run(tmp_path, "--qa-run", f"{NAMES[1]}={qa}")
    assert code == 0


def test_qa_run_with_a_moved_map_is_refused(tmp_path, capsys):
    qa = _qa_dir(tmp_path, "qa-moved", end="sha256:other")
    code, _ = _run(tmp_path, "--qa-run", f"{NAMES[1]}={qa}")
    assert code == cr.EXIT_GATE
    assert "map changed during the QA run" in capsys.readouterr().err


def test_qa_run_of_another_prompt_is_refused(tmp_path, capsys):
    qa = _qa_dir(tmp_path, "qa-other", spec="other")
    code, _ = _run(tmp_path, "--qa-run", f"{NAMES[1]}={qa}")
    assert code == cr.EXIT_GATE
    assert "different agent spec" in capsys.readouterr().err
