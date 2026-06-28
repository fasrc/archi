"""Unit tests for the multi-config loader's cross-config consistency check.

A `--config-dir` benchmarking run sweeps prompt variants that differ only in
`services.benchmarking` (the prompt under test + its name). The loader must
accept that variation while still rejecting drift in `global` or in any other
`services.*` subsection (e.g. `services.chat_app`, which carries the SUT).
"""

import copy

import pytest

from src.cli.managers.config_manager import ConfigurationManager


def _manager():
    """A ConfigurationManager shell without running its file-loading __init__."""
    mgr = object.__new__(ConfigurationManager)
    mgr.configs = []
    return mgr


def _base_config():
    return {
        "name": "ragas-bench",
        "global": {"DATA_PATH": "/root/data/"},
        "services": {
            "chat_app": {"default_provider": "openai", "default_model": "qwen"},
            "benchmarking": {
                "agent_md_file": "config/agents/archive/fasrc-cannon-v1-strict.md",
                "name": "fasrc-cannon-v1-strict",
                "model": "qwen",
            },
        },
    }


def test_configs_differing_only_in_benchmarking_load():
    mgr = _manager()
    base = _base_config()
    variant = copy.deepcopy(base)
    variant["services"]["benchmarking"][
        "agent_md_file"
    ] = "config/agents/archive/fasrc-cannon-v2-lean.md"
    variant["services"]["benchmarking"]["name"] = "fasrc-cannon-v2-lean"

    mgr._append(base)
    mgr._append(variant)

    assert len(mgr.configs) == 2


def test_differing_global_still_rejected():
    mgr = _manager()
    base = _base_config()
    variant = copy.deepcopy(base)
    variant["global"]["DATA_PATH"] = "/somewhere/else/"

    mgr._append(base)
    with pytest.raises(
        ValueError, match="must be consistent across all configurations"
    ):
        mgr._append(variant)


def test_differing_non_benchmarking_service_still_rejected():
    mgr = _manager()
    base = _base_config()
    variant = copy.deepcopy(base)
    # Drift in services.chat_app (the SUT) must still be rejected.
    variant["services"]["chat_app"]["default_model"] = "different-model"

    mgr._append(base)
    with pytest.raises(
        ValueError, match="must be consistent across all configurations"
    ):
        mgr._append(variant)


def test_identical_configs_still_load():
    """Regression guard: the narrowed comparison must not change the equal case."""
    mgr = _manager()
    base = _base_config()
    mgr._append(base)
    mgr._append(copy.deepcopy(base))
    assert len(mgr.configs) == 2
