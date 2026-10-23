"""Acceptance guards of #734: no GPU stack at import, and Lynx is not a RAGAS metric."""

import json
import subprocess
import sys
from pathlib import Path

from scripts.benchmarking import compare_runs as cr
from src.utils.benchmark_schema import RAGAS_METRIC_NAMES

REPO_ROOT = Path(__file__).resolve().parents[2]

# Each name set to None in sys.modules makes any import of it raise ImportError.
_BLOCKED_IMPORT = """
import sys
sys.modules["vllm"] = None
sys.modules["transformers"] = None
import scripts.benchmarking.lynx.score
import scripts.benchmarking.lynx.report
print("imported")
"""


def test_the_scorer_imports_without_vllm_or_transformers():
    result = subprocess.run(
        [sys.executable, "-c", _BLOCKED_IMPORT],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "imported"


def test_lynx_is_not_a_ragas_metric():
    schema = (REPO_ROOT / "src" / "utils" / "benchmark_schema.py").read_text()
    assert "lynx" not in schema.lower()
    assert not [m for m in RAGAS_METRIC_NAMES if "lynx" in m.lower()]
    assert not [m for m in cr.METRICS if "lynx" in m.lower()]


def test_the_benchmark_service_does_not_name_lynx():
    # LEADERBOARD_METRICS and every release gate live here or read RAGAS_METRIC_NAMES.
    service = (REPO_ROOT / "src" / "bin" / "service_benchmark.py").read_text()
    assert "lynx" not in service.lower()


def _artifact(tmp_path):
    row = {
        "question": "Q1?",
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
    entries = [
        {
            "single_question_results": {"question_1": row},
            "total_results": {},
            "configuration_file": f"{name}.yaml",
            "configuration": {"services": {"benchmarking": {"name": name}}},
            "config_version": {
                "digest": "sha256:c",
                "divergence_from_selected_file": [],
            },
            "corpus_fingerprint": "sha256:corpus",
            "corpus_fingerprint_before": "sha256:corpus",
            "corpus_unchanged_at_endpoints": True,
        }
        for name in ("base", "arm")
    ]
    path = tmp_path / "bench-20260101_000000.json"
    path.write_text(
        json.dumps(
            {
                "benchmarking_results": entries,
                "metadata": {"corpus_fingerprint": "sha256:corpus", "code_version": {}},
            }
        )
    )
    lynx = {
        "model_id": "lynx",
        "model_revision": "r",
        "backend": "vllm",
        "result_basename": "bench-20260101_000000",
        "arm_index": 0,
        "bundle_rows_sha256": "sha256:rows",
        "token_budget": {"context_tokens": 8000, "max_new_tokens": 600},
        "rows": {
            "question_1": {
                "verdict": "PASS",
                "status": "scored",
                "reasoning": None,
                "prompt_tokens": 5,
            }
        },
        "counts": {
            "lynx_scored": 1,
            "lynx_pass": 1,
            "lynx_skipped_overflow": 0,
            "lynx_parse_errors": 0,
        },
        "lynx_faithfulness": 1.0,
    }
    (tmp_path / "bench-20260101_000000-arm0.lynx.json").write_text(json.dumps(lynx))
    anchors = tmp_path / "anchors.json"
    anchors.write_text("[]")
    return path, anchors


def test_compare_runs_as_a_plain_script_shows_the_lynx_block(tmp_path):
    path, anchors = _artifact(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "benchmarking" / "compare_runs.py"),
            str(path),
            "--anchors",
            str(anchors),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "## Lynx faithfulness (descriptive)" in result.stdout
