"""G11 (judged) and G12 (one judge) in ``compare_runs.py`` (judge offload D9).

A pending arm has no RAGAS scores yet, and arms that two judges scored are not
one measurement. Both refuse with exit 2 and name what they refuse. G12 also
holds every ``--noise-runs`` replicate to the baseline's judge, because the
replicates set the G7 sigma.
"""

import json
import sys
from typing import Any, Dict, List

from scripts.benchmarking import compare_runs as cr

SONNET = {
    "provider": "huit_bedrock",
    "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "base_url": None,
    "mode": None,
}
_BOUND = {
    "services": {
        "chat_app": {
            "recursion_limit": 50,
            "context_editing": {"trigger": 32768, "keep": 1},
        },
        "benchmarking": {
            "mode_settings": {
                "ragas_settings": {
                    "evaluator_provider": "huit_bedrock",
                    "evaluator_model": "sonnet",
                }
            }
        },
    }
}


def _llama(**overrides):
    identity = {
        "provider": "vllm",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "base_url": None,
        "mode": "unix_socket",
        "model_revision": "6f6073b",
        "precision": "bf16",
        "scorer": {"code_digest": "sha256:c", "package_digest": "sha256:p"},
    }
    identity.update(overrides)
    return identity


def _arm(identity: Any = "absent", *, status=None, scores=(0.5, 0.6), model=None):
    rows: Dict[str, Any] = {}
    for n, score in enumerate(scores, 1):
        row = {"question": f"q{n}", "status": "ok", "answer": "a", "time_elapsed": 1}
        if status != "pending":
            row["faithfulness"] = score
        rows[f"question_{n}"] = row
    configuration = json.loads(json.dumps(_BOUND))
    if model is not None:
        ragas = configuration["services"]["benchmarking"]["mode_settings"]
        ragas["ragas_settings"]["evaluator_model"] = model
    record: Dict[str, Any] = {
        "single_question_results": rows,
        "total_results": {},
        "configuration": configuration,
        "config_version": {"digest": "sha256:a", "divergence_from_selected_file": None},
        "corpus_fingerprint": "corpus-1",
    }
    if identity != "absent":
        record["ragas_judge_identity_effective"] = identity
    if status is not None:
        record["judge_status"] = status
    return record


def _file(tmp_path, name, arms: List[Dict[str, Any]]):
    path = tmp_path / name
    document = {
        "benchmarking_results": arms,
        "metadata": {"corpus_snapshot_id": "s1", "code_version": {"digest": "c1"}},
    }
    path.write_text(json.dumps(document))
    return str(path)


def _gates(tmp_path, argv):
    out = tmp_path / "report.json"
    assert cr.main(list(argv) + ["--json", str(out)]) == cr.EXIT_OK
    return {gate["id"]: gate for gate in json.loads(out.read_text())["gates"]}


# --- G11 -------------------------------------------------------------------------


def test_a_pending_arm_is_refused_and_named_with_the_judged_file(tmp_path, capsys):
    path = _file(
        tmp_path,
        "benchmarking-r-1.json",
        [_arm(None, status="pending"), _arm(None, status="pending")],
    )
    assert cr.main([path]) == cr.EXIT_GATE
    err = capsys.readouterr().err
    assert "G11 refused" in err
    assert "benchmarking-r-1@1" in err and "benchmarking-r-1@2" in err
    assert str(tmp_path / "benchmarking-r-1.judged.json") in err


def test_one_pending_arm_beside_an_inline_arm_is_refused(tmp_path, capsys):
    inline = _file(tmp_path, "a.json", [_arm(SONNET, status="inline")])
    pending = _file(tmp_path, "b.json", [_arm(None, status="pending")])
    assert cr.main([inline, pending]) == cr.EXIT_GATE
    err = capsys.readouterr().err
    assert "G11 refused" in err and "b (wait for" in err and "a (wait" not in err


def test_the_judged_file_compares_and_both_gates_pass(tmp_path):
    path = _file(
        tmp_path,
        "benchmarking-r-1.judged.json",
        [
            _arm(_llama(), status="scored"),
            _arm(_llama(), status="scored", scores=(0.7, 0.8)),
        ],
    )
    gates = _gates(tmp_path, [path])
    assert gates["G11"]["status"] == "pass"
    assert gates["G12"]["status"] == "pass"


# --- G12 -------------------------------------------------------------------------


def test_inline_sonnet_against_offline_llama_is_refused(tmp_path, capsys):
    a = _file(tmp_path, "a.json", [_arm(SONNET)])
    b = _file(tmp_path, "b.json", [_arm(_llama(), status="scored")])
    assert cr.main([a, b]) == cr.EXIT_GATE
    err = capsys.readouterr().err
    assert "G12 refused" in err
    assert "provider" in err and "precision" in err


