"""compare_runs shows a descriptive Lynx block only when a Lynx file exists (#734, D8)."""

import json

from scripts.benchmarking import compare_runs as cr

QUESTIONS = ["Q1?", "Q2?", "Q3?"]
BASENAME = "bench-20260101_000000"


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


def _entry(name, order=QUESTIONS, bundle_digest=None):
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
    if bundle_digest is not None:
        entry["judge_bundle"] = {"folder": "f", "digest": bundle_digest}
    return entry


def _artifact(tmp_path, entries, name=f"{BASENAME}.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "benchmarking_results": entries,
                "metadata": {"corpus_fingerprint": "sha256:corpus", "code_version": {}},
            }
        )
    )
    return path


def _lynx(
    arm_index,
    verdicts,
    *,
    basename=BASENAME,
    digest="sha256:rows",
    counts=None,
    rate=None,
):
    """A Lynx file body; *verdicts* maps key -> "PASS", "FAIL", or a status."""
    rows = {}
    for key, value in verdicts.items():
        if value in ("PASS", "FAIL"):
            rows[key] = {"verdict": value, "status": "scored"}
        else:
            rows[key] = {"verdict": None, "status": value}
        rows[key].update(reasoning=None, prompt_tokens=10)
    scored = [v for v in verdicts.values() if v in ("PASS", "FAIL")]
    passed = [v for v in scored if v == "PASS"]
    default_counts = {
        "lynx_scored": len(scored),
        "lynx_pass": len(passed),
        "lynx_skipped_overflow": sum(
            1 for v in verdicts.values() if v == "skipped_overflow"
        ),
        "lynx_parse_errors": sum(1 for v in verdicts.values() if v == "parse_error"),
    }
    return {
        "model_id": "PatronusAI/Lynx-8B",
        "model_revision": "rev1",
        "backend": "vllm",
        "result_basename": basename,
        "arm_index": arm_index,
        "bundle_rows_sha256": digest,
        "token_budget": {"context_tokens": 8000, "max_new_tokens": 600},
        "rows": rows,
        "counts": counts or default_counts,
        "lynx_faithfulness": (
            rate
            if rate is not None
            else (len(passed) / len(scored) if scored else None)
        ),
    }


def _write_lynx(tmp_path, arm_index, body, basename=BASENAME):
    path = tmp_path / f"{basename}-arm{arm_index}.lynx.json"
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return path


def _run(tmp_path, *specs):
    anchors = tmp_path / "anchors.json"
    anchors.write_text("[]")
    out = tmp_path / "report.json"
    code = cr.main([*map(str, specs), "--anchors", str(anchors), "--json", str(out)])
    return code, (json.loads(out.read_text()) if out.exists() else None)


BASE_VERDICTS = {"question_1": "PASS", "question_2": "FAIL", "question_3": "PASS"}
ARM_VERDICTS = {
    "question_1": "PASS",
    "question_2": "skipped_overflow",
    "question_3": "parse_error",
}


def _sweep(tmp_path, **entry_kwargs):
    return _artifact(tmp_path, [_entry("base", **entry_kwargs), _entry("arm")])


def test_no_lynx_file_gives_no_lynx_block(tmp_path, capsys):
    path = _sweep(tmp_path)
    code, report = _run(tmp_path, path)
    assert code == 0
    assert "lynx" not in report
    assert "Lynx" not in capsys.readouterr().out


def test_matching_files_show_the_rate_and_four_counts(tmp_path, capsys):
    path = _sweep(tmp_path)
    _write_lynx(tmp_path, 0, _lynx(0, BASE_VERDICTS))
    _write_lynx(tmp_path, 1, _lynx(1, ARM_VERDICTS))

    code, report = _run(tmp_path, path)

    assert code == 0
    base, arm = report["lynx"]["arms"]
    assert base["label"].endswith("@1") and base["status"] == "ok"
    assert base["counts"] == {
        "lynx_scored": 3,
        "lynx_pass": 2,
        "lynx_skipped_overflow": 0,
        "lynx_parse_errors": 0,
    }
    assert base["lynx_faithfulness"] == 2 / 3
    assert arm["counts"] == {
        "lynx_scored": 1,
        "lynx_pass": 1,
        "lynx_skipped_overflow": 1,
        "lynx_parse_errors": 1,
    }
    assert arm["lynx_faithfulness"] == 1.0
    assert base["model_id"] == "PatronusAI/Lynx-8B"
    out = capsys.readouterr().out
    assert "## Lynx faithfulness (descriptive)" in out
    assert "0.6667" in out


