"""The judge-trial packager: re-emitted bundles, Sonnet reference rows, and the
swapped-answer control (fasrc/archi#737, step 5.3)."""

from __future__ import annotations

import json

import pytest

from scripts.benchmarking.judge_trial import package as pk
from src.utils.judge_bundle import write_judge_bundle
from src.utils.judge_scorer import load_bundle

BASENAME = "benchmarking-ragas-x-20261012_000000"
SETTINGS = {
    "enabled_metrics": [
        "answer_relevancy",
        "faithfulness",
        "context_precision",
        "context_recall",
        "answer_correctness",
    ],
    "embedding_model": "HuggingFace",
    "timeout": 600,
    "max_workers": 6,
    "batch_size": False,
    "evaluator_provider": "huit_bedrock",
    "evaluator_model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
}


def _row(i, passages=None):
    return {
        "user_input": f"question {i}",
        "retrieved_contexts": passages if passages is not None else [f"passage {i}"],
        "response": f"answer {i}",
        "reference": f"reference {i}",
    }


def _rows(n):
    return [_row(i) for i in range(1, n + 1)], [
        f"question_{i}" for i in range(1, n + 1)
    ]


def _rebuilt(root, rows, keys):
    """A rebuilt bundle as rebuild_bundles.py wrote it (Lynx-era manifest labels)."""
    folder = root / BASENAME / "judge_bundles"
    return write_judge_bundle(
        folder,
        result_basename=BASENAME,
        arm_index=0,
        arm_count=1,
        rows=rows,
        keys=keys,
        enabled_metrics=["faithfulness"],
        ragas_settings={
            "timeout": 180,
            "max_workers": 16,
            "evaluator_model": "lynx-rebuild",
        },
        verbosity=0,
        code_version={"digest": "sha256:" + "a" * 64},
        package_digest="rebuilt-from-corpus-db",
    )


def _result(root, keys, *, drop=()):
    questions = {
        key: {
            "question": f"q {key}",
            "status": "ok",
            "faithfulness": 0.5 + 0.01 * i,
            "answer_relevancy": 0.9,
            "context_precision": 1.0,
            "context_recall": 0.8,
            "answer_correctness": 0.6,
        }
        for i, key in enumerate(keys)
        if key not in drop
    }
    doc = {
        "benchmarking_results": [
            {
                "single_question_results": questions,
                "running_configuration": {
                    "services": {
                        "benchmarking": {
                            "mode_settings": {"ragas_settings": dict(SETTINGS)}
                        }
                    }
                },
                "ragas_effective_settings": {"timeout": 600, "max_workers": 6},
                "ragas_judge_identity_effective": {"provider": "huit_bedrock"},
                "judge_usage": {"calls": 3},
                "corpus_fingerprint": "sha256:corpus",
            }
        ],
        "metadata": {"code_version": {"digest": "sha256:" + "c" * 64}},
    }
    path = root / f"{BASENAME}.json"
    path.write_text(json.dumps(doc))
    return path


# --- build_control -----------------------------------------------------------------------


def test_control_is_deterministic_with_distinct_donors_and_foreign_answers():
    rows, keys = _rows(30)
    a = pk.build_control(rows, keys, size=20, seed=20261027)
    b = pk.build_control(rows, keys, size=20, seed=20261027)
    assert a == b
    c_rows, c_keys, ledger = a
    assert len(c_rows) == len(c_keys) == len(ledger) == 20
    donors = [entry["donor"] for entry in ledger]
    assert len(set(donors)) == 20
    for row, entry in zip(c_rows, ledger):
        own = rows[keys.index(entry["row"])]
        donor = rows[keys.index(entry["donor"])]
        assert entry["row"] != entry["donor"]
        assert row["response"] == donor["response"] != own["response"]
        assert row["user_input"] == own["user_input"]
        assert row["retrieved_contexts"] == own["retrieved_contexts"]
        assert row["reference"] == own["reference"]
    assert c_keys[0] == f"{ledger[0]['row']}__answer_from__{ledger[0]['donor']}"


def test_control_never_pairs_rows_that_share_a_passage():
    rows, keys = _rows(6)
    shared = ["the same passage"]
    for row in rows:
        row["retrieved_contexts"] = shared  # every row shares a passage
    with pytest.raises(pk.PackageError, match="shared passage"):
        pk.build_control(rows, keys, size=2, seed=1)


