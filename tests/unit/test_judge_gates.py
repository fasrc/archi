"""The judge gates G11 (judged) and G12 (one judge), judge offload design D9.

``src/utils/judge_gates.py`` is the one check that ``compare_runs.py`` and both
paths of the campaign archiver call. It is standard library only, so
``compare_runs.py`` can load it by path on a host with no database driver.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from src.utils import judge_gates as jg
from src.utils.judge_identity import canonical_identity as reexported

REPO = Path(__file__).resolve().parents[2]

SONNET = {
    "provider": "huit_bedrock",
    "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "base_url": None,
    "mode": None,
}


def _extended(**overrides):
    identity = {
        "provider": "vllm",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "base_url": None,
        "mode": "unix_socket",
        "model_revision": "6f6073b",
        "precision": "bf16",
        "server_image_digest": None,
        "server_args": None,
        "decoding": {"temperature": 0, "top_p": None, "max_tokens": None},
        "timeout": 300,
        "max_workers": 6,
        "batch_size": None,
        "chunk_size": 8,
        "embedding": {"class": "OpenAIEmbeddings", "model": "m", "revision": None},
        "metrics": ["faithfulness", "answer_relevancy"],
        "scorer": {"code_digest": "sha256:c", "package_digest": "sha256:p"},
    }
    identity.update(overrides)
    return identity


def _arm(
    identity: Any = "absent", *, status=None, evaluator=None, provider=None, name=None
):
    """One result arm. ``identity="absent"`` leaves the key out (before 2026-10-13)."""
    bench: Dict[str, Any] = {}
    if provider is not None:
        bench["provider"], bench["model"] = provider
    if evaluator is not None:
        bench["mode_settings"] = {
            "ragas_settings": {
                "evaluator_provider": evaluator[0],
                "evaluator_model": evaluator[1],
            }
        }
    if name is not None:
        bench["name"] = name
    record: Dict[str, Any] = {"configuration": {"services": {"benchmarking": bench}}}
    if identity != "absent":
        record["ragas_judge_identity_effective"] = identity
    if status is not None:
        record["judge_status"] = status
    return record


# --- the comparison helpers ------------------------------------------------------


def test_judge_identity_reexports_the_one_canonical_helper():
    assert reexported is jg.canonical_identity


def test_canonical_identity_normalizes_integral_floats():
    assert jg.canonical_identity({"t": 0}) == jg.canonical_identity({"t": 0.0})


# --- G11 ---------------------------------------------------------------------------


def test_judged_path_sits_beside_the_pending_file():
    assert jg.judged_path(Path("out/r-1.json")) == Path("out/r-1.judged.json")
    assert jg.judged_path(Path("out/r-1.judged.json")) == Path("out/r-1.judged.json")


def test_g11_passes_inline_scored_and_legacy_arms():
    arms = [
        ("a", _arm(status="inline"), Path("a.json")),
        ("b", _arm(status="scored"), Path("b.judged.json")),
        ("c", _arm(), Path("c.json")),
    ]
    assert jg.pending_refusal(arms) is None


def test_g11_names_each_pending_arm_and_the_judged_file_to_wait_for():
    arms = [
        ("run@1", _arm(status="pending"), Path("out/run.json")),
        ("run@2", _arm(status="pending"), Path("out/run.json")),
        ("other", _arm(status="inline"), Path("out/other.json")),
    ]
    message = jg.pending_refusal(arms)
    assert message.startswith("G11 refused")
    assert "run@1" in message and "run@2" in message and "other" not in message
    assert "out/run.judged.json" in message


# --- G12: the identity of an arm ---------------------------------------------------


def test_the_identity_is_the_recorded_key_when_present():
    assert jg.judge_identity_of(_arm(SONNET, evaluator=("x", "y"))) == SONNET


def test_a_recorded_null_identity_stays_null():
    assert jg.judge_identity_of(_arm(None, evaluator=("x", "y"))) is None


def test_the_fallback_is_used_only_when_the_key_is_absent():
    identity = jg.judge_identity_of(_arm(evaluator=("HUIT_Bedrock", "sonnet")))
    assert identity == {
        "evaluator_provider": "huit_bedrock",
        "evaluator_model": "sonnet",
    }


def test_the_fallback_reads_the_benchmark_provider_like_the_harness():
    from src.bin.service_benchmark import ragas_judge_identity

    record = _arm(provider=("OpenAI", "gpt-4o"))
    identity = jg.judge_identity_of(record)
    provider, model = ragas_judge_identity(record["configuration"])
    assert identity == {"evaluator_provider": provider, "evaluator_model": model}


def test_the_fallback_without_a_recorded_configuration_is_unrecorded():
    assert jg.judge_identity_of({}) == {
        "evaluator_provider": None,
        "evaluator_model": None,
    }


# --- G12: the refusal --------------------------------------------------------------


def _refusal(*identities):
    return jg.identity_refusal(
        [(f"arm{n}", identity) for n, identity in enumerate(identities, 1)]
    )


def test_equal_identities_pass_and_0_equals_0_0():
    a = _extended(decoding={"temperature": 0, "top_p": None, "max_tokens": None})
    b = _extended(decoding={"temperature": 0.0, "top_p": None, "max_tokens": None})
    assert _refusal(a, b) is None


def test_two_offline_arms_from_separate_jobs_compare():
    # judge_execution is not part of the identity, so two jobs with the same
    # settings give the same identity.
    assert _refusal(_extended(), _extended()) is None


def test_inline_sonnet_against_offline_llama_is_refused_with_the_fields():
    message = _refusal(SONNET, _extended())
    assert message.startswith("G12 refused")
    for field in ("provider", "model", "precision", "scorer"):
        assert field in message


def test_bf16_against_fp8_names_precision_and_both_values():
    message = _refusal(_extended(), _extended(precision="fp8"))
    assert "precision" in message
    assert '"bf16"' in message and '"fp8"' in message
    assert "model_revision" not in message


def test_two_old_artifacts_with_the_same_judge_compare():
    old = jg.judge_identity_of(_arm(evaluator=("huit_bedrock", "sonnet")))
    assert _refusal(old, dict(old)) is None


def test_two_old_artifacts_with_different_judges_are_refused():
    a = jg.judge_identity_of(_arm(evaluator=("huit_bedrock", "sonnet")))
    b = jg.judge_identity_of(_arm(evaluator=("huit_bedrock", "haiku")))
    assert "evaluator_model" in _refusal(a, b)


def test_an_old_artifact_never_matches_a_new_one_that_records_a_digest():
    old = jg.judge_identity_of(_arm(evaluator=("vllm", _extended()["model"])))
    message = _refusal(old, _extended())
    assert message is not None and "scorer" in message


def test_new_artifacts_with_one_provider_and_model_but_other_precision_are_refused():
    # The fallback pair would be equal here; it must not be used, because the
    # key is present on both.
    a = jg.judge_identity_of(_arm(_extended(), evaluator=("vllm", "m")))
    b = jg.judge_identity_of(_arm(_extended(precision="fp8"), evaluator=("vllm", "m")))
    assert "precision" in _refusal(a, b)


def test_two_618_artifacts_with_equal_fields_compare():
    assert _refusal(dict(SONNET), dict(SONNET)) is None


def test_a_618_artifact_never_matches_an_extended_one():
    extended = _extended(**{k: SONNET[k] for k in SONNET})
    message = _refusal(SONNET, extended)
    assert message is not None
    assert "precision" in message and "scorer" in message
    assert "<not recorded>" in message


def test_a_null_identity_differs_from_a_judged_arm():
    message = _refusal(None, SONNET)
    assert message is not None and "no judge" in message


def test_two_null_identities_compare():
    assert _refusal(None, None) is None


def test_the_refusal_groups_arms_that_share_an_identity():
    message = jg.identity_refusal([("a", SONNET), ("b", SONNET), ("c", _extended())])
    assert "a, b" in message


# --- G12 across the campaign ledger ------------------------------------------------


KEY = "ragas_judge_identity_effective"


def test_ledger_rows_with_the_same_identity_pass():
    rows = [{"kind": "ragas", "arm": "00", "run": 1, KEY: SONNET}]
    assert jg.ledger_refusal(rows, [("fm-01", SONNET)]) is None


def test_an_earlier_row_with_another_identity_is_refused_with_the_fields():
    rows = [{"kind": "ragas", "arm": "00", "stack": "fm-00", "run": 1, KEY: SONNET}]
    message = jg.ledger_refusal(rows, [("fm-01", _extended())])
    assert message.startswith("G12 refused") and "precision" in message
    assert "fm-00 00 run 1" in message


def test_an_earlier_row_with_no_identity_never_equals_a_recorded_one():
    rows = [{"kind": "ragas", "arm": "00", "stack": "fm-00", "run": 1}]
    message = jg.ledger_refusal(rows, [("fm-01", SONNET)])
    assert message is not None and "new campaign" in message


def test_rows_that_are_not_ragas_rows_are_ignored():
    rows = [{"kind": "ragas-start", "stack": "fm-00"}, {"kind": "qa", "arm": "00"}]
    assert jg.ledger_refusal(rows, [("fm-01", SONNET)]) is None


def test_the_new_arms_of_one_artifact_must_agree_too():
    message = jg.ledger_refusal([], [("a", SONNET), ("b", _extended())])
    assert message is not None and "a" in message and "b" in message


# --- the archiver's artifact choice ------------------------------------------------


def _result(path, arms, mtime):
    path.write_text(json.dumps({"benchmarking_results": arms, "metadata": {}}))
    os.utime(path, (mtime, mtime))
    return path


def test_select_takes_the_newest_inline_result(tmp_path):
    _result(tmp_path / "benchmarking-s-1.json", [_arm()], 100)
    newest = _result(tmp_path / "benchmarking-s-2.json", [_arm(status="inline")], 200)
    choice = jg.select_result(tmp_path, "benchmarking-s-")
    assert choice.artifact == newest and choice.dated_by == newest


def test_select_refuses_a_pending_result_and_names_the_judged_file(tmp_path):
    pending = _result(tmp_path / "benchmarking-s-2.json", [_arm(status="pending")], 200)
    with pytest.raises(jg.JudgeGateRefusal) as info:
        jg.select_result(tmp_path, "benchmarking-s-")
    assert str(info.value).startswith("G11 refused")
    assert str(jg.judged_path(pending)) in str(info.value)


def test_select_takes_the_judged_file_dated_by_its_pending_file(tmp_path):
    pending = _result(tmp_path / "benchmarking-s-2.json", [_arm(status="pending")], 200)
    judged = _result(
        tmp_path / "benchmarking-s-2.judged.json", [_arm(SONNET, status="scored")], 900
    )
    _result(tmp_path / "benchmarking-s-1.json", [_arm()], 150)
    choice = jg.select_result(tmp_path, "benchmarking-s-")
    assert choice.artifact == judged and choice.dated_by == pending


def test_a_judged_file_copied_back_late_does_not_make_its_run_the_newest(tmp_path):
    # The pending file dates the run: a re-run that wrote nothing must not
    # archive the earlier run again because its judged copy arrived later.
    _result(tmp_path / "benchmarking-s-1.json", [_arm(status="pending")], 200)
    _result(tmp_path / "benchmarking-s-1.judged.json", [_arm(status="scored")], 900)
    later = _result(tmp_path / "benchmarking-s-2.json", [_arm()], 300)
    assert jg.select_result(tmp_path, "benchmarking-s-") == (later, later)


def test_select_ignores_lynx_files(tmp_path):
    result = _result(tmp_path / "benchmarking-s-2.json", [_arm()], 200)
    _result(tmp_path / "benchmarking-s-2-arm0.lynx.json", [_arm()], 900)
    assert jg.select_result(tmp_path, "benchmarking-s-").artifact == result


def test_select_refuses_a_judged_file_that_still_has_a_pending_arm(tmp_path):
    _result(tmp_path / "benchmarking-s-2.json", [_arm(status="pending")], 200)
    _result(
        tmp_path / "benchmarking-s-2.judged.json",
        [_arm(status="scored"), _arm(status="pending")],
        900,
    )
    with pytest.raises(jg.JudgeGateRefusal, match="G11 refused"):
        jg.select_result(tmp_path, "benchmarking-s-")


def test_select_with_no_result_says_so(tmp_path):
    with pytest.raises(jg.JudgeGateRefusal, match="no artifact"):
        jg.select_result(tmp_path, "benchmarking-s-")


def test_select_refuses_a_result_that_does_not_load(tmp_path):
    (tmp_path / "benchmarking-s-2.json").write_text("{half")
    with pytest.raises(jg.JudgeGateRefusal, match="does not load"):
        jg.select_result(tmp_path, "benchmarking-s-")


def test_the_cli_prints_the_artifact_and_its_dating_file(tmp_path, capsys):
    pending = _result(tmp_path / "benchmarking-s-2.json", [_arm(status="pending")], 200)
    judged = _result(
        tmp_path / "benchmarking-s-2.judged.json", [_arm(status="scored")], 900
    )
    assert (
        jg.main(["select", "--dir", str(tmp_path), "--prefix", "benchmarking-s-"]) == 0
    )
    assert capsys.readouterr().out.splitlines() == [str(judged), str(pending)]


def test_the_cli_exits_2_with_the_refusal(tmp_path, capsys):
    _result(tmp_path / "benchmarking-s-2.json", [_arm(status="pending")], 200)
    assert (
        jg.main(["select", "--dir", str(tmp_path), "--prefix", "benchmarking-s-"]) == 2
    )
    assert "G11 refused" in capsys.readouterr().err


def test_the_module_loads_by_path_without_the_src_package(monkeypatch):
    # compare_runs.py and archive_run.sh load the file by path on a host with no
    # database driver: `src/utils/__init__.py` imports psycopg2.
    monkeypatch.setitem(sys.modules, "src.utils", None)
    spec = importlib.util.spec_from_file_location(
        "isolated_judge_gates", REPO / "src" / "utils" / "judge_gates.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.identity_refusal([("a", SONNET), ("b", SONNET)]) is None
