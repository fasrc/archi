"""Tests that JudgeRunRefused is a ValueError subclass (task 3.1a, review finding).

Design refs: D3 — plan_judge_run returns JudgePlan or raises JudgeRunRefused,
"a ValueError subclass".
"""

from __future__ import annotations

import pytest
import yaml

from src.utils.judge_bundle import (
    JudgeRunRefused,
    plan_judge_run,
)

_PACKAGE_DIR = "."
_GIT_INFO = "/dev/null"


def _cfg(tmp_path, name, *, judge_mode=None, include_key=True):
    ragas: dict = {}
    if include_key:
        ragas["judge_mode"] = judge_mode
    cfg = {"services": {"benchmarking": {"mode_settings": {"ragas_settings": ragas}}}}
    p = tmp_path / name
    p.write_text(yaml.safe_dump(cfg))
    return p


def test_judge_run_refused_is_subclass_of_value_error():
    assert issubclass(JudgeRunRefused, ValueError)


def test_mixed_modes_caught_as_value_error_type_is_judge_run_refused(tmp_path):
    cfg_deferred = _cfg(tmp_path, "a.yaml", judge_mode="deferred")
    cfg_absent = _cfg(tmp_path, "b.yaml", include_key=False)
    caught = None
    try:
        plan_judge_run(
            [str(cfg_deferred), str(cfg_absent)],
            package_dir=_PACKAGE_DIR,
            git_info_path=_GIT_INFO,
        )
    except ValueError as exc:
        caught = exc
    assert caught is not None, "expected a ValueError (or subclass) to be raised"
    assert type(caught) is JudgeRunRefused


def test_invalid_judge_mode_in_file_raises_judge_run_refused(tmp_path):
    cfg = _cfg(tmp_path, "a.yaml", judge_mode="Deferred")
    with pytest.raises(JudgeRunRefused) as exc_info:
        plan_judge_run(
            [str(cfg)],
            package_dir=_PACKAGE_DIR,
            git_info_path=_GIT_INFO,
        )
    msg = str(exc_info.value)
    assert str(cfg) in msg
    assert "Deferred" in msg


def test_null_judge_mode_in_file_raises_judge_run_refused(tmp_path):
    cfg = _cfg(tmp_path, "a.yaml", judge_mode=None, include_key=True)
    with pytest.raises(JudgeRunRefused) as exc_info:
        plan_judge_run(
            [str(cfg)],
            package_dir=_PACKAGE_DIR,
            git_info_path=_GIT_INFO,
        )
    msg = str(exc_info.value)
    assert str(cfg) in msg
