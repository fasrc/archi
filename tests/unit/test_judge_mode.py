"""Tests for judge_mode parsing and plan_judge_run mode checks (task 1.1).

Design refs: D2 (one-mode rule) and D3 steps 1–3.
"""

from __future__ import annotations

import pytest
import yaml

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import Benchmarker
from src.utils.judge_bundle import (
    JudgePlan,
    JudgeRunRefused,
    parse_judge_mode,
    plan_judge_run,
)

_PACKAGE_DIR = "."
_GIT_INFO = "/dev/null"


def _cfg(tmp_path, name, *, judge_mode=None, include_key=True):
    """Write a minimal config YAML to tmp_path/name.

    When include_key is False the ragas_settings block has no judge_mode key.
    """
    ragas: dict = {}
    if include_key:
        ragas["judge_mode"] = judge_mode
    cfg = {"services": {"benchmarking": {"mode_settings": {"ragas_settings": ragas}}}}
    p = tmp_path / name
    p.write_text(yaml.safe_dump(cfg))
    return p


# ---------------------------------------------------------------------------
# parse_judge_mode
# ---------------------------------------------------------------------------


def test_absent_gives_inline():
    assert parse_judge_mode() == "inline"


def test_string_inline_gives_inline():
    assert parse_judge_mode("inline") == "inline"


def test_string_deferred_gives_deferred():
    assert parse_judge_mode("deferred") == "deferred"


@pytest.mark.parametrize("bad", ["", False, None, "Deferred", "offline"])
def test_invalid_raises_value_error_names_value_and_both_allowed(bad):
    with pytest.raises(ValueError) as exc_info:
        parse_judge_mode(bad)
    msg = str(exc_info.value)
    assert repr(bad) in msg
    assert "inline" in msg
    assert "deferred" in msg


# ---------------------------------------------------------------------------
# plan_judge_run – mode part (steps 1–3 only)
# ---------------------------------------------------------------------------


def test_no_key_and_inline_gives_inline_arm_count_2(tmp_path):
    cfg1 = _cfg(tmp_path, "a.yaml", include_key=False)
    cfg2 = _cfg(tmp_path, "b.yaml", judge_mode="inline")
    plan = plan_judge_run(
        [str(cfg1), str(cfg2)],
        package_dir=_PACKAGE_DIR,
        git_info_path=_GIT_INFO,
    )
    assert plan.mode == "inline"
    assert plan.arm_count == 2


def test_deferred_and_absent_raises_judge_run_refused(tmp_path):
    cfg_deferred = _cfg(tmp_path, "deferred.yaml", judge_mode="deferred")
    cfg_absent = _cfg(tmp_path, "nokey.yaml", include_key=False)
    with pytest.raises(JudgeRunRefused) as exc_info:
        plan_judge_run(
            [str(cfg_deferred), str(cfg_absent)],
            package_dir=_PACKAGE_DIR,
            git_info_path=_GIT_INFO,
        )
    msg = str(exc_info.value)
    assert str(cfg_deferred) in msg
    assert str(cfg_absent) in msg
    assert "deferred" in msg
    assert "inline" in msg


def test_invalid_value_raises_and_names_path(tmp_path):
    cfg = _cfg(tmp_path, "bad.yaml", judge_mode="offline")
    with pytest.raises((ValueError, JudgeRunRefused)) as exc_info:
        plan_judge_run(
            [str(cfg)],
            package_dir=_PACKAGE_DIR,
            git_info_path=_GIT_INFO,
        )
    msg = str(exc_info.value)
    assert str(cfg) in msg


# ---------------------------------------------------------------------------
# Benchmarker integration – mixed sweep raises before load_new_configuration
# ---------------------------------------------------------------------------


def test_benchmarker_mixed_sweep_refuses_before_load_new_configuration(
    tmp_path, monkeypatch
):
    """A deferred/inline mixed sweep must raise JudgeRunRefused before
    load_new_configuration is called."""
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    _cfg(configs_dir, "arm0.yaml", judge_mode="deferred")
    _cfg(configs_dir, "arm1.yaml", include_key=False)  # no key → inline

    load_calls = []
    monkeypatch.setattr(
        Benchmarker,
        "load_new_configuration",
        lambda self: load_calls.append(1),
    )
    monkeypatch.setenv("container_name", "test-bench")

    with pytest.raises(JudgeRunRefused):
        Benchmarker(configs_dir, {})

    assert (
        load_calls == []
    ), "load_new_configuration must not be called before JudgeRunRefused"
