from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from src.cli.managers.templates_manager import TemplateManager

REGISTRY_YAML = """schema_version: qa-evaluation-mcp-v1
servers:
  dbs:
    transport: streamable_http
    url: http://localhost:8013/mcp
    authentication: {mode: none}
"""


class _FakeConfigManager:
    def __init__(self, config):
        self.config = config

    def get_configs(self):
        return [self.config]


def _template_manager():
    repository = Path(__file__).resolve().parents[2]
    environment = Environment(
        loader=FileSystemLoader(str(repository / "src/cli/templates")),
        undefined=ChainableUndefined,
    )
    return TemplateManager(environment, verbosity=0)


def _context(tmp_path, config):
    return SimpleNamespace(
        base_dir=tmp_path / "deployment",
        config_manager=_FakeConfigManager(config),
        plan=SimpleNamespace(host_mode=False, verbosity=0, name="demo"),
        benchmarking=False,
    )


def _config(config_path, mcp_config_path):
    return {
        "_config_path": str(config_path),
        "name": "demo",
        "global": {"LOGGING": {}},
        "utils": {},
        "services": {
            "chat_app": {
                "evaluations": {
                    "enabled": True,
                    "mcp_config_path": mcp_config_path,
                }
            }
        },
    }


class TestEvaluationMCPConfigStaging:
    def test_stages_relative_source_and_renders_container_path(self, tmp_path):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        config_path = source_dir / "archi.yaml"
        registry_path = source_dir / "evaluator.yaml"
        registry_path.write_text(REGISTRY_YAML, encoding="utf-8")
        context = _context(tmp_path, _config(config_path, "evaluator.yaml"))
        manager = _template_manager()

        manager._stage_evaluation_config(context)
        manager._render_config_files(context)

        staged_path = context.base_dir / "evaluation_config" / "qa_evaluation_mcp.yaml"
        assert staged_path.read_text(encoding="utf-8") == REGISTRY_YAML
        assert context.evaluation_mcp_configured is True
        rendered = yaml.safe_load(
            (context.base_dir / "configs" / "config.yaml").read_text(encoding="utf-8")
        )
        assert (
            rendered["services"]["chat_app"]["evaluations"]["mcp_config_path"]
            == "/root/archi/evaluation_config/qa_evaluation_mcp.yaml"
        )

    def test_explicit_missing_source_fails_instead_of_staging_empty_registry(
        self, tmp_path
    ):
        config_path = tmp_path / "source" / "archi.yaml"
        context = _context(tmp_path, _config(config_path, "missing.yaml"))

        with pytest.raises(
            ValueError,
            match="Evaluator MCP configuration file not found",
        ):
            _template_manager()._stage_evaluation_config(context)

        assert not (context.base_dir / "evaluation_config").exists()

    def test_invalid_source_fails_before_it_is_copied(self, tmp_path):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        config_path = source_dir / "archi.yaml"
        registry_path = source_dir / "invalid.yaml"
        registry_path.write_text("servers: {}\n", encoding="utf-8")
        context = _context(tmp_path, _config(config_path, "invalid.yaml"))

        with pytest.raises(ValueError, match="missing: schema_version"):
            _template_manager()._stage_evaluation_config(context)

        assert not (context.base_dir / "evaluation_config").exists()

    def test_omitted_source_removes_managed_stale_snapshot(self, tmp_path):
        config_path = tmp_path / "source" / "archi.yaml"
        config = _config(config_path, None)
        context = _context(tmp_path, config)
        staged_dir = context.base_dir / "evaluation_config"
        staged_dir.mkdir(parents=True)
        staged_path = staged_dir / "qa_evaluation_mcp.yaml"
        staged_path.write_text(REGISTRY_YAML, encoding="utf-8")

        _template_manager()._stage_evaluation_config(context)

        assert not staged_path.exists()
        assert context.evaluation_mcp_configured is False

    def test_staging_runs_before_runtime_config_rendering(self, tmp_path):
        context = _context(
            tmp_path,
            _config(tmp_path / "source" / "archi.yaml", None),
        )

        stages = _template_manager()._build_workflow(context)
        stage_names = [stage.__name__ for stage in stages]

        assert stage_names.index("_stage_evaluation_config") < stage_names.index(
            "_stage_configs"
        )


def _agent_config(config_path, agent_config_path, mcp_config_path=None):
    return {
        "_config_path": str(config_path),
        "name": "demo",
        "global": {"LOGGING": {}},
        "utils": {},
        "services": {
            "chat_app": {
                "evaluations": {
                    "enabled": True,
                    "agent_config_path": agent_config_path,
                    "mcp_config_path": mcp_config_path,
                }
            }
        },
    }


def _disabled_config(config_path):
    return {
        "_config_path": str(config_path),
        "name": "demo",
        "global": {"LOGGING": {}},
        "utils": {},
        "services": {
            "chat_app": {
                "evaluations": {
                    "enabled": False,
                }
            }
        },
    }