def test_control_refuses_too_few_rows():
    rows, keys = _rows(3)
    with pytest.raises(pk.PackageError):
        pk.build_control(rows, keys, size=3, seed=1)


# --- reference rows and settings ------------------------------------------------------------


def test_reference_rows_join_by_key_and_keep_the_five_metrics(tmp_path):
    rows, keys = _rows(3)
    result = json.loads(_result(tmp_path, keys).read_text())
    ref = pk.reference_rows(result, keys)
    assert [r["key"] for r in ref] == keys
    assert ref[0]["faithfulness"] == 0.5
    assert set(ref[0]) >= {"key", "status", "faithfulness", "answer_correctness"}


def test_reference_rows_refuse_a_bundle_key_the_result_lacks(tmp_path):
    rows, keys = _rows(3)
    result = json.loads(_result(tmp_path, keys, drop={"question_2"}).read_text())
    with pytest.raises(pk.PackageError, match="question_2"):
        pk.reference_rows(result, keys)


def test_ragas_settings_come_from_the_running_configuration(tmp_path):
    rows, keys = _rows(2)
    result = json.loads(_result(tmp_path, keys).read_text())
    assert pk.ragas_settings_of(result)["timeout"] == 600


# --- build_package -----------------------------------------------------------------------


def test_build_package_reemits_the_bundle_with_the_runs_settings_and_a_control(
    tmp_path,
):
    rows, keys = _rows(30)
    rebuilt = tmp_path / "rebuilt"
    results = tmp_path / "results"
    results.mkdir()
    source = _rebuilt(rebuilt, rows, keys)
    _result(results, keys)
    out = tmp_path / "pkg"

    manifest = pk.build_package(
        rebuilt_runs=rebuilt,
        results=results,
        out=out,
        basenames=[BASENAME],
        control_from=BASENAME,
        control_size=5,
        seed=20261027,
    )

    bundle = out / "bundles" / f"{BASENAME}-arm0"
    m, b_rows, b_keys = load_bundle(bundle)
    assert b_rows == rows and b_keys == keys
    assert m["judge_mode"] == "inline+bundle"
    assert m["enabled_metrics"] == SETTINGS["enabled_metrics"]
    assert m["ragas_run_config_kwargs"]["timeout"] == 600
    assert m["ragas_run_config_kwargs"]["max_workers"] == 6
    assert m["embedding_model"] == "HuggingFace"
    assert m["judge_model"]["id"] == SETTINGS["evaluator_model"]
    assert m["code_version"]["rebuilt_rows_sha256"] == source["digest"]
    assert m["code_version"]["digest"] == "sha256:" + "c" * 64

    ref_lines = (
        (out / "reference" / f"{BASENAME}.sonnet.jsonl").read_text().splitlines()
    )
    assert len(ref_lines) == 30
    meta = json.loads((out / "reference" / f"{BASENAME}.meta.json").read_text())
    assert meta["judge_usage"] == {"calls": 3}

    control = out / "bundles" / f"control-unrelated-{BASENAME}-arm0"
    cm, c_rows, c_keys = load_bundle(control)
    assert cm["row_count"] == 5 and len(c_keys) == 5
    ledger = [
        json.loads(x)
        for x in (out / "reference" / "control.jsonl").read_text().splitlines()
    ]
    assert [e["key"] for e in ledger] == c_keys
    assert all(
        "original_sonnet" in e and "faithfulness" in e["original_sonnet"]
        for e in ledger
    )

    package = json.loads((out / "package.json").read_text())
    assert package["runs"][0]["rows"] == 30
    assert package["control"]["rows"] == 5
    assert package["control"]["answer_reading_metrics"] == [
        "faithfulness",
        "answer_relevancy",
        "answer_correctness",
    ]
    assert manifest == package


def test_build_package_refuses_a_missing_result(tmp_path):
    rows, keys = _rows(3)
    rebuilt = tmp_path / "rebuilt"
    _rebuilt(rebuilt, rows, keys)
    (tmp_path / "results").mkdir()
    with pytest.raises(pk.PackageError, match="no result JSON"):
        pk.build_package(
            rebuilt_runs=rebuilt,
            results=tmp_path / "results",
            out=tmp_path / "pkg",
            basenames=[BASENAME],
            control_from=None,
            control_size=20,
            seed=1,
        )


