"""Start refusals of a deferred run and the RESULT_COMPLETE marker (task 3.2).

Design refs: D3 steps 4–7, D7 (package digest), D9 (RESULT_COMPLETE).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys

import pytest
import yaml

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import Benchmarker, ResultHandler
from src.utils.benchmark_provenance import collect_package_digest, package_digest
from src.utils.judge_bundle import JudgePlan, JudgeRunRefused, plan_judge_run

_STUB_PACKAGE = {"digest": "sha256:" + "b" * 64, "source": "pip freeze"}
_UNSET = object()


def _stub_package_digest():
    return dict(_STUB_PACKAGE)


def _deferred_cfg(
    tmp_path,
    name,
    *,
    evaluator_model=_UNSET,
    evaluator_model_revision=_UNSET,
    judge_bundle_dir=_UNSET,
):
    ragas: dict = {
        "judge_mode": "deferred",
        "evaluator_model": "judge/model",
        "evaluator_model_revision": "0123abc",
    }
    if evaluator_model is not _UNSET:
        ragas["evaluator_model"] = evaluator_model
    if evaluator_model_revision is not _UNSET:
        ragas["evaluator_model_revision"] = evaluator_model_revision
    if judge_bundle_dir is not _UNSET:
        ragas["judge_bundle_dir"] = judge_bundle_dir
    cfg = {"services": {"benchmarking": {"mode_settings": {"ragas_settings": ragas}}}}
    path = tmp_path / name
    path.write_text(yaml.safe_dump(cfg))
    return str(path)


def _inline_cfg(tmp_path, name):
    cfg = {"services": {"benchmarking": {"mode_settings": {"ragas_settings": {}}}}}
    path = tmp_path / name
    path.write_text(yaml.safe_dump(cfg))
    return str(path)


@pytest.fixture()
def package_dir(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("X = 1\n")
    return str(pkg)


def _plan(paths, package_dir, tmp_path, package_digest_fn=_stub_package_digest):
    return plan_judge_run(
        paths,
        package_dir=package_dir,
        git_info_path=str(tmp_path / "no-git-info.yaml"),
        package_digest_fn=package_digest_fn,
    )


# ---------------------------------------------------------------------------
# package_digest (D7)
# ---------------------------------------------------------------------------


def test_package_digest_is_order_independent():
    assert package_digest("b==1\na==2\n") == package_digest("a==2\nb==1\n")


def test_package_digest_ignores_blank_lines_and_surrounding_spaces():
    assert package_digest("  a==2 \n\n\n b==1\n  \n") == package_digest("a==2\nb==1")


def test_package_digest_is_sha256_of_sorted_lines():
    expected = "sha256:" + hashlib.sha256(b"a==2\nb==1").hexdigest()
    assert package_digest("b==1\na==2") == expected
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", package_digest("a==1"))


# ---------------------------------------------------------------------------
# collect_package_digest (D7)
# ---------------------------------------------------------------------------


def test_collect_package_digest_hashes_stub_output():
    seen = []

    def _run(args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="b==1\na==2\n", stderr="")

    out = collect_package_digest(run=_run)

    assert out == {"digest": package_digest("a==2\nb==1"), "source": "pip freeze"}
    assert seen == [
        (
            [sys.executable, "-m", "pip", "freeze"],
            {"capture_output": True, "text": True, "timeout": 120, "check": False},
        )
    ]


def test_collect_package_digest_non_zero_exit_is_unavailable():
    def _run(args, **kwargs):
        return subprocess.CompletedProcess(args, 3, stdout="a==1\n", stderr="boom")

    out = collect_package_digest(run=_run)

    assert out["digest"] is None
    assert out["source"].startswith("<unavailable: ")
    assert "3" in out["source"]


def test_collect_package_digest_os_error_is_unavailable():
    def _run(args, **kwargs):
        raise OSError("no python here")

    out = collect_package_digest(run=_run)

    assert out["digest"] is None
    assert out["source"].startswith("<unavailable: ")
    assert "no python here" in out["source"]


def test_collect_package_digest_timeout_is_unavailable():
    def _run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 120)

    out = collect_package_digest(run=_run)

    assert out["digest"] is None
    assert out["source"].startswith("<unavailable: ")


def test_collect_package_digest_empty_output_is_unavailable():
    def _run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=" \n\n", stderr="")

    out = collect_package_digest(run=_run)

    assert out["digest"] is None
    assert out["source"].startswith("<unavailable: ")


# ---------------------------------------------------------------------------
# plan_judge_run steps 4–7 (D3)
# ---------------------------------------------------------------------------


def test_deferred_plan_carries_code_version_and_package_digest(tmp_path, package_dir):
    paths = [_deferred_cfg(tmp_path, "a.yaml"), _deferred_cfg(tmp_path, "b.yaml")]

    plan = _plan(paths, package_dir, tmp_path)

    assert isinstance(plan, JudgePlan)
    assert plan.mode == "deferred"
    assert plan.arm_count == 2
    assert plan.bundle_dir == "judge_bundles"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", plan.code_version["digest"])
    assert plan.code_version["file_count"] == 1
    assert plan.package_digest == _STUB_PACKAGE["digest"]


def test_deferred_plan_reads_git_info(tmp_path, package_dir):
    git_info = tmp_path / "git_info.yaml"
    git_info.write_text(yaml.safe_dump({"last_commit": "abc123", "git_diff": ""}))
    paths = [_deferred_cfg(tmp_path, "a.yaml")]

    plan = plan_judge_run(
        paths,
        package_dir=package_dir,
        git_info_path=str(git_info),
        package_digest_fn=_stub_package_digest,
    )

    assert plan.code_version["deploy_git_commit"] == "abc123"


def test_package_dir_with_no_module_files_refuses(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    paths = [_deferred_cfg(tmp_path, "a.yaml")]

    with pytest.raises(JudgeRunRefused) as exc_info:
        _plan(paths, str(empty), tmp_path)

    assert "<unavailable" in str(exc_info.value)


def test_unavailable_package_digest_refuses(tmp_path, package_dir):
    paths = [_deferred_cfg(tmp_path, "a.yaml")]

    def _unavailable():
        return {"digest": None, "source": "<unavailable: pip exited 3>"}

    with pytest.raises(JudgeRunRefused) as exc_info:
        _plan(paths, package_dir, tmp_path, package_digest_fn=_unavailable)

    assert "<unavailable: pip exited 3>" in str(exc_info.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"evaluator_model": ""},
        {"evaluator_model": None},
        {"evaluator_model_revision": ""},
        {"evaluator_model_revision": None},
        {"evaluator_model_revision": 123},
        {"judge_bundle_dir": ""},
        {"judge_bundle_dir": "/abs"},
        {"judge_bundle_dir": "a/../.."},
        {"judge_bundle_dir": "."},
        {"judge_bundle_dir": 2026},
    ],
)
def test_bad_deferred_setting_refuses_and_names_config(
    tmp_path, package_dir, overrides
):
    good = _deferred_cfg(tmp_path, "a.yaml")
    bad = _deferred_cfg(tmp_path, "b.yaml", **overrides)

    with pytest.raises(JudgeRunRefused) as exc_info:
        _plan([good, bad], package_dir, tmp_path)

    msg = str(exc_info.value)
    assert bad in msg
    assert good not in msg


def test_missing_evaluator_model_revision_refuses(tmp_path, package_dir):
    path = tmp_path / "a.yaml"
    ragas = {"judge_mode": "deferred", "evaluator_model": "judge/model"}
    cfg = {"services": {"benchmarking": {"mode_settings": {"ragas_settings": ragas}}}}
    path.write_text(yaml.safe_dump(cfg))

    with pytest.raises(JudgeRunRefused) as exc_info:
        _plan([str(path)], package_dir, tmp_path)

    assert str(path) in str(exc_info.value)
    assert "evaluator_model_revision" in str(exc_info.value)


def test_different_bundle_dirs_refuse_and_name_both(tmp_path, package_dir):
    a = _deferred_cfg(tmp_path, "a.yaml")
    b = _deferred_cfg(tmp_path, "b.yaml", judge_bundle_dir="x")

    with pytest.raises(JudgeRunRefused) as exc_info:
        _plan([a, b], package_dir, tmp_path)

    msg = str(exc_info.value)
    assert a in msg
    assert b in msg
    assert "'judge_bundles'" in msg
    assert "'x'" in msg


def test_same_bundle_dir_spelled_two_ways_is_accepted(tmp_path, package_dir):
    a = _deferred_cfg(tmp_path, "a.yaml", judge_bundle_dir="x")
    b = _deferred_cfg(tmp_path, "b.yaml", judge_bundle_dir="x/")
    c = _deferred_cfg(tmp_path, "c.yaml", judge_bundle_dir="./x")

    plan = _plan([a, b, c], package_dir, tmp_path)

    assert plan.bundle_dir == "x"
    assert plan.arm_count == 3


def test_all_inline_sweep_never_computes_a_digest(tmp_path):
    paths = [_inline_cfg(tmp_path, "a.yaml"), _inline_cfg(tmp_path, "b.yaml")]

    def _boom():
        raise AssertionError("an inline sweep must not compute a package digest")

    plan = plan_judge_run(
        paths,
        package_dir=str(tmp_path / "does-not-exist"),
        git_info_path=str(tmp_path / "no-git-info.yaml"),
        package_digest_fn=_boom,
    )

    assert plan == JudgePlan(mode="inline", arm_count=2)
    assert plan.code_version is None
    assert plan.package_digest is None


def test_default_package_digest_fn_is_collect_package_digest(
    tmp_path, package_dir, monkeypatch
):
    import src.utils.judge_bundle as jb

    calls = []

    def _fake():
        calls.append(1)
        return dict(_STUB_PACKAGE)

    monkeypatch.setattr(jb, "collect_package_digest", _fake)
    paths = [_deferred_cfg(tmp_path, "a.yaml")]

    plan = plan_judge_run(
        paths,
        package_dir=package_dir,
        git_info_path=str(tmp_path / "no-git-info.yaml"),
    )

    assert calls == [1]
    assert plan.package_digest == _STUB_PACKAGE["digest"]


# ---------------------------------------------------------------------------
# RESULT_COMPLETE (D9)
# ---------------------------------------------------------------------------

_DEFERRED_PLAN = JudgePlan(
    mode="deferred",
    arm_count=1,
    bundle_dir="judge_bundles",
    code_version={"digest": "sha256:" + "a" * 64},
    package_digest="sha256:" + "b" * 64,
)


def _run_stub(monkeypatch, tmp_path, judge_plan, dump_artifacts):
    class _RunStub(Benchmarker):
        def __init__(self):
            self.benchmarking_configs = {"modes": ["RAGAS"]}
            self.benchmark_name = "bench"
            self.queries_to_answers = []
            self.all_config_files = ["a.yaml"]
            self.current_config = "a.yaml"
            self.chain = None
            self.config = {}
            if judge_plan is not None:
                self.judge_plan = judge_plan

        def wait_for_ingestion_completion(self):
            return 0.0

        def _merge_anchor_questions(self):
            return None

        def _process_config(self, modes_being_run):
            return {}, {}

        def load_new_configuration(self):
            self.all_config_files.pop(0)

    monkeypatch.setattr(sb, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(ResultHandler, "result_timestamp", lambda: "20260101_000000")
    monkeypatch.setattr(ResultHandler, "check_collection", lambda cfg: None)
    monkeypatch.setattr(ResultHandler, "get_corpus_fingerprint", lambda cfg: None)
    monkeypatch.setattr(ResultHandler, "get_category_map", lambda cfg: (None, None))
    monkeypatch.setattr(ResultHandler, "handle_results", lambda *a, **kw: None)
    monkeypatch.setattr(ResultHandler, "add_metadata", lambda: None)
    monkeypatch.setattr(ResultHandler, "dump_artifacts", dump_artifacts)
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.delenv("ARCHI_ARGILLA", raising=False)
    return _RunStub()


def test_deferred_run_writes_result_complete_after_dump(monkeypatch, tmp_path):
    marker = tmp_path / "bench-20260101_000000.RESULT_COMPLETE"
    dumped = []

    def _dump(name, **kwargs):
        assert not marker.exists(), "RESULT_COMPLETE must come after dump_artifacts"
        dumped.append(kwargs)

    stub = _run_stub(monkeypatch, tmp_path, _DEFERRED_PLAN, _dump)
    stub.run()

    assert dumped == [{"timestamp": "20260101_000000"}]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "bench-20260101_000000.RESULT_COMPLETE"
    ]
    assert marker.read_bytes() == b""


@pytest.mark.parametrize(
    "judge_plan", [JudgePlan(mode="inline", arm_count=1), None], ids=["inline", "none"]
)
def test_inline_run_writes_no_result_complete(monkeypatch, tmp_path, judge_plan):
    stub = _run_stub(monkeypatch, tmp_path, judge_plan, lambda name, **kwargs: None)
    stub.run()

    assert list(tmp_path.iterdir()) == []


def test_failed_dump_writes_no_result_complete(monkeypatch, tmp_path):
    def _dump(name, **kwargs):
        raise RuntimeError("disk full")

    stub = _run_stub(monkeypatch, tmp_path, _DEFERRED_PLAN, _dump)
    with pytest.raises(RuntimeError, match="disk full"):
        stub.run()

    assert list(tmp_path.iterdir()) == []
