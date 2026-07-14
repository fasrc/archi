"""Anchor questions must actually reach the benchmarking container.

The runtime defaults anchors ON (``anchor_cfg.get("enabled", True) is False``) and
looks for the bank at ``DATA_PATH/<path>`` then ``<path>`` relative to the container
WORKDIR ``/root/archi``. ``DATA_PATH`` is a named volume and ``examples/`` is never
COPY-d into the image, so before this fix the file existed at neither candidate and
the harness silently logged "running without anchors" — while the *host* deploy
preflight happily validated the host copy. Host and container disagreed.

The fix mirrors the ``queries.txt`` pattern: stage the resolved host anchor bank into
``base_dir/anchors.json`` and bind-mount it onto the path the runtime already probes.
Because ``_stage_compose`` runs BEFORE ``_stage_benchmarking``, the mount decision
cannot be a side effect of staging — both must derive it from the same pure helpers,
which is what these tests pin.
"""

from __future__ import annotations

import json
import posixpath
from pathlib import Path

import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape

from src.utils.benchmark_schema import (
    CONTAINER_WORKDIR,
    DEFAULT_ANCHOR_PATH,
    anchor_container_path,
    anchor_source_path,
    anchors_enabled,
)

# --- anchors_enabled: absence means ON, only an explicit false disables ------


def test_anchors_enabled_by_default_when_no_anchors_block():
    # The jeopardy config declares no `anchors` key at all.
    assert anchors_enabled({}) is True


def test_anchors_enabled_when_block_present_without_enabled_key():
    assert anchors_enabled({"anchors": {"path": "a.json"}}) is True


def test_anchors_disabled_only_by_explicit_false():
    assert anchors_enabled({"anchors": {"enabled": False}}) is False


def test_anchors_enabled_tolerates_non_dict_config():
    assert anchors_enabled(None) is True


# --- anchor_container_path: where the runtime will look ----------------------


def test_container_path_defaults_under_workdir():
    assert anchor_container_path({}) == f"/root/archi/{DEFAULT_ANCHOR_PATH}"


def test_container_path_honours_custom_relative_path():
    bench = {"anchors": {"path": "custom/anchors.json"}}
    assert anchor_container_path(bench) == "/root/archi/custom/anchors.json"


def test_container_path_passes_absolute_path_through():
    bench = {"anchors": {"path": "/opt/anchors.json"}}
    assert anchor_container_path(bench) == "/opt/anchors.json"


def test_container_path_is_none_when_anchors_disabled():
    assert anchor_container_path({"anchors": {"enabled": False}}) is None


def test_container_path_is_none_for_non_string_path():
    # A non-string path cannot be joined onto WORKDIR; refuse rather than crash.
    assert anchor_container_path({"anchors": {"path": 123}}) is None


@pytest.mark.parametrize("falsy", ["", None])
def test_container_path_falls_back_to_default_for_empty_path(falsy):
    # An empty/absent `path` means "use the default bank", not "no anchors".
    assert (
        anchor_container_path({"anchors": {"path": falsy}})
        == f"/root/archi/{DEFAULT_ANCHOR_PATH}"
    )


# --- anchor_source_path: the host file to stage ------------------------------


def test_source_path_finds_existing_host_file(tmp_path):
    bank = tmp_path / "anchor_questions.json"
    bank.write_text("[]")
    bench = {"anchors": {"path": str(bank)}}
    assert anchor_source_path(bench, data_path=None) == str(bank)


def test_source_path_is_none_when_file_absent(tmp_path):
    bench = {"anchors": {"path": str(tmp_path / "missing.json")}}
    assert anchor_source_path(bench, data_path=None) is None


def test_source_path_is_none_when_anchors_disabled(tmp_path):
    bank = tmp_path / "anchor_questions.json"
    bank.write_text("[]")
    bench = {"anchors": {"enabled": False, "path": str(bank)}}
    assert anchor_source_path(bench, data_path=None) is None