def test_the_judged_result_finds_the_same_file(tmp_path):
    path = _artifact(
        tmp_path, [_entry("base"), _entry("arm")], name=f"{BASENAME}.judged.json"
    )
    _write_lynx(tmp_path, 0, _lynx(0, BASE_VERDICTS))

    code, report = _run(tmp_path, path)

    assert code == 0
    [entry] = report["lynx"]["arms"]
    assert entry["status"] == "ok"
    assert entry["path"].endswith(f"{BASENAME}-arm0.lynx.json")


def test_a_selected_arm_uses_its_own_number(tmp_path):
    first = _sweep(tmp_path)
    _write_lynx(tmp_path, 1, _lynx(1, ARM_VERDICTS))
    other = _artifact(tmp_path, [_entry("other")], name="other-20260101_000000.json")

    code, report = _run(tmp_path, f"{first}@2", other)

    assert code == 0
    [entry] = report["lynx"]["arms"]
    assert entry["label"].endswith("@2")
    assert entry["counts"]["lynx_scored"] == 1


def _refused(tmp_path, body, **entry_kwargs):
    path = _sweep(tmp_path, **entry_kwargs)
    _write_lynx(tmp_path, 0, body)
    code, report = _run(tmp_path, path)
    assert code == 0
    [entry] = report["lynx"]["arms"]
    assert entry["status"] == "refused"
    assert entry["counts"] is None and entry["lynx_faithfulness"] is None
    return entry["reason"]


def test_a_wrong_basename_is_refused(tmp_path):
    reason = _refused(tmp_path, _lynx(0, BASE_VERDICTS, basename="other"))
    assert "'other'" in reason and BASENAME in reason


def test_a_wrong_arm_index_is_refused(tmp_path):
    reason = _refused(tmp_path, _lynx(1, BASE_VERDICTS))
    assert "arm_index 1" in reason and "0" in reason


def test_a_bundle_digest_that_differs_is_refused(tmp_path):
    reason = _refused(
        tmp_path, _lynx(0, BASE_VERDICTS, digest="sha256:b"), bundle_digest="sha256:a"
    )
    assert "sha256:a" in reason and "sha256:b" in reason


def test_a_bundle_digest_that_matches_is_shown(tmp_path):
    path = _sweep(tmp_path, bundle_digest="sha256:rows")
    _write_lynx(tmp_path, 0, _lynx(0, BASE_VERDICTS))
    code, report = _run(tmp_path, path)
    assert code == 0
    assert report["lynx"]["arms"][0]["status"] == "ok"


def test_counts_that_differ_from_the_rows_are_refused(tmp_path):
    counts = {
        "lynx_scored": 3,
        "lynx_pass": 3,
        "lynx_skipped_overflow": 0,
        "lynx_parse_errors": 0,
    }
    reason = _refused(tmp_path, _lynx(0, BASE_VERDICTS, counts=counts))
    assert "counts" in reason


def test_a_rate_that_differs_from_the_counts_is_refused(tmp_path):
    reason = _refused(tmp_path, _lynx(0, BASE_VERDICTS, rate=0.9))
    assert "lynx_faithfulness" in reason


def test_a_file_that_does_not_load_is_refused(tmp_path):
    assert "does not load" in _refused(tmp_path, "{not json")


def test_a_file_with_a_non_finite_number_is_refused(tmp_path):
    body = json.dumps(_lynx(0, BASE_VERDICTS)).replace('"rev1"', "NaN")
    assert "does not load" in _refused(tmp_path, body)


def test_a_file_that_is_not_an_object_is_refused(tmp_path):
    assert "not an object" in _refused(tmp_path, "[]")


def test_a_row_with_an_unknown_status_is_refused(tmp_path):
    body = _lynx(0, BASE_VERDICTS)
    body["rows"]["question_1"]["status"] = "maybe"
    assert "question_1" in _refused(tmp_path, body)


def test_a_scored_row_without_a_verdict_is_refused(tmp_path):
    body = _lynx(0, BASE_VERDICTS)
    body["rows"]["question_1"]["verdict"] = None
    assert "question_1" in _refused(tmp_path, body)


def test_a_skipped_row_with_a_verdict_is_refused(tmp_path):
    body = _lynx(0, ARM_VERDICTS)
    body["rows"]["question_2"]["verdict"] = "PASS"
    assert "question_2" in _refused(tmp_path, body)


def test_a_refused_block_is_rendered_with_its_reason(tmp_path, capsys):
    path = _sweep(tmp_path)
    _write_lynx(tmp_path, 0, _lynx(5, BASE_VERDICTS))
    code, _ = _run(tmp_path, path)
    assert code == 0
    out = capsys.readouterr().out
    assert "refused" in out and "arm_index 5" in out


def test_the_ragas_metric_list_does_not_name_lynx():
    assert not [m for m in cr.METRICS if "lynx" in m.lower()]
