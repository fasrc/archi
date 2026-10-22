"""Tests for crash-safe overwrite: D0 start guard and related invariants (#326)."""

import copy
import json

import pytest

import src.evaluation.qa.workflow as workflow_module
from src.evaluation.qa.artifacts import read_json, sha256_file
from src.evaluation.qa.constants import RUN_FILES, SCORE_FILES
from src.evaluation.qa.history import EvaluationHistory
from src.evaluation.qa.live_checks import iter_live_checks
from src.evaluation.qa.schema import RunManifest
from src.evaluation.qa.workflow import QAWorkflow
from src.utils.benchmark_provenance import CollectionNotReadyError
from tests.unit.evaluation.qa.test_live_workflow import (
    SequenceInvoker,
    _paused_at_live_gate,
    _run,
    runtimes,
)


def _snapshot(run_dir):
    """Return {relative_path_str: bytes} for every file under run_dir."""
    return {
        str(p.relative_to(run_dir)): p.read_bytes()
        for p in sorted(run_dir.rglob("*"))
        if p.is_file()
    }


class TestD0StartGuard:
    def test_start_guard_refuses_before_delete(self, monkeypatch, tmp_path, runtimes):
        value = {"value": 7, "revision": "r1"}
        run_dir, _invoker, _rt = _run(
            monkeypatch, tmp_path, [value, value, value], runtimes
        )
        assert run_dir / "manifest.json" in list(run_dir.iterdir())

        before = _snapshot(run_dir)

        def _raising(config, spec):
            raise CollectionNotReadyError("not ready")

        monkeypatch.setattr(
            workflow_module.corpus_provenance,
            "start_readings",
            _raising,
        )

        with pytest.raises(CollectionNotReadyError):
            QAWorkflow().run(
                run_dir,
                tmp_path / "agent.yaml",
                tmp_path / "agent.md",
                overwrite=True,
            )

        after = _snapshot(run_dir)
        assert after == before

    def test_start_guard_sentinel_survives_prune(self, monkeypatch, tmp_path, runtimes):
        value = {"value": 7, "revision": "r1"}
        run_dir, _invoker, _rt = _run(
            monkeypatch, tmp_path, [value, value, value], runtimes
        )

        overwrite_invoker = SequenceInvoker([value, value])
        monkeypatch.setattr(
            workflow_module.EvaluatorMCPRegistry,
            "load",
            classmethod(lambda cls, path=None: overwrite_invoker),
        )

        sentinel_readings = {
            "retrieval_identity": {"sentinel": "fresh"},
            "corpus_fingerprint_before": "sha256/v2:fresh",
        }
        monkeypatch.setattr(
            workflow_module.corpus_provenance,
            "start_readings",
            lambda config, spec: sentinel_readings,
        )

        result = QAWorkflow().run(
            run_dir,
            tmp_path / "agent.yaml",
            tmp_path / "agent.md",
            overwrite=True,
        )

        assert result["retrieval_identity"] == {"sentinel": "fresh"}
        assert result["corpus_fingerprint_before"] == "sha256/v2:fresh"


def _install_manifest_recorder(monkeypatch):
    """Monkeypatch write_json to deep-copy every manifest.json payload before writing."""
    from src.evaluation.qa.artifacts import write_json as _real_write_json

    captured = []

    def _rec(path, payload):
        if path.name == "manifest.json":
            captured.append(copy.deepcopy(payload))
        _real_write_json(path, payload)

    monkeypatch.setattr(workflow_module, "write_json", _rec)
    return captured


