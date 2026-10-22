"""Tests for crash-safe overwrite: D0 start guard and related invariants (#326)."""

import copy
import json
from collections import deque

import pytest
from mcp.types import CallToolResult

import src.evaluation.qa.workflow as workflow_module
from src.evaluation.qa.artifacts import read_json
from src.evaluation.qa.history import EvaluationHistory
from src.evaluation.qa.oracle import OracleCallEvidence
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