def test_source_path_prefers_data_path_then_cwd(tmp_path):
    # Mirrors the runtime's DATA_PATH-first resolution order.
    nested = tmp_path / "data" / "examples" / "benchmarking"
    nested.mkdir(parents=True)
    (nested / "anchor_questions.json").write_text("[]")
    resolved = anchor_source_path({}, data_path=str(tmp_path / "data"))
    assert resolved == str(nested / "anchor_questions.json")


# --- compose render: the mount appears iff there is a file to mount ----------


@pytest.fixture
def render_benchmark_volumes():
    repo_root = Path(__file__).resolve().parents[2]
    env = Environment(
        loader=FileSystemLoader(str(repo_root / "src" / "cli" / "templates")),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-compose.yaml")

    def _render(**overrides):
        return template.render(
            benchmarking_enabled=True,
            benchmarking_image="im",
            benchmarking_tag="t",
            benchmarking_container_name="bm",
            benchmarking_volume_name="bmv",
            benchmarking_dest="/tmp/out",
            postgres_enabled=True,
            postgres_container_name="pg",
            postgres_port=5432,
            postgres_volume_name="pgv",
            **overrides,
        )

    return _render


def test_compose_mounts_anchor_bank_when_target_set(render_benchmark_volumes):
    rendered = render_benchmark_volumes(
        benchmark_anchors_target="/root/archi/examples/benchmarking/anchor_questions.json"
    )
    assert (
        "./anchors.json:/root/archi/examples/benchmarking/anchor_questions.json:ro"
        in rendered
    )


def test_compose_omits_anchor_mount_when_no_target(render_benchmark_volumes):
    # A bind mount of a non-existent host path would make Docker create an empty
    # DIRECTORY at the destination, which the runtime would then fail to read.
    rendered = render_benchmark_volumes(benchmark_anchors_target=None)
    assert "anchors.json" not in rendered


# --- staging: the host bank is copied into base_dir as anchors.json ----------


def test_stage_benchmarking_copies_anchor_bank(tmp_path, monkeypatch):
    from src.cli.managers.templates_manager import TemplateManager

    anchors = [{"user_input": "q?", "reference": "a", "sources": ["u"]}]
    src = tmp_path / "anchor_questions.json"
    src.write_text(json.dumps(anchors))

    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    queries = tmp_path / "bank.json"
    queries.write_text("[]")

    manager = TemplateManager(Environment(), verbosity=0)
    context = _benchmark_context(
        base_dir,
        query_file=str(queries),
        benchmarking={"anchors": {"path": str(src)}},
    )
    monkeypatch.setattr(
        "src.cli.managers.templates_manager.get_git_information", lambda: {}
    )

    manager._stage_benchmarking(context)

    staged = base_dir / "anchors.json"
    assert staged.exists(), "anchor bank was not staged into base_dir"
    assert json.loads(staged.read_text()) == anchors


def test_stage_benchmarking_skips_anchors_when_disabled(tmp_path, monkeypatch):
    from src.cli.managers.templates_manager import TemplateManager

    src = tmp_path / "anchor_questions.json"
    src.write_text("[]")
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    queries = tmp_path / "bank.json"
    queries.write_text("[]")

    manager = TemplateManager(Environment(), verbosity=0)
    context = _benchmark_context(
        base_dir,
        query_file=str(queries),
        benchmarking={"anchors": {"enabled": False, "path": str(src)}},
    )
    monkeypatch.setattr(
        "src.cli.managers.templates_manager.get_git_information", lambda: {}
    )

    manager._stage_benchmarking(context)

    assert not (base_dir / "anchors.json").exists()


def test_stage_benchmarking_warns_when_anchors_enabled_but_bank_missing(
    tmp_path, monkeypatch, caplog
):
    from src.cli.managers.templates_manager import TemplateManager

    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    queries = tmp_path / "bank.json"
    queries.write_text("[]")

    manager = TemplateManager(Environment(), verbosity=0)
    context = _benchmark_context(
        base_dir,
        query_file=str(queries),
        benchmarking={"anchors": {"path": str(tmp_path / "nope.json")}},
    )
    monkeypatch.setattr(
        "src.cli.managers.templates_manager.get_git_information", lambda: {}
    )

    with caplog.at_level("WARNING"):
        manager._stage_benchmarking(context)

    assert not (base_dir / "anchors.json").exists()
    assert "without anchors" in caplog.text


# --- _anchor_mount_target: the single source of truth for the compose mount --


def test_anchor_mount_target_when_bank_present(tmp_path):
    from src.cli.managers.templates_manager import TemplateManager

    src = tmp_path / "anchor_questions.json"
    src.write_text("[]")
    context = _benchmark_context(
        tmp_path, query_file="x", benchmarking={"anchors": {"path": str(src)}}
    )
    manager = TemplateManager(Environment(), verbosity=0)
    # An absolute configured path is mounted verbatim (no WORKDIR join).
    assert manager._anchor_mount_target(context) == str(src)


def test_anchor_mount_target_joins_relative_path_onto_workdir(tmp_path, monkeypatch):
    from src.cli.managers.templates_manager import TemplateManager

    nested = tmp_path / "examples" / "benchmarking"
    nested.mkdir(parents=True)
    (nested / "anchor_questions.json").write_text("[]")
    monkeypatch.chdir(tmp_path)  # default path resolves relative to CWD

    context = _benchmark_context(tmp_path, query_file="x", benchmarking={})
    manager = TemplateManager(Environment(), verbosity=0)
    assert manager._anchor_mount_target(context) == f"/root/archi/{DEFAULT_ANCHOR_PATH}"


def test_anchor_mount_target_none_when_bank_absent(tmp_path):
    from src.cli.managers.templates_manager import TemplateManager

    context = _benchmark_context(
        tmp_path,
        query_file="x",
        benchmarking={"anchors": {"path": str(tmp_path / "missing.json")}},
    )
    manager = TemplateManager(Environment(), verbosity=0)
    assert manager._anchor_mount_target(context) is None


def test_anchor_mount_target_none_when_benchmarking_service_disabled(tmp_path):
    from types import SimpleNamespace

    from src.cli.managers.templates_manager import TemplateContext, TemplateManager
    from src.cli.utils.service_builder import ServiceBuilder

    src = tmp_path / "anchor_questions.json"
    src.write_text("[]")
    plan = ServiceBuilder.build_compose_config(
        name="t", verbosity=0, base_dir=tmp_path, enabled_services=["chatbot"]
    )
    context = TemplateContext(
        plan=plan,
        config_manager=SimpleNamespace(
            get_configs=lambda: [
                {"services": {"benchmarking": {"anchors": {"path": str(src)}}}}
            ]
        ),
        secrets_manager=None,
        options={},
    )
    manager = TemplateManager(Environment(), verbosity=0)
    assert manager._anchor_mount_target(context) is None


def test_benchmarking_config_tolerates_missing_config_manager(tmp_path):
    from src.cli.managers.templates_manager import TemplateContext, TemplateManager
    from src.cli.utils.service_builder import ServiceBuilder

    plan = ServiceBuilder.build_compose_config(
        name="t", verbosity=0, base_dir=tmp_path, enabled_services=["benchmarking"]
    )
    context = TemplateContext(
        plan=plan, config_manager=None, secrets_manager=None, options={}
    )
    assert TemplateManager._benchmarking_config(context) == ({}, None)


def _benchmark_context(base_dir: Path, *, query_file: str, benchmarking: dict):
    """A TemplateContext whose config_manager exposes one benchmarking config."""
    from types import SimpleNamespace

    from src.cli.managers.templates_manager import TemplateContext
    from src.cli.utils.service_builder import ServiceBuilder

    config = {"services": {"benchmarking": benchmarking}, "global": {}}
    config_manager = SimpleNamespace(
        get_configs=lambda: [config],
        config=config,
    )
    plan = ServiceBuilder.build_compose_config(
        name="t",
        verbosity=0,
        base_dir=base_dir,
        enabled_services=["benchmarking"],
    )
    return TemplateContext(
        plan=plan,
        config_manager=config_manager,
        secrets_manager=None,
        options={"query_file": query_file, "benchmarking": True},
    )


# --- the default bank must resolve from the checkout, not the caller's CWD ----
#
# `archi evaluate` is a CLI and may be invoked from anywhere. The tracked default
# bank lives in the checkout under `examples/` (which is not packaged and is not
# COPYd into the benchmark image), so resolving it only against DATA_PATH and the
# process CWD means an invocation from any other directory stages nothing, and the
# run silently reverts to the pre-fix "running without anchors" behaviour that this
# whole change exists to eliminate.


def test_source_path_resolves_default_from_repo_root_outside_the_checkout(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)  # not the checkout; CWD-relative lookup must miss
    resolved = anchor_source_path({}, data_path=None)
    assert resolved is not None, "default anchor bank must resolve outside the CWD"
    assert Path(resolved).is_file()
    assert Path(resolved).name == "anchor_questions.json"


def test_source_path_still_prefers_data_path_over_repo_root(tmp_path, monkeypatch):
    # The repo-root fallback must be LAST: a DATA_PATH bank still wins.
    monkeypatch.chdir(tmp_path)
    nested = tmp_path / "data" / "examples" / "benchmarking"
    nested.mkdir(parents=True)
    (nested / "anchor_questions.json").write_text("[]")
    resolved = anchor_source_path({}, data_path=str(tmp_path / "data"))
    assert resolved == str(nested / "anchor_questions.json")


# --- base-config render: the container must probe where we actually mounted ----
#
# `_merge_anchor_questions` reads `services.benchmarking.anchors` from the RENDERED
# container config. base-config.yaml emitted every other benchmarking field but
# dropped `anchors` entirely, so the runtime always fell back to the default path
# while the host mounted the bank at the CONFIGURED path — a custom bank was staged
# and mounted somewhere the runtime never looked, and the run lost its anchors.


@pytest.fixture
def render_base_config():
    repo_root = Path(__file__).resolve().parents[2]
    env = Environment(
        loader=FileSystemLoader(str(repo_root / "src" / "cli" / "templates")),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-config.yaml")

    def _render(benchmarking):
        import yaml

        rendered = template.render(services={"benchmarking": benchmarking})
        return yaml.safe_load(rendered)["services"]["benchmarking"]

    return _render


def test_base_config_renders_custom_anchor_path(render_base_config):
    bench = render_base_config({"anchors": {"path": "config/custom_anchors.json"}})
    assert bench["anchors"]["path"] == "config/custom_anchors.json"
    assert bench["anchors"]["enabled"] is True


def test_base_config_renders_disabled_anchors(render_base_config):
    bench = render_base_config({"anchors": {"enabled": False}})
    assert bench["anchors"]["enabled"] is False


def test_base_config_omits_anchors_when_absent(render_base_config):
    # No anchors block on the host -> none in the container, and BOTH sides fall
    # back to the same default. Rendering an empty block would be noise.
    assert "anchors" not in render_base_config({})


def test_mount_target_matches_what_the_rendered_config_makes_the_runtime_probe(
    render_base_config,
):
    """The crux of the bug: host and container must agree on ONE path.

    The host mounts the staged bank at `anchor_container_path(host_config)`. The
    runtime reads `anchors.path` out of the RENDERED container config and probes it
    relative to the image WORKDIR. If those two strings differ, the bank is mounted
    somewhere nothing ever reads. Pin them together.
    """
    host_config = {"anchors": {"path": "config/custom_anchors.json"}}
    mount_target = anchor_container_path(host_config)

    rendered = render_base_config(host_config)
    # What _merge_anchor_questions does with a relative path: resolve it against the
    # process CWD, which inside the image is WORKDIR.
    runtime_probe = posixpath.join(CONTAINER_WORKDIR, rendered["anchors"]["path"])

    assert mount_target == runtime_probe == "/root/archi/config/custom_anchors.json"