def _render_compose_with_flags(
    tmp_path, *, evaluation_agent_config_staged, evaluation_mcp_configured
):
    from src.cli.utils.service_builder import ServiceBuilder

    plan = ServiceBuilder.build_compose_config(
        name="demo",
        verbosity=3,
        base_dir=tmp_path,
        enabled_services=["chatbot"],
        secrets={"PG_PASSWORD"},
        tag="dev",
    )
    template_vars = plan.to_template_vars()
    template_vars.update(
        app_version="test",
        postgres_port=5432,
        data_manager_port_host=7871,
        data_manager_port_container=7871,
        chatbot_port_host=7861,
        chatbot_port_container=7861,
        prompt_files=[],
        rubrics=[],
        evaluation_mcp_configured=evaluation_mcp_configured,
        evaluation_agent_config_staged=evaluation_agent_config_staged,
    )
    repository = Path(__file__).resolve().parents[2]
    environment = Environment(
        loader=FileSystemLoader(str(repository / "src/cli/templates")),
        undefined=ChainableUndefined,
    )
    rendered = environment.get_template("base-compose.yaml").render(**template_vars)
    return yaml.safe_load(rendered)


class TestEvaluationAgentConfigStaging:
    def test_stages_relative_source_and_rewrites_runtime_path(self, tmp_path):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        config_path = source_dir / "archi.yaml"
        agent_config_file = source_dir / "agent.yaml"
        agent_config_content = b"agent: test\n"
        agent_config_file.write_bytes(agent_config_content)
        context = _context(tmp_path, _agent_config(config_path, "agent.yaml"))
        manager = _template_manager()

        manager._stage_evaluation_config(context)
        manager._stage_agent_config(context)
        manager._render_config_files(context)

        staged = context.base_dir / "evaluation_config" / "qa_agent_config.yaml"
        assert staged.read_bytes() == agent_config_content
        assert context.evaluation_agent_config_staged is True
        rendered = yaml.safe_load(
            (context.base_dir / "configs" / "config.yaml").read_text(encoding="utf-8")
        )
        assert (
            rendered["services"]["chat_app"]["evaluations"]["agent_config_path"]
            == "/root/archi/evaluation_config/qa_agent_config.yaml"
        )

    def test_enabled_without_mcp_config_path_renders_fixed_agent_path(self, tmp_path):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        config_path = source_dir / "archi.yaml"
        agent_config_file = source_dir / "agent.yaml"
        agent_config_file.write_text("agent: test\n", encoding="utf-8")
        context = _context(
            tmp_path, _agent_config(config_path, "agent.yaml", mcp_config_path=None)
        )
        manager = _template_manager()

        manager._stage_evaluation_config(context)
        manager._stage_agent_config(context)
        manager._render_config_files(context)

        rendered = yaml.safe_load(
            (context.base_dir / "configs" / "config.yaml").read_text(encoding="utf-8")
        )
        evaluations = rendered["services"]["chat_app"]["evaluations"]
        assert evaluations["mcp_config_path"] is None
        assert (
            evaluations["agent_config_path"]
            == "/root/archi/evaluation_config/qa_agent_config.yaml"
        )

    def test_disabled_clears_flag_and_removes_stale_staged_file(self, tmp_path):
        config_path = tmp_path / "source" / "archi.yaml"
        context = _context(tmp_path, _disabled_config(config_path))
        staged_dir = context.base_dir / "evaluation_config"
        staged_dir.mkdir(parents=True)
        staged_path = staged_dir / "qa_agent_config.yaml"
        staged_path.write_text("stale\n", encoding="utf-8")

        _template_manager()._stage_agent_config(context)

        assert not staged_path.exists()
        assert context.evaluation_agent_config_staged is False

    def test_compose_includes_evaluation_config_mount_when_agent_config_staged(
        self, tmp_path
    ):
        compose = _render_compose_with_flags(
            tmp_path,
            evaluation_agent_config_staged=True,
            evaluation_mcp_configured=False,
        )
        volumes = [
            str(v)
            for v in (compose.get("services", {}).get("chatbot") or {}).get(
                "volumes", []
            )
        ]
        assert "./evaluation_config:/root/archi/evaluation_config:ro" in volumes

    def test_compose_omits_evaluation_config_mount_when_both_flags_false(
        self, tmp_path
    ):
        compose = _render_compose_with_flags(
            tmp_path,
            evaluation_agent_config_staged=False,
            evaluation_mcp_configured=False,
        )
        volumes = [
            str(v)
            for v in (compose.get("services", {}).get("chatbot") or {}).get(
                "volumes", []
            )
        ]
        assert "./evaluation_config:/root/archi/evaluation_config:ro" not in volumes

    def test_build_workflow_lists_stage_agent_config_directly_after_stage_evaluation_config(
        self, tmp_path
    ):
        context = _context(
            tmp_path,
            _config(tmp_path / "source" / "archi.yaml", None),
        )
        stages = _template_manager()._build_workflow(context)
        stage_names = [stage.__name__ for stage in stages]

        eval_config_idx = stage_names.index("_stage_evaluation_config")
        agent_config_idx = stage_names.index("_stage_agent_config")
        assert agent_config_idx == eval_config_idx + 1
