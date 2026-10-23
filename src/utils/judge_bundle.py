"""Judge bundle writer, plan, and start-refusal helpers (PR 2, issue #731).

``plan_judge_run`` is the one start check of a benchmark run (design D3): it
parses ``judge_mode`` in every config, refuses a sweep that mixes modes, and for
a deferred run refuses every setting that would give bundles no scorer could
match. ``write_result_complete`` marks a deferred result as fully written (D9).
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence

import yaml

from src.utils.benchmark_provenance import collect_code_version, collect_package_digest

_ABSENT = object()
_ALLOWED = ("inline", "deferred")
DEFAULT_BUNDLE_DIR = "judge_bundles"


class JudgeRunRefused(ValueError):
    """Raised when a benchmark sweep cannot start due to incompatible judge configs."""


def parse_judge_mode(value=_ABSENT) -> str:
    """Parse a judge_mode config value.

    Call with no argument when the key is absent from the config; returns
    'inline'.  Raises ValueError for any value that is not 'inline' or
    'deferred', naming the value and both allowed values.
    """
    if value is _ABSENT:
        return "inline"
    if value == "inline":
        return "inline"
    if value == "deferred":
        return "deferred"
    raise ValueError(
        f"judge_mode {value!r} is not valid; allowed values are 'inline' and 'deferred'"
    )


@dataclasses.dataclass(frozen=True)
class JudgePlan:
    mode: str
    arm_count: int
    bundle_dir: Optional[str] = None
    code_version: Optional[Dict[str, Any]] = None
    package_digest: Optional[str] = None


def is_deferred(plan: Optional[JudgePlan]) -> bool:
    """True for a deferred plan. ``None`` (a bench built without ``__init__``) is inline."""
    return plan is not None and plan.mode == "deferred"


def _read_ragas_settings(config_path: str) -> Dict[str, Any]:
    """``services.benchmarking.mode_settings.ragas_settings`` of one config YAML."""
    with open(config_path, "r") as f:
        config = yaml.safe_load(f) or {}
    benchmarking = (config.get("services") or {}).get("benchmarking") or {}
    return (benchmarking.get("mode_settings") or {}).get("ragas_settings") or {}


def _read_mode(config_path: str, ragas: Dict[str, Any]) -> str:
    """Read judge_mode from one config's ragas_settings. Returns 'inline' for absent key."""
    if "judge_mode" in ragas:
        try:
            return parse_judge_mode(ragas["judge_mode"])
        except ValueError as exc:
            raise JudgeRunRefused(f"{config_path}: {exc}") from exc
    return parse_judge_mode()


def _check_judge_model(config_path: str, ragas: Dict[str, Any]) -> None:
    """Refuse a deferred config that does not name the judge model and its revision."""
    for key in ("evaluator_model", "evaluator_model_revision"):
        value = ragas.get(key)
        if not isinstance(value, str) or not value.strip():
            raise JudgeRunRefused(
                f"{config_path}: judge_mode 'deferred' needs a non-empty string "
                f"{key}, but found {value!r}"
            )


def _resolve_bundle_dir(config_path: str, ragas: Dict[str, Any]) -> str:
    """The config's bundle folder, relative to the output folder (absent key: default).

    A bundle outside the output folder is outside the container's bind mount and
    is lost with the container, so an absolute path or a ``..`` part refuses.
    """
    value = ragas.get("judge_bundle_dir", DEFAULT_BUNDLE_DIR)
    path = PurePosixPath(value) if isinstance(value, str) else None
    if path is None or not path.parts or path.is_absolute() or ".." in path.parts:
        raise JudgeRunRefused(
            f"{config_path}: judge_bundle_dir {value!r} must be a non-empty relative "
            f"path inside the benchmark output folder, with no '..' part"
        )
    return str(path)


def _read_git_info(git_info_path: str) -> Any:
    """The deploy git info, read as ``add_metadata`` reads it (an OSError gives None)."""
    try:
        with open(git_info_path, "r") as f:
            return yaml.safe_load(f)
    except OSError:
        return None


def plan_judge_run(
    config_paths: Sequence[str],
    *,
    package_dir: str,
    git_info_path: str,
    package_digest_fn=None,
) -> JudgePlan:
    """Check the judge settings of every config and return a JudgePlan (design D3).

    Raises JudgeRunRefused if any config holds an invalid judge_mode, if the
    configs disagree on mode (design D8a), or, for a deferred run, if a config
    lacks the judge model or its revision, if the configs do not resolve to one
    valid judge_bundle_dir, or if the code digest or the package digest is
    unavailable (no scorer could match the bundles). An inline run computes no
    digest. ``package_digest_fn`` defaults to ``collect_package_digest``.
    """
    settings = [_read_ragas_settings(path) for path in config_paths]
    modes: List[str] = [
        _read_mode(path, ragas) for path, ragas in zip(config_paths, settings)
    ]

    unique = set(modes)
    if len(unique) > 1:
        detail = ", ".join(f"{p} (mode={m!r})" for p, m in zip(config_paths, modes))
        raise JudgeRunRefused(
            f"All configs in a sweep must use the same judge_mode, but found "
            f"mixed modes: {detail}"
        )

    mode = modes[0] if modes else "inline"
    if mode == "inline":
        return JudgePlan(mode=mode, arm_count=len(config_paths))

    bundle_dirs: List[str] = []
    for path, ragas in zip(config_paths, settings):
        _check_judge_model(path, ragas)
        bundle_dirs.append(_resolve_bundle_dir(path, ragas))
    if len(set(bundle_dirs)) > 1:
        detail = ", ".join(
            f"{p} (judge_bundle_dir={d!r})" for p, d in zip(config_paths, bundle_dirs)
        )
        raise JudgeRunRefused(
            f"All configs of a deferred run must resolve to the same "
            f"judge_bundle_dir, but found: {detail}"
        )

    code_version = collect_code_version(package_dir, _read_git_info(git_info_path))
    if code_version["digest"] is None:
        raise JudgeRunRefused(
            f"A deferred run needs a code digest so that a scorer can match its "
            f"bundles, but code_version is {code_version['source']}"
        )
    package = (package_digest_fn or collect_package_digest)()
    if package.get("digest") is None:
        raise JudgeRunRefused(
            f"A deferred run needs a package digest so that a scorer can match its "
            f"bundles, but the package digest is {package.get('source')}"
        )

    return JudgePlan(
        mode=mode,
        arm_count=len(config_paths),
        bundle_dir=bundle_dirs[0],
        code_version=code_version,
        package_digest=package["digest"],
    )


def write_result_complete(output_dir: Path, result_basename: str) -> Path:
    """Create the empty ``<result_basename>.RESULT_COMPLETE`` marker (design D9).

    Called only after ``dump_artifacts`` returns, so the marker exists only when
    the result JSON is complete.
    """
    path = Path(output_dir) / f"{result_basename}.RESULT_COMPLETE"
    with open(path, "wb") as handle:
        os.fsync(handle.fileno())
    return path