class TestD1RunValidManifest:
    def test_continue_all_published_manifests_are_valid(
        self, monkeypatch, tmp_path, runtimes
    ):
        _workflow, run_dir = _paused_at_live_gate(monkeypatch, tmp_path)
        captured = _install_manifest_recorder(monkeypatch)
        result = _workflow.run(
            run_dir,
            tmp_path / "agent.yaml",
            tmp_path / "agent.md",
            overwrite=True,
            pause_on_live_mismatch=True,
            authorize_staged_invalid=True,
        )
        assert len(captured) > 1
        for payload in captured:
            RunManifest.from_dict(payload)
        assert result["status"] == "run_completed"

    def test_plain_overwrite_all_published_manifests_are_valid(
        self, monkeypatch, tmp_path, runtimes
    ):
        value = {"value": 7, "revision": "r1"}
        run_dir, _invoker, _rt = _run(monkeypatch, tmp_path, [value] * 3, runtimes)

        overwrite_invoker = SequenceInvoker([value, value])
        monkeypatch.setattr(
            workflow_module.EvaluatorMCPRegistry,
            "load",
            classmethod(lambda cls, path=None: overwrite_invoker),
        )
        captured = _install_manifest_recorder(monkeypatch)
        result = QAWorkflow().run(
            run_dir,
            tmp_path / "agent.yaml",
            tmp_path / "agent.md",
            overwrite=True,
        )
        assert len(captured) > 1
        for payload in captured:
            RunManifest.from_dict(payload)
        assert result["status"] == "run_completed"

    def test_failure_mid_overwrite_leaves_valid_manifest(
        self, monkeypatch, tmp_path, runtimes
    ):
        _workflow, run_dir = _paused_at_live_gate(monkeypatch, tmp_path)
        real_observe = workflow_module.observe_live_item
        call_count = [0]

        def _fake_observe(prepared, resolver, phase):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("injected")
            return real_observe(prepared, resolver, phase)

        monkeypatch.setattr(workflow_module, "observe_live_item", _fake_observe)

        with pytest.raises(RuntimeError, match="injected"):
            _workflow.run(
                run_dir,
                tmp_path / "agent.yaml",
                tmp_path / "agent.md",
                overwrite=True,
                pause_on_live_mismatch=True,
                authorize_staged_invalid=True,
            )

        RunManifest.from_dict(read_json(run_dir / "manifest.json"))

        history = EvaluationHistory(tmp_path)
        (row,) = [
            r for r in history.list_runs() if r["id"] == history.id_for_path(run_dir)
        ]
        assert row["status"] == "prepared"
        assert row["valid"] is True


def _dataset_two_live(path):
    """Dataset with static + two live items for re-pause testing (3.3)."""
    items = [
        {
            "id": "static",
            "question": "Fixed?",
            "answer": "fixed",
            "time_sensitive": False,
            "expected_atoms": [{"id": "fixed", "text": "fixed", "required": True}],
        },
        {
            "id": "live",
            "question": "Current value?",
            "time_sensitive": True,
            "oracle": {
                "kind": "mcp",
                "calls": [
                    {
                        "id": "lookup",
                        "server": "read-model",
                        "tool": "current",
                        "arguments": {},
                        "answer_fields": {"value": "/value"},
                        "metadata_fields": {"revision": "/revision"},
                    }
                ],
            },
        },
        {
            "id": "live-b",
            "question": "Other value?",
            "time_sensitive": True,
            "oracle": {
                "kind": "mcp",
                "calls": [
                    {
                        "id": "lookup",
                        "server": "read-model",
                        "tool": "current",
                        "arguments": {},
                        "answer_fields": {"value": "/value"},
                        "metadata_fields": {"revision": "/revision"},
                    }
                ],
            },
        },
    ]
    path.write_text(
        json.dumps({"schema_version": "qa-dataset-v2", "items": items}),
        encoding="utf-8",
    )


class TestD1ScoreValidManifest:
    def test_score_overwrite_all_published_manifests_are_valid(
        self, monkeypatch, tmp_path, runtimes
    ):
        value = {"value": 7, "revision": "r1"}
        run_dir, _invoker, _rt = _run(monkeypatch, tmp_path, [value] * 3, runtimes)

        captured = _install_manifest_recorder(monkeypatch)
        result = QAWorkflow().score(run_dir, overwrite=True)

        assert len(captured) > 1
        for payload in captured:
            RunManifest.from_dict(payload)
        assert captured[0]["status"] == "run_completed"
        assert result["status"] == "scored"


class TestD1RunRepause:
    def test_repause_all_published_manifests_are_valid(
        self, monkeypatch, tmp_path, runtimes
    ):
        dataset = tmp_path / "dataset.json"
        run_dir = tmp_path / "run"
        _dataset_two_live(dataset)

        invoker = SequenceInvoker(
            [
                {"value": 7, "revision": "r1"},  # prepare live
                {"value": 7, "revision": "r1"},  # prepare live-b
                {"value": 8, "revision": "r2"},  # pre-run live (mismatch -> pause)
                {"value": 7, "revision": "r1"},  # pre-run live-b (no mismatch)
                {"value": 8, "revision": "r2"},  # continue's pre-run live (authorized)
                {
                    "value": 9,
                    "revision": "r3",
                },  # continue's pre-run live-b (mismatch -> re-pause)
            ]
        )
        monkeypatch.setattr(
            workflow_module.EvaluatorMCPRegistry,
            "load",
            classmethod(lambda cls, path=None: invoker),
        )
        workflow = QAWorkflow()
        first = workflow.composite(
            dataset,
            tmp_path / "agent.yaml",
            tmp_path / "agent.md",
            run_dir,
            pause_on_live_mismatch=True,
        )
        assert first["status"] == "attention_required", invoker.calls

        captured = _install_manifest_recorder(monkeypatch)
        result = workflow.run(
            run_dir,
            tmp_path / "agent.yaml",
            tmp_path / "agent.md",
            overwrite=True,
            pause_on_live_mismatch=True,
            authorize_staged_invalid=True,
        )

        assert len(captured) > 1
        for payload in captured:
            RunManifest.from_dict(payload)
        assert result["status"] == "attention_required"
        # 4.5: re-pause publish records the live_checks.jsonl digest equal to the file on disk
        last_payload = captured[-1]
        assert last_payload["artifacts"]["live_checks.jsonl"] == sha256_file(
            run_dir / "live_checks.jsonl"
        )


