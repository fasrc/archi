"""Tests for crash-safe overwrite: D0 start guard and related invariants (#326)."""

from collections import deque

import pytest
from mcp.types import CallToolResult

import src.evaluation.qa.workflow as workflow_module
from src.evaluation.qa.oracle import OracleCallEvidence
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
