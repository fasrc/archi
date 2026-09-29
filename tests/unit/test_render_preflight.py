"""Tests for TemplateManager.preflight_render (issue #294)."""

import tempfile
from pathlib import Path
from typing import List

import pytest
import yaml
from click.testing import CliRunner

from tests.unit.test_cli_create_dev_smoke import (
    EXAMPLE_CONFIG,
    SENTINEL,
    _existing_deployment,
    _record_teardowns,
    archi_home,
    benchmark_config,
    env_file,
    fake_repo_root,
)


def _satisfied_base_images(monkeypatch):
    from src.cli.managers import base_image_preflight

    class _Probe:
        def __init__(self, *a, **kw):
            pass

        def runtime_available(self):
            return True

        def image_present(self, r):
            return True

        def pull(self, r):
            return None

        def reachable(self, r):
            return None

        def python_version(self, r):
            return "Python 3.11.9"

    monkeypatch.setattr(base_image_preflight, "ContainerProbe", _Probe)


def _capture_tm_args(
    monkeypatch,
    env_file_path,
    *,
    config_path=None,
    bypass_port_check=False,
):
    """Run cli_main.create and capture the TemplateManager instance and its args.

    Patches: ContainerProbe→satisfied, check_docker_available→True,
    VolumeManager.create_required_volumes→no-op, TemplateManager._probe_port→None.
    Returns captured dict with keys: tm, plan, config_manager, secrets_manager, options.
    """
    cfg = config_path or EXAMPLE_CONFIG

    from src.cli import cli_main
    from src.cli.managers.templates_manager import TemplateManager
    from src.cli.managers.volume_manager import VolumeManager

    _satisfied_base_images(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        VolumeManager, "create_required_volumes", lambda self, *a, **kw: None
    )
    monkeypatch.setattr(TemplateManager, "_probe_port", lambda self, port: None)

    if bypass_port_check:
        monkeypatch.setattr(cli_main, "validate_port_config", lambda *a, **kw: ({}, []))

    captured = {}

    def _capturing_prepare(self, plan, cfg_mgr, sec, **opts):
        captured["tm"] = self
        captured["plan"] = plan
        captured["config_manager"] = cfg_mgr
        captured["secrets_manager"] = sec
        captured["options"] = opts
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(TemplateManager, "prepare_deployment_files", _capturing_prepare)

    CliRunner().invoke(
        cli_main.create,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(cfg),
            "-e",
            str(env_file_path),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )
    return captured


def _collect_files(root: Path, exclude_names: tuple) -> dict:
    """Return {relative_path_str: bytes} for all files under root, excluding top-level names."""
    result = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.parts[0] in exclude_names:
            continue
        result[str(rel)] = p.read_bytes()
    return result


# ---------------------------------------------------------------------------
# Task 1.1 — TemplateManager.preflight_render
# ---------------------------------------------------------------------------


