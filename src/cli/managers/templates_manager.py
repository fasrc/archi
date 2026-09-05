import copy
import json
import os
import platform
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from jinja2 import Environment

from src.cli.managers.source_version import write_source_commit
from src.cli.service_registry import service_registry
from src.cli.utils.grafana_styling import assign_feedback_palette
from src.cli.utils.service_builder import DeploymentPlan
from src.utils.benchmark_schema import (
    anchor_container_path,
    anchor_source_path,
    anchors_enabled,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _render_config_target_name(
    single_mode: bool,
    top_level_name: str,
    benchmarking_name: Optional[str],
    index: int,
    used_names: set,
) -> str:
    """Pick the rendered-config filename for one config.

    A single-config deployment renders to ``config.yaml`` (the path config-seed
    and the chatbot expect). A multi-config run (e.g. a benchmarking sweep)
    renders one distinct file per config so the benchmarker iterates every
    variant instead of overwriting a single file. The per-variant
    ``services.benchmarking.name`` is preferred for readability, falling back to
    the top-level ``name``; collisions are disambiguated with the config index.
    """
    if single_mode:
        return "config.yaml"
    stem = str(benchmarking_name or top_level_name)
    candidate = f"{stem}.yaml"
    # Disambiguate against ALL previously-used names, not just once: keep
    # bumping the suffix until the name is unique so a config can never
    # silently overwrite an earlier rendered file.
    suffix = index
    while candidate in used_names:
        candidate = f"{stem}_{suffix}.yaml"
        suffix += 1
    used_names.add(candidate)
    return candidate


# Template file constants
BASE_CONFIG_TEMPLATE = "base-config.yaml"
BASE_COMPOSE_TEMPLATE = "base-compose.yaml"
BASE_INIT_SQL_TEMPLATE = "init.sql"  # PostgreSQL + pgvector schema
MIGRATIONS_TEMPLATE_DIR = "migrations"  # catch-up SQL files shipped with the package
# Record of the migration filenames this renderer staged, written into the
# deployment's migrations/ directory. It is what makes pruning safe: only Archi's
# own past output is ever removed, never a file an operator put there. Not a
# *.sql name, so the sidecar's glob never executes it.
MIGRATIONS_MANIFEST = ".archi-staged-migrations.json"
BASE_GRAFANA_DATASOURCES_TEMPLATE = "grafana/datasources.yaml"
BASE_GRAFANA_DASHBOARDS_TEMPLATE = "grafana/dashboards.yaml"
BASE_GRAFANA_ARCHI_DEFAULT_DASHBOARDS_TEMPLATE = "grafana/archi-default-dashboard.json"
BASE_GRAFANA_CONFIG_TEMPLATE = "grafana/grafana.ini"
EVALUATION_CONFIG_DIR = "evaluation_config"
EVALUATION_MCP_CONFIG_FILENAME = "qa_evaluation_mcp.yaml"
EVALUATION_MCP_RUNTIME_PATH = (
    f"/root/archi/{EVALUATION_CONFIG_DIR}/{EVALUATION_MCP_CONFIG_FILENAME}"
)


def collect_host_information() -> Optional[Dict[str, Optional[str]]]:
    hostname = socket.getfqdn()
    cpu_model = None
    with open("/proc/cpuinfo") as f:
        for line in f:
            if line.startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    if not cpu_model:
        fallback = platform.processor()
        cpu_model = fallback if fallback else None
    return {"hostname": hostname, "cpu_model": cpu_model}


def get_git_information() -> Dict[str, str]:

    meta_data: Dict[str, str] = {}
    wd = Path(__file__).parent

    if (
        subprocess.call(
            ["git", "branch"],
            cwd=wd,
            stderr=subprocess.STDOUT,
            stdout=open(os.devnull, "w"),
        )
        != 0
    ):
        meta_data["git_info"] = {
            "hash": "Not a git repository!",
            "diff": "Not a git repository",
        }
    else:
        meta_data["last_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=wd, encoding="UTF-8"
        )
        diff_comm = ["git", "diff"]
        meta_data["git_diff"] = subprocess.check_output(
            diff_comm, encoding="UTF-8", cwd=wd
        )
    meta_data["host"] = collect_host_information()
    return meta_data


def get_git_version() -> str:
    """Get the current git version using 'git describe --tags --always --dirty'."""

    try:
        version = (
            subprocess.check_output(
                ["git", "describe", "--tags", "--always", "--dirty"],
                stderr=subprocess.DEVNULL,
                cwd=Path(__file__).parent,
            )
            .strip()
            .decode("utf-8")
        )
        return version
    except Exception:
        return "unknown"


@dataclass
class TemplateContext:
    plan: DeploymentPlan
    config_manager: Any
    secrets_manager: Any
    options: Dict[str, Any]
    base_dir: Path = field(init=False)
    prompt_mappings: Dict[str, Dict[str, str]] = field(default_factory=dict)
    evaluation_mcp_configured: bool = False

    def __post_init__(self) -> None:
        self.base_dir = self.plan.base_dir

    def pop_option(self, key: str, default: Any = None) -> Any:
        return self.options.pop(key, default)

    def get_option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    @property
    def benchmarking(self) -> bool:
        return bool(self.options.get("benchmarking"))

    @property
    def needs_agent_specs(self) -> bool:
        """Whether any enabled service reads agent specs from data/agents.

        Not "is the chatbot enabled": piazza and redmine-mailer both call
        select_agent_spec() on the staged directory and fail when it is empty,
        so an integration-only deployment still needs agents staged. Nor "does
        anything mount data/agents": grader and mattermost mount it without
        reading specs. The registry flag records which services actually
        consume them.
        """
        definitions = service_registry.get_all_services()
        return any(
            getattr(definitions.get(name), "consumes_agent_specs", False)
            for name in self.plan.get_enabled_services()
        )

    @property
    def build(self) -> bool:
        # Whether this run will (re)build the image. Source is only copied — and the
        # SOURCE_COMMIT provenance file only refreshed — when a build actually happens,
        # so ``restart --no-build`` leaves both matching the running image.
        return bool(self.options.get("build", True))


# Sentinel meaning "the configuration did not supply a value"; a None-valued
# registry default is not a substitute (design.md D1).
_UNSET = object()


def _normalize_port(port: Any, service_name: str, config_hint: Optional[str]) -> int:
    # bool is a subclass of int, so int(True) is 1 and int(False) is 0 -- without this guard
    # `port: on` (PyYAML resolves on/yes/true to True) passes preflight as port 1, and the
    # bool itself is what port_config carries into template rendering. `off` was refused only
    # by accident, reported as an out-of-range port 0 rather than as a bad value.
    if isinstance(port, bool):
        location = f" ({config_hint})" if config_hint else ""
        raise ValueError(f"Invalid port value '{port}' for {service_name}{location}")
    try:
        port_value = int(port)
    except (TypeError, ValueError):
        location = f" ({config_hint})" if config_hint else ""
        raise ValueError(f"Invalid port value '{port}' for {service_name}{location}")
    if port_value < 1 or port_value > 65535:
        location = f" ({config_hint})" if config_hint else ""
        raise ValueError(
            f"Port out of range for {service_name}{location}: {port_value}"
        )
    return port_value


_MISSING: Any = object()


def _walk_port_config_path(base_config: Any, port_config_path: str) -> Any:
    """Walk a dotted path into base_config; return the value or _MISSING on KeyError/TypeError."""
    value: Any = base_config
    try:
        for key in port_config_path.split("."):
            value = value[key]
    except (KeyError, TypeError):
        return _MISSING
    return value


def _service_port_config_hint(
    service_def, host_mode: bool, config_value: Any = None
) -> Optional[str]:
    if not service_def.port_config_path:
        return None
    if host_mode:
        has_external = (
            isinstance(config_value, dict)
            and config_value.get("external_port") is not None
        )
        suffix = "external_port" if has_external else "port"
    else:
        suffix = "external_port"
    return f"{service_def.port_config_path}.{suffix}"


def _resolve_ports_from_config(
    config_value: Any,
    *,
    host_mode: bool,
    host_default: Optional[int],
    container_default: Optional[int],
) -> Tuple[Optional[int], Optional[int]]:
    host_port = host_default
    container_port = container_default
    if isinstance(config_value, dict):
        container_port = (
            config_value["port"] if "port" in config_value else container_port
        )
        if host_mode:
            # Mirror _apply_host_mode_port_overrides (#310): in host mode the port the
            # deployment binds is external_port when that key is present and not None,
            # so that is the value validation must check. Key presence alone is not the
            # test here -- a present-but-null external_port overrides nothing on the
            # render side, so it must override nothing here either. `port` above does
            # use key presence, so a configured falsy `port` still reaches
            # _normalize_port instead of being dropped (design.md D1).
            external = config_value.get("external_port")
            if external is not None:
                host_port = external
                container_port = external
            else:
                host_port = container_port
        else:
            host_port = (
                config_value["external_port"]
                if "external_port" in config_value
                else host_port
            )
    else:
        host_port = config_value
    return host_port, container_port


def extract_port_config(plan: DeploymentPlan, config_manager: Any) -> Dict[str, Any]:
    port_config: Dict[str, Any] = {}
    host_mode = plan.host_mode
    base_config = (config_manager.get_configs() or [{}])[0]

    for service_name, service_def in service_registry.get_all_services().items():
        key_prefix = service_name.replace("-", "_")
        configured_host: Any = _UNSET
        configured_container: Any = _UNSET

        if service_def.port_config_path:
            # _MISSING means the dotted path is absent from the config; _UNSET means the
            # path resolved but supplied no value for that side. Both fall back to the
            # registry default, and neither is a configured value.
            config_value = _walk_port_config_path(
                base_config, service_def.port_config_path
            )
            if config_value is not _MISSING:
                configured_host, configured_container = _resolve_ports_from_config(
                    config_value,
                    host_mode=host_mode,
                    host_default=_UNSET,
                    container_default=_UNSET,
                )

        host_port = (
            configured_host
            if configured_host is not _UNSET
            else service_def.default_host_port
        )
        container_port = (
            configured_container
            if configured_container is not _UNSET
            else service_def.default_container_port
        )

        # Emit when the configuration supplied a value or the registry default is
        # not None.  Postgres has no port default and no config path; it is handled
        # separately in validate_port_config, so a bare emit here would incorrectly
        # add postgres_port_host with value None and cause _normalize_port to raise.
        if configured_host is not _UNSET or service_def.default_host_port is not None:
            port_config[f"{key_prefix}_port_host"] = host_port
        if (
            configured_container is not _UNSET
            or service_def.default_container_port is not None
        ):
            port_config[f"{key_prefix}_port_container"] = container_port
        # Signal for validate_port_config: was the container port operator-configured?
        # Registry defaults are never re-checked; only operator-supplied values need
        # validation (D2 — container validity without duplicate detection).
        if configured_container is not _UNSET:
            port_config[f"{key_prefix}_port_container_configured"] = True

    return port_config


def validate_port_config(
    plan: DeploymentPlan,
    config_manager: Any,
    port_config: Dict[str, Any],
) -> Tuple[Dict[int, List[Tuple[str, Optional[str]]]], List[str]]:
    host_mode = plan.host_mode
    enabled_services = plan.get_enabled_services()
    base_config = (config_manager.get_configs() or [{}])[0]
    services_cfg = (
        base_config.get("services", {}) if isinstance(base_config, dict) else {}
    )

    port_usages: List[Tuple[int, str, Optional[str]]] = []
    for service_name in enabled_services:
        if service_name not in service_registry.get_all_services():
            continue
        key_prefix = service_name.replace("-", "_")
        host_port_key = f"{key_prefix}_port_host"
        if host_port_key not in port_config:
            continue
        host_port = port_config[host_port_key]
        service_def = service_registry.get_service(service_name)
        svc_config_value = (
            _walk_port_config_path(base_config, service_def.port_config_path)
            if service_def.port_config_path
            else _MISSING
        )
        config_hint = _service_port_config_hint(
            service_def,
            host_mode,
            config_value=svc_config_value if svc_config_value is not _MISSING else None,
        )
        port_usages.append(
            (
                _normalize_port(host_port, service_name, config_hint),
                service_name,
                config_hint,
            )
        )
        # Validate the configured container port for validity only — never for
        # duplicate detection, because container ports share namespaces (D2).
        # Only when operator-configured: registry defaults are already known-good.
        container_key = f"{key_prefix}_port_container"
        if (
            port_config.get(f"{key_prefix}_port_container_configured")
            and container_key in port_config
        ):
            container_hint = (
                f"{service_def.port_config_path}.port"
                if service_def.port_config_path
                else None
            )
            _normalize_port(port_config[container_key], service_name, container_hint)

    if host_mode and plan.get_service("postgres").enabled:
        postgres_port = services_cfg.get("postgres", {}).get("port", 5432)
        port_usages.append(
            (
                _normalize_port(postgres_port, "postgres", "services.postgres.port"),
                "postgres",
                "services.postgres.port",
            )
        )

    port_to_services: Dict[int, List[Tuple[str, Optional[str]]]] = {}
    for port, service_name, config_hint in port_usages:
        port_to_services.setdefault(port, []).append((service_name, config_hint))

    errors: List[str] = []
    for port, services in sorted(port_to_services.items()):
        if len(services) > 1:
            details = ", ".join(
                f"{service} ({hint})" if hint else service for service, hint in services
            )
            errors.append(f"Port {port} is assigned to multiple services: {details}")

    return port_to_services, errors


class TemplateManager:
    """Manages template rendering and file preparation using service registry"""

    def __init__(self, jinja_env: Environment, verbosity: int):
        self.env = jinja_env
        self.global_verbosity = verbosity
        self.registry = service_registry
        self._service_hooks: Dict[str, Callable[[TemplateContext], None]] = {
            "grafana": self._render_grafana_assets,
            "grader": self._copy_grader_assets,
        }

    def prepare_deployment_files(
        self,
        plan: DeploymentPlan,
        config_manager,
        secrets_manager,
        **options,
    ) -> None:
        context = TemplateContext(
            plan=plan,
            config_manager=config_manager,
            secrets_manager=secrets_manager,
            options=dict(options),
        )

        logger.info(
            f"Preparing deployment artifacts for `{plan.name}` in {str(context.base_dir)}"
        )
        # SOURCE_COMMIT provenance is written by ``copy_source_code`` (the source-copy
        # stage), so it is tied to the code that actually lands in the image and is
        # skipped when no build happens (``restart --no-build``).

        for stage in self._build_workflow(context):
            logger.debug(f"Starting template stage {stage.__name__}")
            stage(context)
            logger.debug(f"Completed template stage {stage.__name__}")

        logger.info(f"Finished preparing deployment artifacts for {plan.name}")

    # workflow construction
    def _build_workflow(
        self, context: TemplateContext
    ) -> List[Callable[[TemplateContext], None]]:
        stages: List[Callable[[TemplateContext], None]] = [
            self._stage_prompts,
            self._stage_agents,
            self._stage_skills,
            self._stage_evaluation_config,
            self._stage_configs,
            self._stage_service_artifacts,
            self._stage_postgres_init,
            self._stage_compose,
            self._stage_web_lists,
            self._stage_source_copy,
        ]

        if context.benchmarking:
            stages.append(self._stage_benchmarking)

        return stages

    # individual stages
    def _stage_prompts(self, context: TemplateContext) -> None:
        # Copy default prompt templates (condense/, chat/, system/ structure)
        self._copy_default_prompts(context)
        context.prompt_mappings = {}

    def _stage_agents(self, context: TemplateContext) -> None:
        config = context.config_manager.config or {}
        dst_dir = context.base_dir / "data" / "agents"
        services_cfg = config.get("services", {}) or {}

        if context.benchmarking:
            # A multi-config sweep names a distinct agent_md_file per config; stage
            # every one so the benchmarker can load each variant (not just the first).
            dst_dir.mkdir(parents=True, exist_ok=True)
            # Agent files are staged (and later referenced by the rendered config)
            # by basename, so two configs whose agent_md_file share a basename
            # would silently overwrite each other. Detect and reject that.
            staged_by_basename: Dict[str, Path] = {}
            for bench_config in context.config_manager.get_configs():
                bench_services = bench_config.get("services", {}) or {}
                benchmark_cfg = bench_services.get("benchmarking", {}) or {}
                agent_md_file = benchmark_cfg.get("agent_md_file")
                if not agent_md_file:
                    raise ValueError(
                        "Missing required services.benchmarking.agent_md_file in config."
                    )
                source_path = Path(str(agent_md_file)).expanduser()
                config_path = Path(
                    str(bench_config.get("_config_path", ""))
                ).expanduser()
                if not source_path.is_absolute() and config_path:
                    candidate = (config_path.parent / source_path).resolve()
                    if candidate.exists():
                        source_path = candidate
                if not source_path.exists() or not source_path.is_file():
                    raise ValueError(f"Benchmark agent file not found: {source_path}")
                if source_path.suffix.lower() != ".md":
                    raise ValueError(
                        f"Benchmark agent file must be a .md file: {source_path}"
                    )
                prior = staged_by_basename.get(source_path.name)
                if prior is not None and prior != source_path:
                    raise ValueError(
                        "Two benchmark configs reference different agent files with the "
                        f"same basename '{source_path.name}' ({prior} vs {source_path}); "
                        "they would overwrite each other when staged. Rename one."
                    )
                staged_by_basename[source_path.name] = source_path
                shutil.copyfile(source_path, dst_dir / source_path.name)
            return

        # Agents exist to be consumed by a service that reads specs from them --
        # the chat app, piazza, or redmine-mailer. A deployment with none --
        # the grader-only flow, examples/deployments/grading/config.yaml, whose
        # services are grader_app only -- has no services.chat_app section at
        # all, and config validation skips the chat-app checks for exactly that
        # reason. Demanding agents_dir here anyway raised after the deployment
        # directory had been created, and so under --force after the existing
        # deployment was torn down (fasrc/archi#287). Nothing this stage can
        # reject may reach that point.
        if not getattr(context, "needs_agent_specs", True):
            logger.debug("no enabled service consumes agent specs; skipping")
            return

        agents_dir = (services_cfg.get("chat_app") or {}).get("agents_dir")
        if not agents_dir:
            if dst_dir.exists() and any(
                p.suffix.lower() == ".md" for p in dst_dir.iterdir()
            ):
                return
            raise ValueError("Missing required services.chat_app.agents_dir in config.")
        src_dir = Path(agents_dir).expanduser()
        if not src_dir.exists() or not src_dir.is_dir():
            raise ValueError(f"Agents directory not found: {src_dir}")
        dst_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for agent_file in sorted(src_dir.iterdir()):
            if agent_file.is_file() and agent_file.suffix.lower() == ".md":
                shutil.copyfile(agent_file, dst_dir / agent_file.name)
                copied += 1
        if copied == 0:
            raise ValueError(f"No agent markdown files found in {src_dir}")

    def _stage_skills(self, context: TemplateContext) -> None:
        config = context.config_manager.config or {}
        services_cfg = config.get("services", {}) or {}
        skills_dir = (services_cfg.get("chat_app") or {}).get("skills_dir")
        if not skills_dir:
            logger.debug("No skills_dir configured; skipping skills copy")
            return

        src_dir = Path(skills_dir).expanduser()
        if not src_dir.exists() or not src_dir.is_dir():
            logger.warning("Skills directory not found: %s", src_dir)
            return

        dst_dir = context.base_dir / "data" / "skills"
        dst_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for skill_file in sorted(src_dir.iterdir()):
            if skill_file.is_file() and skill_file.suffix.lower() == ".md":
                shutil.copyfile(skill_file, dst_dir / skill_file.name)
                copied += 1
        if copied:
            logger.info("Copied %d skill file(s) from %s", copied, src_dir)
        else:
            logger.warning("No skill markdown files found in %s", src_dir)

    def _copy_default_prompts(self, context: TemplateContext) -> None:
        """Copy default prompt templates to deployment for PromptService."""
        # Source from examples/defaults/prompts/ (not source code)
        repo_root = Path(__file__).parent.parent.parent.parent
        defaults_prompts_dir = repo_root / "examples" / "defaults" / "prompts"
        # Deploy to data/prompts/ (admin-editable location)
        deployment_prompts_dir = context.base_dir / "data" / "prompts"

        if not defaults_prompts_dir.exists():
            logger.warning(
                f"Default prompts directory not found: {defaults_prompts_dir}"
            )
            return

        # Copy the entire prompts directory structure (condense/, chat/, system/)
        for prompt_type in ["condense", "chat", "system"]:
            src_dir = defaults_prompts_dir / prompt_type
            dst_dir = deployment_prompts_dir / prompt_type

            if src_dir.exists():
                dst_dir.mkdir(parents=True, exist_ok=True)
                for prompt_file in src_dir.glob("*.prompt"):
                    dst_file = dst_dir / prompt_file.name
                    if not dst_file.exists():  # Don't overwrite existing prompts
                        shutil.copyfile(prompt_file, dst_file)
                        logger.debug(
                            f"Copied default prompt: {prompt_type}/{prompt_file.name}"
                        )

    def _stage_evaluation_config(self, context: TemplateContext) -> None:
        """Validate and stage the evaluator-owned MCP registry.

        The deployment configuration names a host path. Runtime configuration
        always receives the fixed path where this stage mounts the validated
        snapshot into the chatbot container.
        """
        config = context.config_manager.config or {}
        services = config.get("services", {}) or {}
        chat_app = services.get("chat_app", {}) or {}
        evaluations = chat_app.get("evaluations", {}) or {}
        raw_path = evaluations.get("mcp_config_path")

        staged_path = (
            context.base_dir / EVALUATION_CONFIG_DIR / EVALUATION_MCP_CONFIG_FILENAME
        )

        if raw_path is None:
            context.evaluation_mcp_configured = False
            if staged_path.exists() or staged_path.is_symlink():
                staged_path.unlink()
            return
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(
                "services.chat_app.evaluations.mcp_config_path must be a "
                "non-empty string"
            )

        source_path = Path(raw_path).expanduser()
        if not source_path.is_absolute():
            config_path_raw = config.get("_config_path")
            if not config_path_raw:
                raise ValueError(
                    "Cannot resolve relative evaluator MCP configuration path "
                    "without the deployment configuration file path"
                )
            config_path = Path(str(config_path_raw)).expanduser()
            source_path = (config_path.parent / source_path).resolve()

        try:
            if not source_path.exists():
                raise ValueError(
                    f"Evaluator MCP configuration file not found: {source_path}"
                )
            if not source_path.is_file():
                raise ValueError(
                    f"Evaluator MCP configuration must be a file: {source_path}"
                )

            # Imported here so loading the CLI does not pull in the MCP client
            # until an evaluator registry is actually configured.
            from src.evaluation.qa.oracle_config import EvaluatorMCPRegistry

            EvaluatorMCPRegistry.load(source_path)
        except PermissionError:
            raise ValueError(
                f"Evaluator MCP configuration is not readable: {source_path}"
            ) from None

        context.evaluation_mcp_configured = True
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, staged_path)
        logger.info(
            "Staged evaluator MCP configuration from %s to %s",
            source_path,
            staged_path,
        )

    def _stage_configs(self, context: TemplateContext) -> None:
        self._render_config_files(context)

    def _stage_service_artifacts(self, context: TemplateContext) -> None:
        for name, hook in self._service_hooks.items():
            if context.plan.get_service(name).enabled:
                logger.info(f"Rendering supplemental assets for service {name}")
                hook(context)

    def _stage_postgres_init(self, context: TemplateContext) -> None:
        self._render_postgres_init(context)

    def _stage_compose(self, context: TemplateContext) -> None:
        self._render_compose_file(context)

    def _stage_web_lists(self, context: TemplateContext) -> None:
        self._copy_web_input_lists(context)

    def _stage_source_copy(self, context: TemplateContext) -> None:
        # Only copy source (and refresh SOURCE_COMMIT) when a build will consume it;
        # ``restart --no-build`` must not overwrite the running image's provenance.
        if not context.build:
            return
        self.copy_source_code(context.base_dir)

    @staticmethod
    def _benchmarking_config(context: TemplateContext) -> Tuple[Dict[str, Any], Any]:
        """``(services.benchmarking, global.DATA_PATH)`` of the FIRST config.

        The deploy stages only ``configs[0]``'s assets (see
        ``preflight_benchmark_configs``), so anchors resolve off that same config.
        """
        config_manager = getattr(context, "config_manager", None)
        if config_manager is None:
            return {}, None
        configs = config_manager.get_configs() or [{}]
        config = configs[0] if isinstance(configs[0], dict) else {}
        bench = (config.get("services") or {}).get("benchmarking") or {}
        return bench, (config.get("global") or {}).get("DATA_PATH")

    def _anchor_mount_target(self, context: TemplateContext) -> Optional[str]:
        """In-container mount point for the staged anchor bank, or ``None``.

        ``None`` whenever nothing will be staged — anchors off, no benchmarking
        service, or no host bank — because binding a missing host path would make
        Docker materialise an empty DIRECTORY at the destination, which the runtime
        would then fail to read (``IsADirectoryError``).
        """
        if not context.plan.get_service("benchmarking").enabled:
            return None
        bench, data_path = self._benchmarking_config(context)
        if not anchor_source_path(bench, data_path):
            return None
        return anchor_container_path(bench)

    def _stage_benchmarking(self, context: TemplateContext) -> None:
        query_file = context.pop_option("query_file")
        if not query_file:
            logger.warning(
                "Benchmarking requested but no query file provided; skipping copy"
            )
        else:
            query_file_dest = context.base_dir / "queries.txt"
            shutil.copyfile(query_file, query_file_dest)

        # Anchors default ON, but the image ships no `examples/` and /root/data is a
        # named volume — so the bank only reaches the runtime via this staged copy
        # plus the bind mount rendered into the compose file.
        bench, data_path = self._benchmarking_config(context)
        anchor_src = anchor_source_path(bench, data_path)
        if anchor_src:
            shutil.copyfile(anchor_src, context.base_dir / "anchors.json")
            logger.info(f"Staged anchor questions from {anchor_src}")
        elif anchors_enabled(bench):
            logger.warning(
                "Anchor questions are enabled but no anchor bank was found on the "
                "host; the benchmark will run without anchors."
            )

        git_info = get_git_information()
        git_info_path = context.base_dir / "git_info.yaml"

        import yaml

        with open(git_info_path, "w") as f:
            yaml.dump(git_info, f)

    # prompt preparation
    def _collect_prompt_mappings(
        self, context: TemplateContext
    ) -> Dict[str, Dict[str, str]]:
        return {}

    def _copy_pipeline_prompts(
        self,
        base_dir: Path,
        prompts_config: Dict[str, Any],
        *,
        config_dir: Optional[Path] = None,
    ) -> Dict[str, str]:
        prompt_mappings: Dict[str, str] = {}

        for _, section_prompts in prompts_config.items():
            if not isinstance(section_prompts, dict):
                continue

            for prompt_key, prompt_path in section_prompts.items():
                if not prompt_path or prompt_path == "null":
                    continue

                source_path = Path(prompt_path).expanduser()
                if not source_path.is_absolute() and config_dir:
                    # Prefer config-relative paths but fall back to CWD if it already exists.
                    if not source_path.exists():
                        source_path = (config_dir / source_path).resolve()
                if not source_path.exists():
                    logger.warning(f"Prompt file not found: {prompt_path}")
                    continue

                target_path = base_dir / "data" / "prompts" / source_path.name
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_path, target_path)

                prompt_mappings[prompt_key] = (
                    f"/root/archi/data/prompts/{source_path.name}"
                )
                logger.debug(f"Copied prompt {prompt_key} to {target_path}")

        return prompt_mappings

    # config rendering
    def _render_config_files(self, context: TemplateContext) -> None:
        configs_path = context.base_dir / "configs"
        configs_path.mkdir(exist_ok=True)

        archi_configs = context.config_manager.get_configs()
        single_mode = len(archi_configs) == 1
        used_names: set = set()
        for index, archi_config in enumerate(archi_configs):
            name = archi_config["name"]
            updated_config = copy.deepcopy(archi_config)

            if context.plan.host_mode:
                updated_config["host_mode"] = context.plan.host_mode
                self._apply_host_mode_port_overrides(updated_config)

            services_cfg = updated_config.get("services", {})
            for service_name in ("chat_app", "redmine_mailbox", "piazza"):
                service_cfg = services_cfg.get(service_name)
                if isinstance(service_cfg, dict):
                    service_cfg["agents_dir"] = "/root/archi/agents"
                    if service_cfg.get("skills_dir"):
                        service_cfg["skills_dir"] = "/root/archi/skills"
                    if service_name == "chat_app":
                        evaluations_cfg = service_cfg.get("evaluations")
                        if isinstance(evaluations_cfg, dict):
                            evaluations_cfg["mcp_config_path"] = (
                                EVALUATION_MCP_RUNTIME_PATH
                                if context.evaluation_mcp_configured
                                else None
                            )
            if context.benchmarking:
                benchmark_cfg = services_cfg.get("benchmarking")
                if isinstance(benchmark_cfg, dict):
                    agent_md_file = benchmark_cfg.get("agent_md_file")
                    if agent_md_file:
                        benchmark_cfg["agent_md_file"] = (
                            f"/root/archi/agents/{Path(str(agent_md_file)).name}"
                        )

            config_template = self.env.get_template(BASE_CONFIG_TEMPLATE)
            config_rendered = config_template.render(
                verbosity=context.plan.verbosity, **updated_config
            )

            benchmarking_name = None
            if context.benchmarking:
                benchmark_cfg = services_cfg.get("benchmarking")
                if isinstance(benchmark_cfg, dict):
                    benchmarking_name = benchmark_cfg.get("name")
            target_name = _render_config_target_name(
                single_mode, name, benchmarking_name, index, used_names
            )
            with open(configs_path / target_name, "w") as f:
                f.write(config_rendered)
            logger.info(f"Rendered configuration file {configs_path / target_name}")

    # service-specific assets
    def _render_grafana_assets(self, context: TemplateContext) -> None:
        base_dir = context.base_dir
        grafana_dir = base_dir / "grafana"
        grafana_dir.mkdir(exist_ok=True)

        grafana_pg_password = context.secrets_manager.get_secret("GRAFANA_PG_PASSWORD")
        postgres_port = (
            context.config_manager.config.get("services", {})
            .get("postgres", {})
            .get("port", 5432)
        )

        datasources_template = self.env.get_template(BASE_GRAFANA_DATASOURCES_TEMPLATE)
        datasources = datasources_template.render(
            grafana_pg_password=grafana_pg_password,
            host_mode=context.plan.host_mode,
            postgres_port=postgres_port,
        )
        with open(grafana_dir / "datasources.yaml", "w") as f:
            f.write(datasources)

        dashboards_template = self.env.get_template(BASE_GRAFANA_DASHBOARDS_TEMPLATE)
        dashboards = dashboards_template.render()
        with open(grafana_dir / "dashboards.yaml", "w") as f:
            f.write(dashboards)

        configs = context.config_manager.get_configs()
        palette = assign_feedback_palette(configs)

        dashboard_template = self.env.get_template(
            BASE_GRAFANA_ARCHI_DEFAULT_DASHBOARDS_TEMPLATE
        )
        dashboard = dashboard_template.render(
            feedback_palette=palette,
        )
        with open(grafana_dir / "archi-default-dashboard.json", "w") as f:
            f.write(dashboard)

        grafana_anonymous_access = (
            context.config_manager.config.get("services", {})
            .get("grafana", {})
            .get("anonymous_access", False)
        )
        config_template = self.env.get_template(BASE_GRAFANA_CONFIG_TEMPLATE)
        grafana_config = config_template.render(
            grafana_anonymous_access=grafana_anonymous_access,
        )
        with open(grafana_dir / "grafana.ini", "w") as f:
            f.write(grafana_config)

    def _copy_grader_assets(self, context: TemplateContext) -> None:
        archi_config = context.config_manager.get_configs()[0]
        grader_config = archi_config.get("services", {}).get("grader_app", {})

        users_csv_dir = grader_config.get("local_users_csv_dir")
        if users_csv_dir:
            users_csv_path = Path(users_csv_dir).expanduser() / "users.csv"
            if users_csv_path.exists():
                shutil.copyfile(users_csv_path, context.base_dir / "users.csv")

        rubric_dir = grader_config.get("local_rubric_dir")
        num_problems = grader_config.get("num_problems", 1)

        if rubric_dir:
            for problem in range(1, num_problems + 1):
                rubric_path = (
                    Path(rubric_dir).expanduser()
                    / f"solution_with_rubric_{problem}.txt"
                )
                if rubric_path.exists():
                    target_path = (
                        context.base_dir / f"solution_with_rubric_{problem}.txt"
                    )
                    shutil.copyfile(rubric_path, target_path)

    # postgres + compose rendering
    def _render_postgres_init(self, context: TemplateContext) -> None:
        grafana_enabled = context.plan.get_service("grafana").enabled
        grafana_pg_password = (
            context.secrets_manager.get_secret("GRAFANA_PG_PASSWORD")
            if grafana_enabled
            else ""
        )

        # PostgreSQL + pgvector schema
        init_sql_template = self.env.get_template(BASE_INIT_SQL_TEMPLATE)

        # Get embedding dimensions from data_manager config
        data_manager_config = context.config_manager.config.get("data_manager", {})
        embedding_class_map = data_manager_config.get("embedding_class_map", {})
        embedding_name = data_manager_config.get("embedding_name", "all-MiniLM-L6-v2")

        # Default dimensions based on common embedding models
        default_dimensions = {
            "all-MiniLM-L6-v2": 384,
            "text-embedding-ada-002": 1536,
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
        }
        embedding_dimensions = default_dimensions.get(embedding_name, 384)

        # Allow override from config
        if embedding_name in embedding_class_map:
            embedding_dimensions = embedding_class_map[embedding_name].get(
                "dimensions", embedding_dimensions
            )

        init_sql = init_sql_template.render(
            use_grafana=grafana_enabled,
            grafana_pg_password=grafana_pg_password,
            embedding_dimensions=embedding_dimensions,
            # Vector index settings (optional overrides)
            vector_index_type=data_manager_config.get("vector_index_type", "hnsw"),
            vector_index_hnsw_m=data_manager_config.get("vector_index_hnsw_m", 16),
            vector_index_hnsw_ef=data_manager_config.get("vector_index_hnsw_ef", 64),
        )
        dest = context.base_dir / "init.sql"

        with open(dest, "w") as f:
            f.write(init_sql)
        logger.debug(f"Wrote PostgreSQL init script to {dest}")

        migrations_src = (
            Path(__file__).parent.parent / "templates" / MIGRATIONS_TEMPLATE_DIR
        )
        migrations_dest = context.base_dir / MIGRATIONS_TEMPLATE_DIR
        shutil.copytree(migrations_src, migrations_dest, dirs_exist_ok=True)

        # copytree overwrites and adds; it never removes. The sidecar globs every
        # staged *.sql on every startup, so a migration deleted or renamed upstream
        # would keep executing forever against a schema its replacement has already
        # moved past — and under ON_ERROR_STOP=1 any disagreement between the two
        # fails db-migrate, which config-seed and the data manager gate on. So the
        # destination is synchronized rather than merged into.
        #
        # Synchronized to what Archi OWNS, established by provenance rather than by
        # basename. A file absent from the package is not thereby obsolete: an
        # operator's hotfix or recovery migration is absent by definition, and
        # deleting it on the next routine redeploy — before the sidecar ever ran it —
        # would destroy operational work with nothing to recover it from. The
        # manifest records what this function staged, so only its own past output is
        # ever removed.
        #
        # No manifest means no record, so nothing is removed: a deployment predating
        # the manifest carries an obsolete migration for one more run, which is the
        # price of never deleting an operator's file. A manifest that cannot be read
        # is treated the same way.
        packaged_sql = {path.name for path in migrations_src.glob("*.sql")}
        manifest_path = migrations_dest / MIGRATIONS_MANIFEST
        previously_staged = self._read_migrations_manifest(manifest_path)

        # Scoped to *.sql: that is exactly what the sidecar executes, so it is the
        # set that can misbehave.
        for staged in migrations_dest.glob("*.sql"):
            if staged.name in packaged_sql or staged.name not in previously_staged:
                continue
            staged.unlink()
            logger.debug(f"Removed migration no longer packaged: {staged.name}")

        manifest_path.write_text(json.dumps(sorted(packaged_sql), indent=2) + "\n")
        logger.debug(f"Copied migrations to {migrations_dest}")

    @staticmethod
    def _read_migrations_manifest(manifest_path: Path) -> set:
        """Names this function staged on a previous render, or an empty set.

        Every failure path yields the empty set — the conservative answer, since the
        only thing this licenses is deletion.
        """
        try:
            recorded = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            return set()
        if not isinstance(recorded, list):
            logger.debug(f"Ignoring malformed migration manifest at {manifest_path}")
            return set()
        return {name for name in recorded if isinstance(name, str)}

    def _render_compose_file(self, context: TemplateContext) -> None:
        template_vars = context.plan.to_template_vars()
        port_config = self._extract_port_config(context)
        allow_port_reuse = context.get_option("allow_port_reuse", False)
        self._check_ports_available(
            context, port_config, allow_port_reuse=allow_port_reuse
        )
        template_vars.update(port_config)
        template_vars.setdefault(
            "postgres_port",
            context.config_manager.config.get("services", {})
            .get("postgres", {})
            .get("port", 5432),
        )
        template_vars.setdefault("verbosity", self.global_verbosity)

        template_vars["app_version"] = get_git_version()

        # Compose template still expects optional lists
        template_vars.setdefault("prompt_files", [])
        template_vars.setdefault("rubrics", [])

        template_vars["benchmark_anchors_target"] = self._anchor_mount_target(context)
        template_vars["evaluation_mcp_configured"] = context.evaluation_mcp_configured

        if context.plan.get_service("grader").enabled:
            template_vars["rubrics"] = self._get_grader_rubrics(context.config_manager)

        compose_template = self.env.get_template(BASE_COMPOSE_TEMPLATE)
        compose_rendered = compose_template.render(**template_vars)

        dest = context.base_dir / "compose.yaml"
        with open(dest, "w") as f:
            f.write(compose_rendered)
        logger.info(f"Rendered compose file {dest}")

    def _extract_port_config(self, context: TemplateContext) -> Dict[str, Any]:
        return extract_port_config(context.plan, context.config_manager)

    def _check_ports_available(
        self,
        context: TemplateContext,
        port_config: Dict[str, Any],
        *,
        allow_port_reuse: bool = False,
    ) -> None:
        port_to_services, errors = validate_port_config(
            context.plan, context.config_manager, port_config
        )

        # The probe runs here — after teardown — not pre-teardown: the existing
        # deployment still holds its ports, so an early probe would report a false
        # conflict for every port the replacement reuses, refusing exactly the
        # re-creates that should succeed (spec acceptance criterion 5).
        if not allow_port_reuse:
            for port, services in sorted(port_to_services.items()):
                error = self._probe_port(port)
                if error:
                    details = ", ".join(
                        f"{service} ({hint})" if hint else service
                        for service, hint in services
                    )
                    errors.append(f"Port {port} is already in use ({details}): {error}")

        if errors:
            raise ValueError("Port check failed:\n" + "\n".join(errors))

    def _probe_port(self, port: int) -> Optional[str]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
            except OSError as exc:
                return str(exc)
        return None

    def _get_grader_rubrics(self, config_manager) -> List[str]:
        archi_config = config_manager.get_configs()[0]
        grader_config = archi_config.get("services", {}).get("grader_app", {})
        num_problems = grader_config.get("num_problems", 1)
        return [f"solution_with_rubric_{i}" for i in range(1, num_problems + 1)]

    def _apply_host_mode_port_overrides(self, config: Dict[str, Any]) -> None:
        """Normalize service ports in host mode using port/external_port only."""
        services_cfg = config.get("services", {})
        if not isinstance(services_cfg, dict):
            return

        for service_cfg in services_cfg.values():
            if not isinstance(service_cfg, dict):
                continue

            external = service_cfg.get("external_port")
            if external is not None:
                service_cfg["port"] = external

    # input list / source copying helpers
    def _copy_web_input_lists(self, context: TemplateContext) -> None:
        # Always create weblists directory (required by Dockerfiles, even if empty)
        weblists_path = context.base_dir / "weblists"
        weblists_path.mkdir(exist_ok=True)
        logger.debug(f"Created weblists directory at {weblists_path}")

        input_lists = context.config_manager.get_input_lists()
        if not input_lists:
            return

        for input_list in input_lists:
            # isfile, not exists: a directory satisfies exists() and then makes
            # shutil.copyfile raise IsADirectoryError here — inside
            # prepare_deployment_files(), so under --force after the existing
            # deployment was torn down (fasrc/archi#287). Staging already
            # tolerates a missing input list by warning and skipping, and a path
            # that is not a regular file is no more usable than an absent one.
            if os.path.isfile(input_list):
                shutil.copyfile(
                    input_list, weblists_path / os.path.basename(input_list)
                )
                logger.debug(f"Copied input list {input_list}")
            else:
                logger.warning(
                    f"Configured input list {input_list} is not a readable file; "
                    f"skipping"
                )

    def copy_source_code(self, base_dir: Path) -> None:
        # Try to locate the repository root in a robust way. Prefer CWD when
        # it contains expected marker files (pyproject.toml, LICENSE, .git)
        # — this is what the template/preview code typically uses. If CWD
        # doesn't look like the repo root, fall back to walking up from this
        # file's location. Avoid assuming a fixed number of parent hops which
        # breaks in PR-preview, installed-package, or temporary test layouts.

        try:
            import src.cli.utils._repository_info

            repo_root = Path(src.cli.utils._repository_info.REPO_PATH)
        except Exception as e:
            logger.warning(
                f"Could not import repository path information. {str(e)}",
                "Falling back to current working directory.",
            )
            repo_root = Path(__file__).resolve()

        source_files = [
            ("src", "archi_code"),
            ("pyproject.toml", "pyproject.toml"),
            ("LICENSE", "LICENSE"),
        ]

        for src, dst in source_files:
            src_path = repo_root / src
            dst_path = base_dir / dst
            logger.debug(f"Copying source from {src_path} to {dst_path}")
            if src_path.is_dir():
                if dst_path.exists():
                    shutil.rmtree(dst_path)
                shutil.copytree(src_path, dst_path)
            elif src_path.exists():
                shutil.copyfile(src_path, dst_path)
            else:
                raise FileNotFoundError(
                    f"Source path {src_path} does not exist. Something went wrong in the repo structure."
                )

        # Record the provenance of the source just copied, resolved from the same
        # repo root, so SOURCE_COMMIT reflects the code that lands in the image.
        write_source_commit(base_dir, repo_root=repo_root)
