"""Judge bundle writer, plan, and start-refusal helpers (PR 2, issue #731).

Steps 1–3: judge_mode parsing and the one-mode rule.
Steps 4–7 (package/code digest checks) are in task 3.2.
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional, Sequence

import yaml

_ABSENT = object()
_ALLOWED = ("inline", "deferred")


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


def _read_mode(config_path: str) -> str:
    """Read judge_mode from one config YAML. Returns 'inline' for absent key."""
    with open(config_path, "r") as f:
        config = yaml.safe_load(f) or {}
    benchmarking = (config.get("services") or {}).get("benchmarking") or {}
    ragas = (benchmarking.get("mode_settings") or {}).get("ragas_settings") or {}
    if "judge_mode" in ragas:
        try:
            return parse_judge_mode(ragas["judge_mode"])
        except ValueError as exc:
            raise JudgeRunRefused(f"{config_path}: {exc}") from exc
    return parse_judge_mode()


def plan_judge_run(
    config_paths: Sequence[str],
    *,
    package_dir: str,
    git_info_path: str,
    package_digest_fn=None,
) -> JudgePlan:
    """Check judge_mode across all config files and return a JudgePlan (steps 1–3).

    Raises JudgeRunRefused if any config holds an invalid judge_mode.
    Raises JudgeRunRefused if configs disagree on mode (design D8a).
    """
    modes: List[str] = []
    for path in config_paths:
        modes.append(_read_mode(path))

    unique = set(modes)
    if len(unique) > 1:
        detail = ", ".join(f"{p} (mode={m!r})" for p, m in zip(config_paths, modes))
        raise JudgeRunRefused(
            f"All configs in a sweep must use the same judge_mode, but found "
            f"mixed modes: {detail}"
        )

    mode = modes[0] if modes else "inline"
    return JudgePlan(mode=mode, arm_count=len(config_paths))
