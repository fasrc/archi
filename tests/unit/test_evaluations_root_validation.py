"""Tests for evaluations root path validation."""

import pytest

from src.utils.evaluations_root import EVALUATIONS_MOUNT_PATH, validate_evaluations_root


def _config_with_root(root):
    return {"evaluations": {"enabled": True, "root": root}}


def test_outside_root_raises():
    with pytest.raises(ValueError) as exc_info:
        validate_evaluations_root(_config_with_root("/data/evaluations"))
    msg = str(exc_info.value)
    assert "/data/evaluations" in msg
    assert "/root/archi/evaluations" in msg


def test_exact_mount_returns_none():
    result = validate_evaluations_root(_config_with_root(EVALUATIONS_MOUNT_PATH))
    assert result is None


def test_child_of_mount_returns_none():
    result = validate_evaluations_root(
        _config_with_root("/root/archi/evaluations/trial-a")
    )
    assert result is None


def test_prefix_sibling_raises():
    with pytest.raises(ValueError):
        validate_evaluations_root(_config_with_root("/root/archi/evaluations-backup"))


def test_traversal_raises():
    with pytest.raises(ValueError):
        validate_evaluations_root(
            _config_with_root("/root/archi/evaluations/../elsewhere")
        )


def test_relative_root_raises():
    with pytest.raises(ValueError) as exc_info:
        validate_evaluations_root(_config_with_root("evaluations"))
    msg = str(exc_info.value)
    assert "evaluations" in msg
    assert "absolute" in msg