def test_bf16_against_fp8_is_refused_and_names_precision(tmp_path, capsys):
    a = _file(tmp_path, "a.json", [_arm(_llama(), status="scored")])
    b = _file(tmp_path, "b.json", [_arm(_llama(precision="fp8"), status="scored")])
    assert cr.main([a, b]) == cr.EXIT_GATE
    err = capsys.readouterr().err
    assert "precision" in err and '"fp8"' in err and "model_revision" not in err


def test_two_old_artifacts_with_the_same_judge_still_compare(tmp_path):
    a = _file(tmp_path, "a.json", [_arm()])
    b = _file(tmp_path, "b.json", [_arm(scores=(0.4, 0.9))])
    assert _gates(tmp_path, [a, b])["G12"]["status"] == "pass"


def test_two_old_artifacts_with_different_judges_are_refused(tmp_path, capsys):
    a = _file(tmp_path, "a.json", [_arm()])
    b = _file(tmp_path, "b.json", [_arm(model="haiku")])
    assert cr.main([a, b]) == cr.EXIT_GATE
    assert "evaluator_model" in capsys.readouterr().err


def test_an_old_artifact_never_matches_a_new_one_with_a_digest(tmp_path, capsys):
    a = _file(tmp_path, "a.json", [_arm()])
    b = _file(tmp_path, "b.json", [_arm(_llama(), status="scored")])
    assert cr.main([a, b]) == cr.EXIT_GATE
    assert "G12 refused" in capsys.readouterr().err


def test_a_618_artifact_never_matches_an_extended_one(tmp_path, capsys):
    a = _file(tmp_path, "a.json", [_arm(SONNET)])
    b = _file(tmp_path, "b.json", [_arm(dict(SONNET, precision=None))])
    assert cr.main([a, b]) == cr.EXIT_GATE
    assert "precision" in capsys.readouterr().err


# --- G12 for the noise replicates --------------------------------------------------


def test_noise_replicates_with_the_baseline_judge_pass(tmp_path):
    base = _file(tmp_path, "base.json", [_arm(_llama(), status="scored")])
    treat = _file(tmp_path, "treat.json", [_arm(_llama(), status="scored")])
    r1 = _file(
        tmp_path, "r1.json", [_arm(_llama(), status="scored", scores=(0.5, 0.7))]
    )
    r2 = _file(
        tmp_path, "r2.json", [_arm(_llama(), status="scored", scores=(0.6, 0.8))]
    )
    gates = _gates(tmp_path, [base, treat, "--noise-runs", r1, r2])
    assert gates["G12"]["status"] == "pass"


def test_a_noise_replicate_from_another_judge_is_refused_and_named(tmp_path, capsys):
    base = _file(tmp_path, "base.json", [_arm(_llama(), status="scored")])
    treat = _file(tmp_path, "treat.json", [_arm(_llama(), status="scored")])
    r1 = _file(tmp_path, "r1.json", [_arm(_llama(), status="scored")])
    r2 = _file(tmp_path, "r2.json", [_arm(_llama(precision="fp8"), status="scored")])
    assert cr.main([base, treat, "--noise-runs", r1, r2]) == cr.EXIT_GATE
    err = capsys.readouterr().err
    assert "G12 refused" in err and "noise" in err
    assert "r2.json@1" in err and "precision" in err


def test_a_sonnet_noise_replicate_cannot_set_sigma_for_llama_arms(tmp_path, capsys):
    base = _file(tmp_path, "base.json", [_arm(_llama(), status="scored")])
    treat = _file(tmp_path, "treat.json", [_arm(_llama(), status="scored")])
    r1 = _file(tmp_path, "r1.json", [_arm(SONNET)])
    r2 = _file(tmp_path, "r2.json", [_arm(SONNET)])
    assert cr.main([base, treat, "--noise-runs", r1, r2]) == cr.EXIT_GATE
    assert "G12 refused" in capsys.readouterr().err


def test_a_pending_noise_replicate_is_refused(tmp_path, capsys):
    base = _file(tmp_path, "base.json", [_arm(_llama(), status="scored")])
    treat = _file(tmp_path, "treat.json", [_arm(_llama(), status="scored")])
    r1 = _file(tmp_path, "r1.json", [_arm(_llama(), status="scored")])
    r2 = _file(tmp_path, "r2.json", [_arm(None, status="pending")])
    assert cr.main([base, treat, "--noise-runs", r1, r2]) == cr.EXIT_GATE
    assert "G11 refused" in capsys.readouterr().err


def test_the_gates_load_without_the_src_package(monkeypatch):
    # compare_runs reads finished artifacts on hosts with no database driver.
    monkeypatch.delitem(sys.modules, "src.utils.judge_gates", raising=False)
    monkeypatch.setitem(sys.modules, "src.utils", None)
    gates = cr._judge_gates()
    assert gates.identity_refusal([("a", SONNET), ("b", SONNET)]) is None
