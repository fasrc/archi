"""Tests for TemplateManager.preflight_render (issue #294)."""

import tempfile
from pathlib import Path
from typing import List

import pytest
import yaml
from click.testing import CliRunner

from tests.unit.test_cli_create_dev_smoke import (
    EXAMPLE_CONFIG,
    REPO_ROOT,
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
    VolumeManager.create_required_volumes→no-op, TemplateManager._probe_port→None,
    TemplateManager.preflight_render→capturing no-op (D5: preflight runs before
    prepare_deployment_files; tests that need the real preflight must save and
    restore TemplateManager.preflight_render themselves).
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

    def _capturing_preflight(self, plan, cfg_mgr, sec, **opts):
        # Capture args here so configs that fail the real preflight (e.g. bad
        # port values) can still populate `captured` for subsequent direct calls.
        captured["tm"] = self
        captured["plan"] = plan
        captured["config_manager"] = cfg_mgr
        captured["secrets_manager"] = sec
        captured["options"] = opts

    monkeypatch.setattr(TemplateManager, "preflight_render", _capturing_preflight)

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

    from src.cli.managers.templates_manager import TemplateManager

    _real_preflight = (
        TemplateManager.preflight_render
    )  # save before _capture_tm_args patches it
    captured = _capture_tm_args(monkeypatch, env_file)
    assert "tm" in captured, "failed to capture TemplateManager args"
    monkeypatch.setattr(TemplateManager, "preflight_render", _real_preflight)

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

    from src.cli.managers.templates_manager import TemplateContext, TemplateManager

    _real_preflight = (
        TemplateManager.preflight_render
    )  # save before _capture_tm_args patches it
    captured = _capture_tm_args(monkeypatch, env_file)
    assert "tm" in captured
    monkeypatch.setattr(TemplateManager, "preflight_render", _real_preflight)

    tm = captured["tm"]
    plan = captured["plan"]
    config_manager = captured["config_manager"]
    secrets_manager = captured["secrets_manager"]
    options = captured["options"]

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

    from src.cli.managers.templates_manager import TemplateManager

    # Bypass cli_main's own port check so the run reaches TemplateManager with the bad config.
    # Save original before _capture_tm_args patches preflight_render to a no-op.
    _real_preflight = TemplateManager.preflight_render
    captured = _capture_tm_args(
        monkeypatch, env_file, config_path=bad_config, bypass_port_check=True
    )
    assert "tm" in captured
    monkeypatch.setattr(TemplateManager, "preflight_render", _real_preflight)

    tm = captured["tm"]
    plan = captured["plan"]
    config_manager = captured["config_manager"]
    secrets_manager = captured["secrets_manager"]
    options = captured["options"]

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

    from src.cli.managers.templates_manager import TemplateManager

    _real_preflight = (
        TemplateManager.preflight_render
    )  # save before _capture_tm_args patches it
    captured = _capture_tm_args(monkeypatch, env_file)
    assert "tm" in captured
    monkeypatch.setattr(TemplateManager, "preflight_render", _real_preflight)

    tm = captured["tm"]
    plan = captured["plan"]
    config_manager = captured["config_manager"]
    secrets_manager = captured["secrets_manager"]
    options = captured["options"]

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

    from src.cli.managers.templates_manager import TemplateManager

    _real_preflight = (
        TemplateManager.preflight_render
    )  # save before _capture_tm_args patches it
    captured = _capture_tm_args(monkeypatch, env_file)
    assert "tm" in captured
    monkeypatch.setattr(TemplateManager, "preflight_render", _real_preflight)

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

    # Save originals before _capture_tm_args patches them.
    original_prepare = TemplateManager.prepare_deployment_files
    _real_preflight = TemplateManager.preflight_render

    captured = _capture_tm_args(monkeypatch, env_file)
    assert "tm" in captured
    monkeypatch.setattr(TemplateManager, "preflight_render", _real_preflight)

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


# ---------------------------------------------------------------------------
# Task 2.1 — create() preflight before teardown
# ---------------------------------------------------------------------------


def test_create_force_preflight_failure_keeps_existing_deployment(
    env_file, archi_home, monkeypatch
):
    """create --force must not tear down the existing deployment when the preflight fails."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing {EXAMPLE_CONFIG}")

    from src.cli import cli_main
    from src.cli.managers.deployment_manager import DeploymentManager
    from src.cli.managers.templates_manager import TemplateManager

    _satisfied_base_images(monkeypatch)
    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(TemplateManager, "_probe_port", lambda self, port: None)

    sentinel_exc = ValueError("late-render-sentinel")

    def _raising_stage(ctx):
        raise sentinel_exc

    monkeypatch.setattr(
        TemplateManager, "_build_workflow", lambda self, ctx: [_raising_stage]
    )

    from click.testing import CliRunner

    result = CliRunner().invoke(
        cli_main.create,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert (
        result.exit_code != 0
    ), f"expected non-zero exit when preflight fails. output:\n{result.output}"
    assert (
        "late-render-sentinel" in result.output
    ), f"expected sentinel in output. output:\n{result.output}"
    assert teardowns == [], (
        f"existing deployment was torn down before the preflight ran. "
        f"teardowns={teardowns}\noutput:\n{result.output}"
    )
    assert (existing / "marker.txt").exists(), (
        f"existing deployment directory was removed despite preflight failure. "
        f"output:\n{result.output}"
    )


def test_create_dry_force_preflight_failure_keeps_existing_deployment(
    env_file, archi_home, monkeypatch
):
    """create --dry --force must not call remove_existing_deployment when the preflight fails."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing {EXAMPLE_CONFIG}")

    from src.cli import cli_main
    from src.cli.managers.templates_manager import TemplateManager
    from src.cli.managers.volume_manager import VolumeManager

    _satisfied_base_images(monkeypatch)
    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(TemplateManager, "_probe_port", lambda self, port: None)

    sentinel_exc = ValueError("late-render-sentinel")

    def _raising_stage(ctx):
        raise sentinel_exc

    monkeypatch.setattr(
        TemplateManager, "_build_workflow", lambda self, ctx: [_raising_stage]
    )

    remove_calls = []
    monkeypatch.setattr(
        cli_main,
        "remove_existing_deployment",
        lambda *a, **kw: remove_calls.append(a),
    )

    volume_calls = []
    monkeypatch.setattr(
        VolumeManager,
        "create_required_volumes",
        lambda self, *a, **kw: volume_calls.append(True),
    )

    from click.testing import CliRunner

    result = CliRunner().invoke(
        cli_main.create,
        [
            "--dry",
            "--force",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert result.exit_code != 0, (
        f"expected non-zero exit when preflight fails. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}"
    )
    assert (
        "late-render-sentinel" in result.output
    ), f"expected sentinel in output. output:\n{result.output}"
    assert (
        teardowns == []
    ), f"delete_deployment was called under --dry. teardowns={teardowns}\noutput:\n{result.output}"
    assert (
        existing / "marker.txt"
    ).exists(), f"existing deployment directory was removed. output:\n{result.output}"
    assert remove_calls == [], (
        f"remove_existing_deployment was called before the preflight failed. "
        f"output:\n{result.output}"
    )
    assert (
        volume_calls == []
    ), f"create_required_volumes was called under --dry. output:\n{result.output}"


def test_create_force_records_preflight_volumes_teardown_render_order(
    env_file, archi_home, monkeypatch
):
    """create --force must run preflight, then volumes, then teardown, then render."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing {EXAMPLE_CONFIG}")

    from src.cli import cli_main
    from src.cli.managers.deployment_manager import DeploymentManager
    from src.cli.managers.templates_manager import TemplateManager
    from src.cli.managers.volume_manager import VolumeManager

    _satisfied_base_images(monkeypatch)
    _existing_deployment(archi_home)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(TemplateManager, "_probe_port", lambda self, port: None)

    events: List[str] = []

    _orig_preflight = TemplateManager.preflight_render

    def _recording_preflight(self, plan, cfg_mgr, sec, **opts):
        events.append("preflight")
        return _orig_preflight(self, plan, cfg_mgr, sec, **opts)

    monkeypatch.setattr(TemplateManager, "preflight_render", _recording_preflight)

    monkeypatch.setattr(
        VolumeManager,
        "create_required_volumes",
        lambda self, *a, **kw: events.append("volumes"),
    )

    monkeypatch.setattr(
        DeploymentManager,
        "delete_deployment",
        lambda self, **kwargs: events.append("teardown"),
    )

    def _recording_render(self, *a, **kw):
        events.append("render")
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(TemplateManager, "prepare_deployment_files", _recording_render)

    from click.testing import CliRunner

    result = CliRunner().invoke(
        cli_main.create,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert events == ["preflight", "volumes", "teardown", "render"], (
        f"expected ['preflight', 'volumes', 'teardown', 'render'], got {events}\n"
        f"output:\n{result.output}"
    )


def test_create_dry_runs_preflight_no_volume_no_dir(env_file, archi_home, monkeypatch):
    """create --dry must run the preflight but create no volumes and no deployment directory."""
    if not EXAMPLE_CONFIG.exists():
        pytest.skip(f"missing {EXAMPLE_CONFIG}")

    from src.cli import cli_main
    from src.cli.managers.templates_manager import TemplateManager
    from src.cli.managers.volume_manager import VolumeManager

    _satisfied_base_images(monkeypatch)
    monkeypatch.setattr(TemplateManager, "_probe_port", lambda self, port: None)

    preflight_calls: List[bool] = []
    _orig_preflight = TemplateManager.preflight_render

    def _recording_preflight(self, plan, cfg_mgr, sec, **opts):
        preflight_calls.append(True)
        return _orig_preflight(self, plan, cfg_mgr, sec, **opts)

    monkeypatch.setattr(TemplateManager, "preflight_render", _recording_preflight)

    volume_calls: List[bool] = []
    monkeypatch.setattr(
        VolumeManager,
        "create_required_volumes",
        lambda self, *a, **kw: volume_calls.append(True),
    )

    from click.testing import CliRunner

    result = CliRunner().invoke(
        cli_main.create,
        [
            "--dry",
            "-n",
            "smoke",
            "-c",
            str(EXAMPLE_CONFIG),
            "-e",
            str(env_file),
            "--services",
            "chatbot",
            "--hostmode",
        ],
    )

    assert (
        result.exit_code == 0
    ), f"expected exit 0 for --dry. exit_code={result.exit_code}\noutput:\n{result.output}"
    assert preflight_calls == [
        True
    ], f"expected preflight called once, got {preflight_calls}. output:\n{result.output}"
    assert (
        volume_calls == []
    ), f"create_required_volumes must not be called under --dry. output:\n{result.output}"
    assert not (
        archi_home / "archi-smoke"
    ).exists(), (
        f"archi-smoke directory must not be created by --dry. output:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Task 3.1 — evaluate() preflight before teardown
# ---------------------------------------------------------------------------


def test_evaluate_force_preflight_failure_keeps_existing_runtime(
    env_file, archi_home, benchmark_config, monkeypatch
):
    """evaluate --force must not tear down the runtime when the preflight fails."""
    from src.cli import cli_main
    from src.cli.managers.templates_manager import TemplateManager

    _satisfied_base_images(monkeypatch)
    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )
    monkeypatch.setattr(TemplateManager, "_probe_port", lambda self, port: None)

    sentinel_exc = ValueError("late-render-sentinel")

    def _raising_stage(ctx):
        raise sentinel_exc

    monkeypatch.setattr(
        TemplateManager, "_build_workflow", lambda self, ctx: [_raising_stage]
    )

    result = CliRunner().invoke(
        cli_main.evaluate,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(benchmark_config),
            "-e",
            str(env_file),
        ],
    )

    assert (
        result.exit_code != 0
    ), f"expected non-zero exit when preflight fails. output:\n{result.output}"
    assert (
        "late-render-sentinel" in result.output
    ), f"expected sentinel in output. output:\n{result.output}"
    assert teardowns == [], (
        f"runtime was torn down before the preflight ran. "
        f"teardowns={teardowns}\noutput:\n{result.output}"
    )
    assert (existing / "marker.txt").exists(), (
        f"existing runtime was removed despite preflight failure. "
        f"output:\n{result.output}"
    )


def test_evaluate_force_config_dir_basename_collision_keeps_existing_runtime(
    env_file, archi_home, monkeypatch, tmp_path
):
    """evaluate --force --config-dir must not tear down when agent_md basenames collide."""
    from src.cli import cli_main
    from src.cli.managers.templates_manager import TemplateManager

    _satisfied_base_images(monkeypatch)
    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )
    monkeypatch.setattr(TemplateManager, "_probe_port", lambda self, port: None)

    miscellanea = (
        REPO_ROOT / "examples" / "deployments" / "basic-openai" / "miscellanea.list"
    )

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    agent_a = dir_a / "agent.md"
    agent_b = dir_b / "agent.md"
    agent_a.write_text("# Agent A")
    agent_b.write_text("# Agent B")

    config_dir = tmp_path / "configs"
    config_dir.mkdir()

    def _config_text(name, agent_md_path):
        return f"""\
name: {name}

services:
  benchmarking:
    agent_class: CMSCompOpsAgent
    agent_md_file: {agent_md_path}
    provider: openai
    model: gpt-4o
    ollama_url: http://localhost:11434

data_manager:
  sources:
    links:
      input_lists:
        - {miscellanea}
  embedding_name: HuggingFaceEmbeddings
"""

    (config_dir / "a.yaml").write_text(_config_text("bench-a", str(agent_a)))
    (config_dir / "b.yaml").write_text(_config_text("bench-b", str(agent_b)))

    result = CliRunner().invoke(
        cli_main.evaluate,
        [
            "--force",
            "-n",
            "smoke",
            "--config-dir",
            str(config_dir),
            "-e",
            str(env_file),
        ],
    )

    assert (
        result.exit_code != 0
    ), f"expected non-zero exit on basename collision. output:\n{result.output}"
    assert (
        "same basename 'agent.md'" in result.output
    ), f"expected basename-collision message in output. output:\n{result.output}"
    assert teardowns == [], (
        f"runtime was torn down before preflight detected the collision. "
        f"teardowns={teardowns}\noutput:\n{result.output}"
    )


def test_evaluate_force_missing_agent_md_file_keeps_existing_runtime(
    env_file, archi_home, monkeypatch, tmp_path
):
    """evaluate --force must not tear down when agent_md_file does not exist.

    config_manager.py:347-348 already refuses this before the teardown on origin/dev,
    so this test is green at the start — it is kept as a regression test.
    """
    from src.cli import cli_main

    _satisfied_base_images(monkeypatch)
    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )

    miscellanea = (
        REPO_ROOT / "examples" / "deployments" / "basic-openai" / "miscellanea.list"
    )
    config_text = f"""\
name: smoke-missing-agent

services:
  benchmarking:
    agent_class: CMSCompOpsAgent
    agent_md_file: /does/not/exist/agent.md
    provider: openai
    model: gpt-4o
    ollama_url: http://localhost:11434

data_manager:
  sources:
    links:
      input_lists:
        - {miscellanea}
  embedding_name: HuggingFaceEmbeddings
"""
    config_file = tmp_path / "missing-agent.yaml"
    config_file.write_text(config_text)

    result = CliRunner().invoke(
        cli_main.evaluate,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(config_file),
            "-e",
            str(env_file),
        ],
    )

    assert (
        result.exit_code != 0
    ), f"expected non-zero exit when agent_md_file is missing. output:\n{result.output}"
    assert (
        "agent_md_file not found" in result.output
    ), f"expected 'agent_md_file not found' in output. output:\n{result.output}"
    assert teardowns == [], (
        f"runtime was torn down before agent_md_file validation. "
        f"teardowns={teardowns}\noutput:\n{result.output}"
    )


def test_evaluate_force_records_preflight_volumes_teardown_stage_render_order(
    env_file, archi_home, benchmark_config, monkeypatch
):
    """evaluate --force: preflight, volumes, teardown, local-file staging, render.

    As in create, every step that can refuse (the render preflight and volume
    creation) runs before the teardown, so a missing volume that cannot be created
    never costs the operator the running runtime.  The teardown keeps volumes
    (remove_volumes=False).  Staging writes into a volume the old runtime still
    mounts, so it waits for the teardown.
    """
    import shutil

    from src.cli import cli_main
    from src.cli.managers.deployment_manager import DeploymentManager
    from src.cli.managers.templates_manager import TemplateManager
    from src.cli.managers.volume_manager import VolumeManager

    _satisfied_base_images(monkeypatch)
    existing = _existing_deployment(archi_home)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )
    monkeypatch.setattr(TemplateManager, "_probe_port", lambda self, port: None)

    events: List[str] = []

    def _recording_preflight(self, plan, cfg_mgr, sec, **opts):
        events.append("preflight")

    monkeypatch.setattr(TemplateManager, "preflight_render", _recording_preflight)

    def _delete_recording(self, **kwargs):
        events.append("teardown")
        shutil.rmtree(existing, ignore_errors=True)

    monkeypatch.setattr(DeploymentManager, "delete_deployment", _delete_recording)

    monkeypatch.setattr(
        VolumeManager, "_create_volume", lambda self, name: events.append("volumes")
    )
    monkeypatch.setattr(
        VolumeManager,
        "stage_local_files",
        lambda self, plan, cfg: events.append("stage"),
    )

    def _recording_render(self, *a, **kw):
        events.append("render")
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(TemplateManager, "prepare_deployment_files", _recording_render)

    result = CliRunner().invoke(
        cli_main.evaluate,
        [
            "--force",
            "-n",
            "smoke",
            "-c",
            str(benchmark_config),
            "-e",
            str(env_file),
        ],
    )

    # One "volumes" entry per required volume; collapse repeats.
    order = [e for i, e in enumerate(events) if i == 0 or events[i - 1] != e]
    assert order == [
        "preflight",
        "volumes",
        "teardown",
        "stage",
        "render",
    ], f"got {events}\noutput:\n{result.output}"


# ---------------------------------------------------------------------------
# PR #628 review — evaluate refuses divergent arms before the manager drops
# one and before --force tears the runtime down
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "section, key, value",
    [
        # ConfigurationManager would log and drop this arm, then deploy one arm.
        ("services", "chat_app", {"force_initial_retrieval": False}),
        # ConfigurationManager accepts this arm; only config-seed refused it,
        # after the teardown.
        ("data_manager", "chunk_size", 500),
    ],
)
def test_evaluate_force_divergent_arms_keeps_existing_runtime(
    env_file, archi_home, benchmark_config, monkeypatch, tmp_path, section, key, value
):
    from src.cli import cli_main
    from src.cli.managers.deployment_manager import DeploymentManager
    from src.cli.managers.templates_manager import TemplateManager

    _satisfied_base_images(monkeypatch)
    existing = _existing_deployment(archi_home)
    teardowns = _record_teardowns(monkeypatch)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )
    monkeypatch.setattr(TemplateManager, "_probe_port", lambda self, port: None)
    starts = []
    monkeypatch.setattr(
        DeploymentManager, "start_deployment", lambda self, d: starts.append(d)
    )

    base = yaml.safe_load(benchmark_config.read_text())
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    arm_a = {**base, "name": "bench-a"}
    arm_b = {**base, "name": "bench-b"}
    arm_b[section] = {**base[section], key: value}
    (config_dir / "a.yaml").write_text(yaml.safe_dump(arm_a))
    (config_dir / "b.yaml").write_text(yaml.safe_dump(arm_b))

    result = CliRunner().invoke(
        cli_main.evaluate,
        [
            "--force",
            "-n",
            "smoke",
            "--config-dir",
            str(config_dir),
            "-e",
            str(env_file),
        ],
    )

    assert result.exit_code != 0, f"output:\n{result.output}"
    assert f"{section}.{key}" in result.output, f"output:\n{result.output}"
    assert teardowns == [], f"teardowns={teardowns}\noutput:\n{result.output}"
    assert starts == [], f"output:\n{result.output}"
    assert (existing / "marker.txt").exists(), f"output:\n{result.output}"
