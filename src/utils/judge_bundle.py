"""Judge bundle writer, plan, and start-refusal helpers (PR 2, issue #731).

``plan_judge_run`` is the one start check of a benchmark run (design D3): it
parses ``judge_mode`` in every config, refuses a sweep that mixes modes, and for
a deferred run refuses every setting that would give bundles no scorer could
match. ``write_result_complete`` marks a deferred result as fully written (D9).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence

import yaml

from src.utils.benchmark_provenance import collect_code_version, collect_package_digest
from src.utils.benchmark_schema import RAGAS_METRIC_NAMES, ragas_run_config_kwargs

_ABSENT = object()
DEFAULT_BUNDLE_DIR = "judge_bundles"
BUNDLE_FORMAT = 1
_ROW_FIELDS = ("user_input", "retrieved_contexts", "response", "reference")


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


def _fsync_dir(folder: Path) -> None:
    """Make the names in *folder* durable."""
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_file(path: Path, data: bytes) -> None:
    """Create *path* (never replace it) with *data*, flushed and fsynced."""
    with open(path, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def write_judge_bundle(
    bundle_root: Path,
    *,
    result_basename: str,
    arm_index: int,
    arm_count: int,
    rows: Sequence[Mapping[str, Any]],
    keys: Sequence[str],
    enabled_metrics: Sequence[str],
    ragas_settings: Mapping[str, Any],
    verbosity: int,
    code_version: Mapping[str, Any],
    package_digest: str,
) -> Dict[str, str]:
    """Write the judge bundle of one deferred arm (design D5).

    Makes ``<bundle_root>/<result_basename>-arm<arm_index>/`` with
    ``rows.jsonl``, ``manifest.json`` and an empty ``BUNDLE_COMPLETE``, in that
    order. The folder must not exist: a bundle is never overwritten. Zero rows
    still give a complete bundle, because the merge waits for every arm.
    Returns ``{"folder": <folder name>, "digest": <rows_sha256>}``.
    """
    if len(rows) != len(keys):
        raise ValueError(
            f"a judge bundle needs one key per row, but got {len(rows)} rows "
            f"and {len(keys)} keys"
        )
    # ensure_ascii (the default) escapes every non-ASCII character, so a lone
    # surrogate in an answer cannot make the UTF-8 encode raise.
    rows_bytes = b"".join(
        (
            json.dumps(
                {"key": key, **{name: row[name] for name in _ROW_FIELDS}},
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for key, row in zip(keys, rows)
    )
    rows_sha256 = "sha256:" + hashlib.sha256(rows_bytes).hexdigest()
    manifest = {
        "bundle_format": BUNDLE_FORMAT,
        "result_basename": result_basename,
        "arm_index": arm_index,
        "arm_count": arm_count,
        "row_count": len(rows),
        "rows_sha256": rows_sha256,
        "enabled_metrics": [m for m in RAGAS_METRIC_NAMES if m in enabled_metrics],
        "ragas_run_config_kwargs": ragas_run_config_kwargs(
            dict(ragas_settings), verbosity
        ),
        "batch_size": ragas_settings.get("batch_size") or None,
        "embedding_model": ragas_settings.get("embedding_model"),
        "judge_model": {
            "id": ragas_settings.get("evaluator_model"),
            "revision": ragas_settings.get("evaluator_model_revision"),
        },
        "code_version": dict(code_version),
        "package_digest": package_digest,
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")

    folder = Path(bundle_root) / f"{result_basename}-arm{arm_index}"
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir()
    _fsync_dir(folder.parent)
    _write_file(folder / "rows.jsonl", rows_bytes)
    _write_file(folder / "manifest.json", manifest_bytes)
    # The names live in the folder: make them durable before the marker that
    # promises them, and the marker durable after it.
    _fsync_dir(folder)
    _write_file(folder / "BUNDLE_COMPLETE", b"")
    _fsync_dir(folder)
    return {"folder": folder.name, "digest": rows_sha256}


def pending_arm_fields(judge_bundle: Mapping[str, str]) -> Dict[str, Any]:
    """The two keys a deferred arm's result record gets (design D8)."""
    return {
        "judge_status": "pending",
        "judge_bundle": {
            "folder": judge_bundle["folder"],
            "digest": judge_bundle["digest"],
        },
    }


def judge_status_of(arm_record: Mapping[str, Any]) -> str:
    """The judge status of a result arm; a record with no status reads as inline."""
    return arm_record.get("judge_status") or "inline"


def write_result_complete(output_dir: Path, result_basename: str) -> Path:
    """Create the empty ``<result_basename>.RESULT_COMPLETE`` marker (design D9).

    Called only after ``dump_artifacts`` returns. The result JSON and its folder
    entry are fsynced first, so the marker never outlives a lost result. A
    missing result JSON raises, and no marker is written.
    """
    folder = Path(output_dir)
    fd = os.open(folder / f"{result_basename}.json", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(folder)
    path = folder / f"{result_basename}.RESULT_COMPLETE"
    with open(path, "wb") as handle:
        os.fsync(handle.fileno())
    _fsync_dir(folder)
    return path
