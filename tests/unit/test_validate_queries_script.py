"""Unit tests for the standalone bank validator CLI
`scripts/benchmarking/validate_queries.py` (openspec change
`add-benchmark-bank-preflight`).

The script is a thin wrapper over `src.utils.benchmark_schema.preflight_bank_file`:
it loads a benchmark config, resolves the bank, and exits non-zero when the bank
fails the schema for the configured modes. These tests drive its `main()` so the
script's lines are covered and its exit contract is pinned.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "benchmarking"
    / "validate_queries.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("validate_queries", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_config(tmp_path, modes, queries_path):
    cfg = {
        "services": {
            "benchmarking": {
                "modes": modes,
                "queries_path": str(queries_path),
                "mode_settings": {
                    "ragas_settings": {"enabled_metrics": ["answer_relevancy"]}
                },
            }
        }
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def test_main_exits_nonzero_on_invalid_bank(tmp_path, capsys):
    bank = tmp_path / "bank.json"
    bank.write_text(json.dumps([{"user_input": "q"}]))  # missing sources
    cfg = _write_config(tmp_path, ["RAGAS", "SOURCES"], bank)

    rc = _load_script().main(["-c", str(cfg)])
    assert rc == 1
    assert "sources" in capsys.readouterr().out


def test_main_exits_zero_on_valid_bank(tmp_path, capsys):
    bank = tmp_path / "bank.json"
    bank.write_text(json.dumps([{"user_input": "q", "sources": ["https://x"]}]))
    cfg = _write_config(tmp_path, ["RAGAS", "SOURCES"], bank)

    rc = _load_script().main(["-c", str(cfg)])
    assert rc == 0


def test_main_queries_override_takes_precedence(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps([{"user_input": "q", "sources": ["https://x"]}]))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"user_input": "q"}]))
    # config points at the BAD bank; -q override points at the good one
    cfg = _write_config(tmp_path, ["RAGAS", "SOURCES"], bad)

    assert _load_script().main(["-c", str(cfg), "-q", str(good)]) == 0