class TestD2D3StagedChecksWindow:
    """D2+D3: staged live_checks.jsonl and its digest survive the overwrite window."""

    def _raising_observe_fake(self, monkeypatch):
        """Returns a fake observe_live_item that raises on the first call then delegates."""
        real_observe = workflow_module.observe_live_item
        call_count = [0]

        def _fake(prepared, resolver, phase):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("injected")
            return real_observe(prepared, resolver, phase)

        monkeypatch.setattr(workflow_module, "observe_live_item", _fake)
        return call_count

    def test_live_checks_preserved_on_failure(self, monkeypatch, tmp_path, runtimes):
        """4.1: staged live_checks.jsonl survives a mid-window failure on the continue path."""
        _workflow, run_dir = _paused_at_live_gate(monkeypatch, tmp_path)
        original_bytes = (run_dir / "live_checks.jsonl").read_bytes()

        captured = _install_manifest_recorder(monkeypatch)
        self._raising_observe_fake(monkeypatch)

        with pytest.raises(RuntimeError, match="injected"):
            _workflow.run(
                run_dir,
                tmp_path / "agent.yaml",
                tmp_path / "agent.md",
                overwrite=True,
                pause_on_live_mismatch=True,
                authorize_staged_invalid=True,
            )

        assert len(captured) == 1
        assert (run_dir / "live_checks.jsonl").read_bytes() == original_bytes
        assert captured[0]["artifacts"]["live_checks.jsonl"] == sha256_file(
            run_dir / "live_checks.jsonl"
        )

    def test_subsequent_continue_passes_input_verification(
        self, monkeypatch, tmp_path, runtimes
    ):
        """4.2: after a failed continue the workspace loads and a fresh continue succeeds."""
        _workflow, run_dir = _paused_at_live_gate(monkeypatch, tmp_path)
        self._raising_observe_fake(monkeypatch)

        with pytest.raises(RuntimeError):
            _workflow.run(
                run_dir,
                tmp_path / "agent.yaml",
                tmp_path / "agent.md",
                overwrite=True,
                pause_on_live_mismatch=True,
                authorize_staged_invalid=True,
            )

        # call_count[0] is now >= 1, so the fake delegates on all subsequent calls
        result = _workflow.run(
            run_dir,
            tmp_path / "agent.yaml",
            tmp_path / "agent.md",
            overwrite=True,
            pause_on_live_mismatch=True,
            authorize_staged_invalid=True,
        )
        assert result["status"] in {"run_completed", "attention_required"}

    def test_plain_overwrite_removes_staged_checks(
        self, monkeypatch, tmp_path, runtimes
    ):
        """4.4: --overwrite without authorize_staged_invalid still removes live_checks.jsonl."""
        _workflow, run_dir = _paused_at_live_gate(monkeypatch, tmp_path)
        assert (run_dir / "live_checks.jsonl").is_file()

        self._raising_observe_fake(monkeypatch)

        with pytest.raises(RuntimeError):
            _workflow.run(
                run_dir,
                tmp_path / "agent.yaml",
                tmp_path / "agent.md",
                overwrite=True,
                pause_on_live_mismatch=True,
            )

        assert not (run_dir / "live_checks.jsonl").is_file()
        manifest = read_json(run_dir / "manifest.json")
        assert "live_checks.jsonl" not in manifest.get("artifacts", {})

    def test_normal_continue_live_checks_digest_matches(
        self, monkeypatch, tmp_path, runtimes
    ):
        """4.5: a normal complete continue writes live_checks.jsonl with the correct digest."""
        _workflow, run_dir = _paused_at_live_gate(monkeypatch, tmp_path)

        result = _workflow.run(
            run_dir,
            tmp_path / "agent.yaml",
            tmp_path / "agent.md",
            overwrite=True,
            pause_on_live_mismatch=True,
            authorize_staged_invalid=True,
        )

        assert result["status"] in {"run_completed", "attention_required"}
        live_checks = run_dir / "live_checks.jsonl"
        assert live_checks.is_file()
        assert result["artifacts"]["live_checks.jsonl"] == sha256_file(live_checks)
        rows = list(iter_live_checks(live_checks))
        assert len(rows) >= 1