def test_preflight_render_returns_none_discards_temp_dir(
    archi_home, env_file, monkeypatch, tmp_path
):
    """preflight_render returns None, does not touch plan.base_dir, and cleans up its temp dir."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing {EXAMPLE_CONFIG}")

    captured = _capture_tm_args(monkeypatch, env_file)
    assert "tm" in captured, "failed to capture TemplateManager args"

    tm = captured["tm"]
    plan = captured["plan"]
    config_manager = captured["config_manager"]
    secrets_manager = captured["secrets_manager"]
    options = captured["options"]

    # Record temp dirs that preflight_render creates so we can verify they are gone.
    recorded_dirs: List[str] = []
    _orig_cls = tempfile.TemporaryDirectory

    class _RecordingTmpDir:
        def __init__(self, *args, **kwargs):
            self._real = _orig_cls(*args, **kwargs)
            recorded_dirs.append(self._real.name)

        def __enter__(self):
            return self._real.__enter__()

        def __exit__(self, *args):
            return self._real.__exit__(*args)

        @property
        def name(self):
            return self._real.name

    monkeypatch.setattr(tempfile, "TemporaryDirectory", _RecordingTmpDir)

    base_dir_existed = plan.base_dir.exists()
    base_items_before = (
        sorted(str(p) for p in plan.base_dir.iterdir()) if base_dir_existed else None
    )

    result = tm.preflight_render(plan, config_manager, secrets_manager, **options)

    assert result is None
    assert plan.base_dir.exists() == base_dir_existed
    if base_items_before is not None:
        assert sorted(str(p) for p in plan.base_dir.iterdir()) == base_items_before
    assert recorded_dirs, "preflight_render did not create a TemporaryDirectory"
    for d in recorded_dirs:
        assert not Path(d).exists(), f"temp dir {d} still exists after preflight_render"


def test_preflight_render_runs_full_stage_list_skips_source_copy(
    archi_home, env_file, monkeypatch
):
    """preflight_render runs every stage in _build_workflow in order; copy_source_code is not called."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing {EXAMPLE_CONFIG}")

    captured = _capture_tm_args(monkeypatch, env_file)
    assert "tm" in captured

    tm = captured["tm"]
    plan = captured["plan"]
    config_manager = captured["config_manager"]
    secrets_manager = captured["secrets_manager"]
    options = captured["options"]

    from src.cli.managers.templates_manager import TemplateContext, TemplateManager

    # Compute expected stage list before patching _build_workflow.
    context_for_expected = TemplateContext(
        plan=plan,
        config_manager=config_manager,
        secrets_manager=secrets_manager,
        options=dict(options),
    )
    expected_stage_names = [
        s.__name__ for s in tm._build_workflow(context_for_expected)
    ]

    recorded_stages: List[str] = []
    copy_source_called: List[bool] = []

    _orig_build_workflow = TemplateManager._build_workflow

    def _recording_build_workflow(self, context):
        real_stages = _orig_build_workflow(self, context)

        def _wrap(stage):
            def _recorder(ctx):
                recorded_stages.append(stage.__name__)
                return stage(ctx)

            _recorder.__name__ = stage.__name__
            return _recorder

        return [_wrap(s) for s in real_stages]

    monkeypatch.setattr(TemplateManager, "_build_workflow", _recording_build_workflow)
    monkeypatch.setattr(
        TemplateManager,
        "copy_source_code",
        lambda self, d: copy_source_called.append(True),
    )

    # Call with build=True in options — preflight must override it to False.
    tm.preflight_render(
        plan, config_manager, secrets_manager, **{**options, "build": True}
    )

    assert recorded_stages == expected_stage_names
    assert (
        not copy_source_called
    ), "copy_source_code was called despite build being overridden to False in preflight"


