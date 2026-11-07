"""The deferred judge path end to end, with no cluster, GPU or network (#733, task 4.7).

Operator decision, 2026-10-21: the first real cluster run is in fasrc/archi#737,
after the October release, so this test is the release's end-to-end check of
the deferred path. It runs the real steps in order on temporary folders:

1. a deferred two-arm sweep run through ``Benchmarker.run`` with the PR 2 code
   (the bundle writer, the pending arm record, ``dump_artifacts``,
   ``RESULT_COMPLETE``); only the stack around it (the agent, Postgres) is a
   stub;
2. ``publish.py``: both bundles READY, the pending result in ``<queue>/results/``;
3. ``judge_submit.sh`` with stub ``squeue`` and ``sbatch``: one judge job;
4. the scorer CLI ``score.py run`` with a stub judge in place of vLLM: both
   bundles claimed and SCORED;
5. ``merge.py``: one judged file with both arms, and MERGED; then the
   archiver refuses while only the pending file is there (G11), and the
   publish copy-back puts the judged file beside it;
6. ``compare_runs.py``: G11 refuses the pending file and accepts the judged
   file, and G12 passes;
7. ``archive_run.sh --sweep``: one ledger row per arm, each with its judge
   identity.

The judged aggregates equal the inline aggregates for the same rows and judge.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

import src.bin.service_benchmark as sb
from scripts.benchmarking import compare_runs as cr
from scripts.benchmarking.feature_matrix import sweep_tools as st
from src.bin.service_benchmark import Benchmarker, ResultHandler
from src.utils.benchmark_provenance import (
    category_map_digest,
    category_map_records,
    prompt_text_sha256,
)
from src.utils.benchmark_resilience import OK
from src.utils.judge_bundle import JudgePlan
from src.utils.ragas_scoring import RagasScoreOutcome, score_ragas
from tests.unit.test_judge_scorer import OpenAIEmbeddings, StubJudge
from tests.unit.test_ragas_scoring_characterization import _install_ragas_stub

REPO = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
MODEL = "meta-llama/Llama-3.3-70B-Instruct"
REVISION = "6f6073b4"
CODE = "sha256:" + "c" * 64
PACKAGE = "sha256:" + "d" * 64
STACK = "r0"
STAMP = "20261023_120000"
BASENAME = f"benchmarking-{STACK}-{STAMP}"
RAGAS = {
    "enabled_metrics": ["answer_relevancy", "context_recall"],
    "timeout": 600,
    "max_workers": 6,
    "batch_size": None,
    "embedding_model": "openai",
    "evaluator_model": MODEL,
    "evaluator_model_revision": REVISION,
}
QUESTIONS = [f"q{n}" for n in range(1, 10)]
KB = "https://docs.rc.fas.harvard.edu/kb"
TREE = " ".join(
    f"{p}=c0ffee00"
    for p in ("src", "scripts", "deploy", "pyproject.toml", "requirements")
)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


# --- the campaign around the run: a locked two-arm sweep --------------------------------


def _sweep(root):
    """A generated two-arm sweep, its inputs and its prepared QA folder."""
    sweep = root / "sweep"
    (sweep / "prompts").mkdir(parents=True)
    (sweep / "bank.json").write_text(
        json.dumps(
            [{"user_input": q, "reference": f"reference {q}"} for q in QUESTIONS]
        )
    )
    (sweep / "anchors.json").write_text("[]")
    (sweep / "prompts" / "fasrc-docs.md").write_text("control\n")
    (sweep / "prompts" / "fasrc-docs-terse.md").write_text("control\nBe brief.\n")
    base = {
        "name": STACK,
        "services": {
            "chat_app": {
                "recursion_limit": 50,
                "context_editing": {"trigger": 32768, "keep": 1},
            },
            "benchmarking": {
                "queries_path": str(sweep / "bank.json"),
                "anchors": {"path": str(sweep / "anchors.json")},
                "agent_md_file": str(sweep / "prompts" / "fasrc-docs.md"),
                "modes": ["RAGAS"],
                "judge_mode": "deferred",
                "mode_settings": {"ragas_settings": dict(RAGAS)},
            },
        },
        "data_manager": {"chunking": {"strategy": "sentence"}},
    }
    (sweep / "base.yaml").write_text(yaml.safe_dump(base))
    manifest = sweep / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "base_config": str(sweep / "base.yaml"),
                "out_dir": str(sweep / "configs"),
                "prompts": [
                    str(sweep / "prompts" / "fasrc-docs.md"),
                    str(sweep / "prompts" / "fasrc-docs-terse.md"),
                ],
            }
        )
    )
    configs = st.generate(manifest)
    prepared = sweep / "prepared"
    prepared.mkdir()
    (prepared / "preparation.jsonl").write_text('{"item_id": "x"}\n')
    (sweep / "qa.json").write_text("{}")
    (sweep / "profile.yaml").write_text("judge: x\n")
    lock = st.build_lock(
        sweep_dir=sweep / "configs",
        manifest=manifest,
        stack=STACK,
        qa_dataset=sweep / "qa.json",
        qa_profile=sweep / "profile.yaml",
        prepared=prepared,
        code_sha="c0ffee00",
        code_tree=TREE,
    )
    return sweep, sorted(configs), lock


# --- 1. the deferred run -----------------------------------------------------------------

MAP_RECORDS = category_map_records([(f"{KB}/jobs", "Cluster Usage")])
MAP_DIGEST = category_map_digest(MAP_RECORDS)
RETRIEVAL = {
    "collection": "docs_with_HF",
    "embedding_name": "HuggingFaceEmbeddings",
    "embedding_model": "all-MiniLM-L6-v2",
    "chunk_count": 10,
    "usable_chunk_count": 10,
    "untagged_chunk_count": 0,
    "embedding_model_source": "chunks",
}


class _DeferredRun(Benchmarker):
    """``Benchmarker.run`` over the sweep configs; the agent and Postgres are stubs."""

    def __init__(self, configs):
        self.benchmark_name = f"benchmarking-{STACK}"
        self.benchmarking_configs = {
            "modes": ["RAGAS"],
            "mode_settings": {"ragas_settings": dict(RAGAS)},
        }
        self.config = {"services": {"benchmarking": self.benchmarking_configs}}
        self.queries_to_answers = [{"user_input": q} for q in QUESTIONS]
        self.required_fields = ["user_input"]
        self.judge_plan = JudgePlan(
            mode="deferred",
            arm_count=len(configs),
            bundle_dir="judge_bundles",
            code_version={"digest": CODE, "source": "content digest"},
            package_digest=PACKAGE,
        )
        self.all_config_files = [str(path) for path in configs[1:]] + ["FINISHED"]
        self._use(str(configs[0]))

    def _use(self, path):
        self.current_config = path
        if path == "FINISHED":
            return
        config = yaml.safe_load(Path(path).read_text())
        # The running configuration is the selected file: no divergence.
        self.chain = types.SimpleNamespace(config=config)
        bench = config["services"]["benchmarking"]
        self.agent_md_sha256 = prompt_text_sha256(Path(bench["agent_md_file"]))

    def load_new_configuration(self):
        self._use(self.all_config_files.pop(0))

    def wait_for_ingestion_completion(self):
        return 12.5

    def _merge_anchor_questions(self):
        return None

    def get_ragas_llm_evaluator(self, *args, **kwargs):
        raise AssertionError("a deferred run never builds the judge")

    def get_ragas_embedding_model(self, *args, **kwargs):
        raise AssertionError("a deferred run never builds the judge")

    def _answer_and_score_question(self, question_item, question_id, modes_being_run):
        question = question_item["user_input"]
        return {
            "q_results": {
                "question": question,
                "answer": f"answer {question}",
                "status": OK,
                "time_elapsed": 1.0,
            },
            "dataset_result": {
                "user_input": question,
                "retrieved_contexts": [f"context {question}"],
                "response": f"answer {question}",
                "reference": f"reference {question}",
            },
            "matches": None,
        }


def _run_deferred(monkeypatch, out_dir, configs):
    monkeypatch.setattr(sb, "OUTPUT_DIR", out_dir)
    monkeypatch.setattr(sb, "EXTRA_METADATA_PATH", str(out_dir / "no-git-info.yaml"))
    for name, value in (
        ("results", []),
        ("metadata", {}),
        ("ab_comparison", {}),
        ("ab_comparisons", []),
        ("leaderboard", {}),
        ("category_map_records_by_arm", []),
        ("_corpus_snapshot_id", "snap-1"),
    ):
        monkeypatch.setattr(ResultHandler, name, value)
    monkeypatch.setattr(ResultHandler, "result_timestamp", staticmethod(lambda: STAMP))
    monkeypatch.setattr(
        ResultHandler, "check_collection", staticmethod(lambda cfg: dict(RETRIEVAL))
    )
    monkeypatch.setattr(
        ResultHandler,
        "get_corpus_fingerprint",
        staticmethod(lambda cfg: "sha256:corpus"),
    )
    monkeypatch.setattr(
        ResultHandler,
        "get_category_map",
        staticmethod(lambda cfg: (list(MAP_RECORDS), MAP_DIGEST)),
    )
    monkeypatch.setattr(
        ResultHandler,
        "get_embedding_tag_state",
        staticmethod(
            lambda cfg: {
                "embedding_model_tags": ["all-MiniLM-L6-v2"],
                "untagged_chunk_count": 0,
            }
        ),
    )
    monkeypatch.delenv("ARCHI_ARGILLA", raising=False)
    _DeferredRun(configs).run()


# --- the steps after the run -------------------------------------------------------------


def _python(*args, env=None):
    proc = subprocess.run(
        [PYTHON, *[str(a) for a in args]],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO,
        env=env,
    )
    return proc


def _judge_home(root):
    home = root / "judge"
    (home / "queue").mkdir(parents=True)
    (home / "judge.env").write_text(
        f"JUDGE_MODEL_ID={MODEL}\nJUDGE_MODEL_REVISION={REVISION}\n"
        "JUDGE_PRECISION=bf16\nJUDGE_BOOTSTRAP_ROWS_PER_MINUTE=2\n"
    )
    (home / "scorer-identity.json").write_text(
        json.dumps(
            {
                "code_digest": CODE,
                "package_digest": PACKAGE,
                "image_digest": "sha256:scorer-image",
                "ragas_version": "0.3.5",
            },
            sort_keys=True,
        )
    )
    return home


def _stubs(root):
    """Stub squeue, sbatch, docker and git for the submitter and the archiver."""
    bin_dir = root / "bin"
    bin_dir.mkdir()
    state = root / "stub"
    state.mkdir()
    _stub(bin_dir, "squeue", f'echo "$*" >> {state}/squeue.calls\nexit 0\n')
    _stub(bin_dir, "sbatch", f'echo "$*" >> {state}/sbatch.calls\necho 4242\n')
    _stub(bin_dir, "docker", 'case "$1" in inspect) echo exited ;; esac\n')
    _stub(
        bin_dir,
        "git",
        'case "$1" in rev-parse) echo c0ffee00 ;; status) ;; esac\nexit 0\n',
    )
    return bin_dir, state


def _shell_env(root, bin_dir, out_dir, home):
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bin_dir}:{Path(PYTHON).parent}:/usr/bin:/bin",
            "USER": "tester",
            "JUDGE_HOME": str(home),
            "JUDGE_PYTHON": PYTHON,
            "FM_OUT": str(out_dir),
            "FM_PYTHON": PYTHON,
            "FM_DOCKER": str(bin_dir / "docker"),
            "FM_GIT": str(bin_dir / "git"),
            "ARCHI_DIR": str(root / "archi"),
            "FM_POLL_SECONDS": "0",
        }
    )
    env.pop("FM_PAGE_MAIL_TO", None)
    return env


def _inline_aggregates(bundle, monkeypatch):
    """The inline path's aggregates for the bundle's rows and the same stub judge."""
    lines = [
        json.loads(line) for line in (bundle / "rows.jsonl").read_text().splitlines()
    ]
    keys = [line.pop("key") for line in lines]
    judge = StubJudge()
    _install_ragas_stub(monkeypatch, judge.evaluate)
    outcome = RagasScoreOutcome()
    score_ragas(
        lines,
        keys,
        {key: {} for key in keys},
        enabled_metrics=RAGAS["enabled_metrics"],
        ragas_settings=RAGAS,
        verbosity=0,
        judge_factory=lambda: types.SimpleNamespace(),
        embeddings_factory=OpenAIEmbeddings,
        judge_provider="vllm",
        judge_model=MODEL,
        outcome=outcome,
    )
    return outcome.aggregates


def test_the_deferred_path_end_to_end(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sweep, configs, lock = _sweep(tmp_path)
    (out_dir / f"sweep-{STACK}.lock").write_text(
        json.dumps(lock, indent=1, sort_keys=True)
    )
    stack_dir = tmp_path / "archi" / f"archi-{STACK}"
    stack_dir.mkdir(parents=True)
    (stack_dir / "fm-lock.sha256").write_text(
        _sha(out_dir / f"sweep-{STACK}.lock") + "\n"
    )
    started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
    (out_dir / "ledger.json").write_text(
        json.dumps(
            [
                {
                    "kind": "ragas-start",
                    "stack": STACK,
                    "sweep": True,
                    "started": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            ]
        )
    )
    home = _judge_home(tmp_path)
    queue = home / "queue"
    bin_dir, state = _stubs(tmp_path)
    env = _shell_env(tmp_path, bin_dir, out_dir, home)

    # 1. The deferred run: two bundles, a pending result and RESULT_COMPLETE.
    _run_deferred(monkeypatch, out_dir, configs)
    pending = out_dir / f"{BASENAME}.json"
    assert (out_dir / f"{BASENAME}.RESULT_COMPLETE").exists()
    document = json.loads(pending.read_text())
    assert [arm["judge_status"] for arm in document["benchmarking_results"]] == [
        "pending",
        "pending",
    ]
    bundles = [out_dir / "judge_bundles" / f"{BASENAME}-arm{i}" for i in (0, 1)]
    assert all((bundle / "BUNDLE_COMPLETE").exists() for bundle in bundles)

    # 2. publish.py: both bundles READY, the pending result in the queue.
    publish = REPO / "scripts" / "benchmarking" / "judge" / "publish.py"
    proc = _python(publish, "--out-dir", out_dir, "--queue", queue)
    assert proc.returncode == 0, proc.stderr
    assert all((queue / bundle.name / "READY").exists() for bundle in bundles)
    assert (queue / "results" / pending.name).read_bytes() == pending.read_bytes()

    # 3. judge_submit.sh: exactly one judge job.
    submit = REPO / "scripts" / "benchmarking" / "slurm" / "judge_submit.sh"
    proc = subprocess.run(
        ["bash", str(submit)], env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    (job,) = (state / "sbatch.calls").read_text().splitlines()
    assert "--dependency=singleton" in job and job.endswith("archi_judge.sbatch")

    # 4. The scorer CLI with a stub judge in place of vLLM.
    score = _load("e2e_score", REPO / "scripts" / "benchmarking" / "judge" / "score.py")
    monkeypatch.setattr(
        score,
        "live_identity",
        lambda package_dir: {"code_digest": CODE, "package_digest": PACKAGE},
    )
    judge = StubJudge()
    _install_ragas_stub(monkeypatch, judge.evaluate)
    monkeypatch.setenv("VLLM_API_KEY", "k-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PATH", env["PATH"])
    argv = [
        "run",
        "--queue",
        str(queue),
        "--judge-env",
        str(home / "judge.env"),
        "--sidecar",
        str(home / "scorer-identity.json"),
        "--socket",
        "/nonexistent/vllm.sock",
        "--job-id",
        "4242",
    ]
    assert score.main(argv) == 0
    for bundle in bundles:
        assert (queue / bundle.name / "claim.1").read_text().strip() == "4242"
        assert (queue / bundle.name / "SCORED").exists()

    # 5. merge.py: one judged file with both arms, and MERGED.
    merge = REPO / "scripts" / "benchmarking" / "judge" / "merge.py"
    proc = _python(merge, "--queue", queue)
    assert proc.returncode == 0, proc.stderr
    judged_in_queue = queue / "results" / f"{BASENAME}.judged.json"
    assert all((queue / bundle.name / "MERGED").exists() for bundle in bundles)

    # While only the pending file is beside the run, the archiver refuses (G11).
    archive = REPO / "scripts" / "benchmarking" / "feature_matrix" / "archive_run.sh"
    census = sweep / "census.json"
    census.write_text(
        json.dumps(
            {
                "passed": True,
                "corpus_fingerprint": "sha256:corpus",
                "category_map_digest": MAP_DIGEST,
                "inputs": {
                    "bank_sha256": lock["bank"]["sha256"],
                    "anchors_sha256": lock["anchors"]["sha256"],
                    "similarity_threshold": lock["similarity_threshold"],
                },
            }
        )
    )
    archive_argv = [
        "bash",
        str(archive),
        "--sweep",
        str(sweep / "configs"),
        "--stack",
        STACK,
        "--run",
        "1",
        "--census",
        str(census),
    ]
    ledger_before = (out_dir / "ledger.json").read_text()
    proc = subprocess.run(
        archive_argv, env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode != 0
    assert "G11 refused" in proc.stderr and f"{BASENAME}.judged.json" in proc.stderr
    assert (out_dir / "ledger.json").read_text() == ledger_before

    # The publish copy-back puts the judged file beside the pending file.
    proc = _python(publish, "--out-dir", out_dir, "--queue", queue)
    assert proc.returncode == 0, proc.stderr
    judged = out_dir / f"{BASENAME}.judged.json"
    assert judged.read_bytes() == judged_in_queue.read_bytes()

    # 6. compare_runs: G11 refuses the pending file, accepts the judged file; G12 passes.
    capsys.readouterr()
    assert cr.main([str(pending)]) == cr.EXIT_GATE
    assert "G11 refused" in capsys.readouterr().err
    report = tmp_path / "report.json"
    assert (
        cr.main([str(judged), "--json", str(report)]) == cr.EXIT_OK
    ), capsys.readouterr().err
    gates = {gate["id"]: gate for gate in json.loads(report.read_text())["gates"]}
    assert gates["G11"]["status"] == "pass" and gates["G12"]["status"] == "pass"

    # 7. archive_run.sh --sweep: one ledger row per arm, each with its judge identity.
    proc = subprocess.run(
        archive_argv, env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    judged_doc = json.loads(judged.read_text())
    rows = [
        r
        for r in json.loads((out_dir / "ledger.json").read_text())
        if r["kind"] == "ragas"
    ]
    assert [row["arm"] for row in rows] == [Path(c).stem for c in configs]
    for row, arm in zip(rows, judged_doc["benchmarking_results"]):
        assert row["artifact"].endswith(judged.name)
        assert (
            row["ragas_judge_identity_effective"]
            == arm["ragas_judge_identity_effective"]
        )
        assert row["ragas_judge_identity_effective"]["provider"] == "vllm"
        assert row["ragas_judge_identity_effective"]["model"] == MODEL

    # The judged aggregates equal the inline aggregates for the same rows and judge.
    for bundle, arm in zip(bundles, judged_doc["benchmarking_results"]):
        inline = _inline_aggregates(bundle, monkeypatch)
        assert inline
        for name, value in inline.items():
            recorded = arm["total_results"][name]
            if isinstance(value, float) and math.isnan(value):
                assert recorded is None
            else:
                assert recorded == pytest.approx(value)