UNSCORED = "benchmarking-embtrial-a1-20261029_000000"


def _rebuilt_unscored(root, rows, keys):
    return write_judge_bundle(
        root / UNSCORED / "judge_bundles",
        result_basename=UNSCORED,
        arm_index=0,
        arm_count=1,
        rows=rows,
        keys=keys,
        enabled_metrics=["faithfulness"],
        ragas_settings={"timeout": 180, "max_workers": 16},
        verbosity=0,
        code_version={"digest": "sha256:" + "e" * 64},
        package_digest="rebuilt-from-corpus-db",
    )


def test_unscored_runs_are_reemitted_deferred_with_the_first_runs_settings(tmp_path):
    rows, keys = _rows(4)
    rebuilt = tmp_path / "rebuilt"
    results = tmp_path / "results"
    results.mkdir()
    _rebuilt(rebuilt, rows, keys)
    _result(results, keys)
    u_rows, u_keys = _rows(2)
    other_root = tmp_path / "embtrial"
    source = _rebuilt_unscored(other_root, u_rows, u_keys)
    out = tmp_path / "pkg"

    manifest = pk.build_package(
        rebuilt_runs=rebuilt,
        results=results,
        out=out,
        basenames=[BASENAME],
        control_from=None,
        control_size=20,
        seed=1,
        unscored=[UNSCORED],
        unscored_rebuilt=other_root,
    )

    m, b_rows, b_keys = load_bundle(out / "bundles-unscored" / f"{UNSCORED}-arm0")
    assert b_rows == u_rows and b_keys == u_keys
    assert m["judge_mode"] == "deferred"
    assert m["enabled_metrics"] == SETTINGS["enabled_metrics"]
    assert m["ragas_run_config_kwargs"]["timeout"] == 600
    assert m["code_version"] == {"rebuilt_rows_sha256": source["digest"]}
    assert not (out / "reference" / f"{UNSCORED}.sonnet.jsonl").exists()
    assert manifest["unscored"] == [
        {
            "result_basename": UNSCORED,
            "bundle": f"{UNSCORED}-arm0",
            "rows": 2,
            "rows_sha256": m["rows_sha256"],
            "settings_from": BASENAME,
        }
    ]
    assert manifest["control"] is None


def test_unscored_without_a_scored_run_is_refused(tmp_path):
    with pytest.raises(pk.PackageError, match="--unscored needs"):
        pk.build_package(
            rebuilt_runs=tmp_path,
            results=tmp_path,
            out=tmp_path / "pkg",
            basenames=[],
            control_from=None,
            control_size=20,
            seed=1,
            unscored=[UNSCORED],
        )


def test_main_prints_one_line_per_bundle(tmp_path, capsys):
    rows, keys = _rows(6)
    rebuilt = tmp_path / "rebuilt"
    results = tmp_path / "results"
    results.mkdir()
    _rebuilt(rebuilt, rows, keys)
    _result(results, keys)
    code = pk.main(
        [
            "--rebuilt-runs",
            str(rebuilt),
            "--results",
            str(results),
            "--out",
            str(tmp_path / "pkg"),
            "--run",
            BASENAME,
            "--control-from",
            BASENAME,
            "--control-size",
            "2",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{BASENAME}-arm0: 6 rows, 6 reference rows",
        f"control-unrelated-{BASENAME}-arm0: 2 control rows",
    ]


def test_main_reports_a_refusal_and_returns_1(tmp_path, capsys):
    rows, keys = _rows(3)
    rebuilt = tmp_path / "rebuilt"
    _rebuilt(rebuilt, rows, keys)
    (tmp_path / "results").mkdir()
    code = pk.main(
        [
            "--rebuilt-runs",
            str(rebuilt),
            "--results",
            str(tmp_path / "results"),
            "--out",
            str(tmp_path / "pkg"),
            "--run",
            BASENAME,
        ]
    )
    assert code == 1
    assert "no result JSON" in capsys.readouterr().err