def test_preflight_render_skips_probe_invalid_port_still_raises(
    archi_home, env_file, monkeypatch, tmp_path
):
    """_probe_port is never called; validate_port_config still raises for an invalid port."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing {EXAMPLE_CONFIG}")

    data = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    data["services"]["chat_app"]["port"] = "notaport"
    data["services"]["chat_app"]["external_port"] = "notaport"
    bad_config = tmp_path / "config-bad-port.yaml"
    bad_config.write_text(yaml.safe_dump(data))

    # Bypass cli_main's own port check so the run reaches TemplateManager with the bad config.
    captured = _capture_tm_args(
        monkeypatch, env_file, config_path=bad_config, bypass_port_check=True
    )
    assert "tm" in captured

    tm = captured["tm"]
    plan = captured["plan"]
    config_manager = captured["config_manager"]
    secrets_manager = captured["secrets_manager"]
    options = captured["options"]

    from src.cli.managers.templates_manager import TemplateManager

    probe_calls: List[int] = []
    monkeypatch.setattr(
        TemplateManager,
        "_probe_port",
        lambda self, port: probe_calls.append(port) or None,
    )

    with pytest.raises(ValueError, match="Invalid port value"):
        tm.preflight_render(plan, config_manager, secrets_manager, **options)

    assert probe_calls == [], f"_probe_port was called with ports {probe_calls}"


def test_preflight_render_propagates_stage_exception_unchanged(
    archi_home, env_file, monkeypatch
):
    """A stage exception propagates as the same object; the temp dir is removed on failure."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing {EXAMPLE_CONFIG}")

    captured = _capture_tm_args(monkeypatch, env_file)
    assert "tm" in captured

    tm = captured["tm"]
    plan = captured["plan"]
    config_manager = captured["config_manager"]
    secrets_manager = captured["secrets_manager"]
    options = captured["options"]

    from src.cli.managers.templates_manager import TemplateManager

    sentinel_exc = ValueError("late-render-sentinel")

    def _raising_stage(ctx):
        raise sentinel_exc

    monkeypatch.setattr(
        TemplateManager, "_build_workflow", lambda self, ctx: [_raising_stage]
    )

    recorded_dirs: List[str] = []
    _orig_cls = tempfile.TemporaryDirectory

    class _RecordingTmpDir:
        def __init__(self, *args, **kwargs):
            self._real = _orig_cls(*args, **kwargs)
            recorded_dirs.append(self._real.name)

        def __enter__(self):
            return self._real.__enter__()

        def __exit__(self, *args):
            return self._real.__exit__(*args)

        @property
        def name(self):
            return self._real.name

    monkeypatch.setattr(tempfile, "TemporaryDirectory", _RecordingTmpDir)

    with pytest.raises(ValueError) as exc_info:
        tm.preflight_render(plan, config_manager, secrets_manager, **options)

    assert exc_info.value is sentinel_exc, "stage exception was wrapped or replaced"
    for d in recorded_dirs:
        assert not Path(d).exists(), f"temp dir {d} still exists after preflight raised"


def test_preflight_render_does_not_mutate_options(archi_home, env_file, monkeypatch):
    """preflight_render does not modify the caller's options dict."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing {EXAMPLE_CONFIG}")

    captured = _capture_tm_args(monkeypatch, env_file)
    assert "tm" in captured

    tm = captured["tm"]
    plan = captured["plan"]
    config_manager = captured["config_manager"]
    secrets_manager = captured["secrets_manager"]
    options = captured["options"]

    options_copy = dict(options)
    tm.preflight_render(plan, config_manager, secrets_manager, **options)

    assert (
        options == options_copy
    ), f"options dict was mutated by preflight_render: {options!r} != {options_copy!r}"


def test_preflight_render_idempotent(archi_home, env_file, monkeypatch, tmp_path):
    """Rendering after a preflight produces identical files to rendering without one."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing {EXAMPLE_CONFIG}")

    from src.cli.managers.templates_manager import TemplateManager

    # Save original before _capture_tm_args patches it.
    original_prepare = TemplateManager.prepare_deployment_files

    captured = _capture_tm_args(monkeypatch, env_file)
    assert "tm" in captured

    tm = captured["tm"]
    plan = captured["plan"]
    config_manager = captured["config_manager"]
    secrets_manager = captured["secrets_manager"]
    options = captured["options"]

    # Skip source copy so archi_code/ is absent from both dirs (excluded from comparison anyway).
    monkeypatch.setattr(TemplateManager, "copy_source_code", lambda self, d: None)

    dir_a = tmp_path / "render_a"
    dir_b = tmp_path / "render_b"
    dir_a.mkdir()
    dir_b.mkdir()

    # Render A: no prior preflight.
    plan.base_dir = dir_a
    original_prepare(tm, plan, config_manager, secrets_manager, **options)

    # Run the preflight on the same objects.
    tm.preflight_render(plan, config_manager, secrets_manager, **options)

    # Render B: after the preflight, using same config_manager and secrets_manager.
    plan.base_dir = dir_b
    original_prepare(tm, plan, config_manager, secrets_manager, **options)

    exclude = ("archi_code", "SOURCE_COMMIT")
    files_a = _collect_files(dir_a, exclude)
    files_b = _collect_files(dir_b, exclude)

    assert files_a, "render A produced no files; check config or stage errors"
    assert files_a == files_b, (
        f"preflight_render mutated shared state: "
        f"only-in-A={sorted(set(files_a)-set(files_b))}, "
        f"only-in-B={sorted(set(files_b)-set(files_a))}"
    )