@pytest.mark.parametrize(
    "artifact",
    ["agent_config.resolved.yaml", "agent_spec.resolved.md"],
)
class TestD3AgentInputDigests:
    """4a: a recovery continue must verify the frozen agent inputs it rewrote."""

    def test_recovery_continue_refuses_tampered_agent_inputs(
        self, monkeypatch, tmp_path, runtimes, artifact
    ):
        """4a.1: after a failed overwrite-continue, the subsequent continue detects tampering."""
        _workflow, run_dir = _paused_at_live_gate(monkeypatch, tmp_path)

        real_observe = workflow_module.observe_live_item
        call_count = [0]

        def _fake(prepared, resolver, phase):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("injected")
            return real_observe(prepared, resolver, phase)

        monkeypatch.setattr(workflow_module, "observe_live_item", _fake)

        with pytest.raises(RuntimeError, match="injected"):
            _workflow.run(
                run_dir,
                tmp_path / "agent.yaml",
                tmp_path / "agent.md",
                overwrite=True,
                pause_on_live_mismatch=True,
                authorize_staged_invalid=True,
            )

        target = run_dir / artifact
        target.write_text(
            target.read_text(encoding="utf-8") + "\ntampered: true\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError) as caught:
            _workflow.run(
                run_dir,
                tmp_path / "agent.yaml",
                tmp_path / "agent.md",
                overwrite=True,
                pause_on_live_mismatch=True,
                authorize_staged_invalid=True,
            )

        assert str(caught.value) == f"workspace artifact hash mismatch: {artifact}"


class TestValidatorFence:
    """5.1: Validator refuses pre-fix states; pins from_dict so widening it breaks this test."""

    @pytest.mark.parametrize(
        "prior_status", ["scored", "run_completed", "attention_required"]
    )
    def test_run_prune_refused_with_prior_status(
        self, monkeypatch, tmp_path, runtimes, prior_status
    ):
        value = {"value": 7, "revision": "r1"}
        run_dir, _invoker, _rt = _run(monkeypatch, tmp_path, [value] * 3, runtimes)
        manifest = read_json(run_dir / "manifest.json")

        # Apply the run() overwrite prune WITHOUT the status fix.
        pruned = copy.deepcopy(manifest)
        pruned["status"] = prior_status
        pruned["phases"].pop("run", None)
        pruned["phases"].pop("score", None)
        pruned.pop("attempts", None)
        pruned.pop("agent", None)
        pruned.pop("attention_required", None)
        for key in workflow_module.corpus_provenance.PROVENANCE_KEYS:
            pruned.pop(key, None)
        for key in workflow_module.corpus_provenance.TAG_KEYS:
            pruned.pop(key, None)
        for name in RUN_FILES | SCORE_FILES:
            pruned["artifacts"].pop(name, None)

        if prior_status in ("scored", "run_completed"):
            expected_msg = "manifest phase state is incomplete"
        else:
            expected_msg = "manifest attempts must be a positive integer"

        with pytest.raises(ValueError, match=expected_msg):
            RunManifest.from_dict(pruned)

        # Setting status to "prepared" makes it accepted.
        pruned["status"] = "prepared"
        RunManifest.from_dict(pruned)

    def test_score_prune_refused_with_wrong_status(
        self, monkeypatch, tmp_path, runtimes
    ):
        value = {"value": 7, "revision": "r1"}
        run_dir, _invoker, _rt = _run(monkeypatch, tmp_path, [value] * 3, runtimes)
        manifest = read_json(run_dir / "manifest.json")

        # Apply the score() overwrite prune WITHOUT the status fix.
        pruned = copy.deepcopy(manifest)
        pruned["phases"].pop("score", None)
        for name in SCORE_FILES:
            pruned["artifacts"].pop(name, None)

        with pytest.raises(ValueError, match="manifest phase state is incomplete"):
            RunManifest.from_dict(pruned)

        # Demoted to run_completed is accepted.
        pruned["status"] = "run_completed"
        RunManifest.from_dict(pruned)


class TestHappyPathAuditSweep:
    """5.2: Every manifest.json published on the happy path and first-run pause validates."""

    def test_happy_path_all_manifests_valid(self, monkeypatch, tmp_path, runtimes):
        value = {"value": 7, "revision": "r1"}
        captured = _install_manifest_recorder(monkeypatch)
        _run(monkeypatch, tmp_path, [value] * 3, runtimes)
        assert len(captured) > 1
        for payload in captured:
            RunManifest.from_dict(payload)

    def test_first_run_pause_manifest_valid(self, monkeypatch, tmp_path, runtimes):
        captured = _install_manifest_recorder(monkeypatch)
        _paused_at_live_gate(monkeypatch, tmp_path)
        pause_docs = [p for p in captured if p.get("status") == "attention_required"]
        assert pause_docs, "expected at least one attention_required manifest"
        for payload in captured:
            RunManifest.from_dict(payload)
