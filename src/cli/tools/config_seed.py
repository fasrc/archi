"""
One-shot config seeder for compose deployments.

Expects env:
- PGHOST, PGPORT, PGDATABASE, PGUSER, PG_PASSWORD
- CONFIG_PATH: path to rendered config.yaml inside container

Actions:
1) Ensure schema/columns via ConfigService (it will apply DDL best-effort).
2) Upsert static_config from YAML.
3) Initialize dynamic_config only if empty.
4) Record this deploy's config provenance (ARCHI_CONFIG_* from ensure_config).
Exits 0 on success, non-zero on failure.
"""

import glob
import os
import sys

import yaml

from src.utils.benchmark_provenance import DIVERGENCE_IGNORED_PATHS
from src.utils.config_service import ConfigService
from src.utils.deployment_record import record_deployment
from src.utils.postgres_service_factory import PostgresServiceFactory


def load_config(path: str):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def fallback_candidates(config_path: str) -> list:
    """Return sorted ``*.yaml`` candidates when ``config_path`` is absent, else ``[]``.

    Returns an empty list when ``config_path`` is an existing file (single-config
    deployment). Otherwise returns the sorted ``*.yaml`` files in the same directory,
    which are the arm files a multi-config deployment rendered instead of
    ``config.yaml``.
    """
    if os.path.isfile(config_path):
        return []
    directory = (
        config_path
        if os.path.isdir(config_path)
        else (os.path.dirname(config_path) or ".")
    )
    return sorted(glob.glob(os.path.join(directory, "*.yaml")))


def resolve_config_path(config_path: str) -> str:
    """Resolve the config file to seed Postgres from.

    A single-config deployment renders ``config.yaml`` and this returns it
    unchanged. A multi-config benchmarking deployment renders per-variant files
    (e.g. ``fasrc-cannon-v1-strict.yaml``) instead, so the hardcoded
    ``config.yaml`` is absent — fall back to the first ``*.yaml`` in the
    rendered-config directory rather than aborting the whole deployment.
    ``seed_entry`` refuses the deployment when the arm files disagree outside the
    ignored paths, because the agent reads the seeded configuration for everything
    else. If nothing is found, return the original path so ``load_config`` raises
    a clear error.
    """
    candidates = fallback_candidates(config_path)
    if candidates:
        return candidates[0]
    return config_path


def _arm_walk(left, right, prefix: str, found: list) -> None:
    if prefix in DIVERGENCE_IGNORED_PATHS:
        return
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right), key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            if (key in left) != (key in right):
                if path not in DIVERGENCE_IGNORED_PATHS:
                    found.append(path)
                continue
            _arm_walk(left[key], right[key], path, found)
        return
    # ``0 == False`` in Python; a numeric setting is not a boolean one.
    if type(left) is not type(right) or left != right:
        found.append(prefix or "<root>")


def arm_config_divergence(paths: list) -> dict:
    """Paths at which each arm file disagrees with the first, outside ignored paths.

    Returns a dict mapping each differing file path to its sorted list of dotted
    config paths. Files that agree with the first are absent from the dict. Fewer
    than two paths, or an empty list, returns ``{}``.

    Both sides are files, so key presence is a setting: an absent key, ``null``
    and an empty container all differ. ``GitScraper`` reads ``code_suffixes``
    with a default, so absent means its built-in list and ``[]`` means none.
    """
    if len(paths) < 2:
        return {}
    configs = [load_config(p) for p in paths]
    first = configs[0]
    result = {}
    for path, cfg in zip(paths[1:], configs[1:]):
        found = []
        _arm_walk(first, cfg, "", found)
        if found:
            result[path] = sorted(found)
    return result


def format_arm_divergence(reference: str, divergence: dict) -> str:
    """The refusal message for arm files that disagree with ``reference``."""
    lines = [f"ARM CONFIG DIVERGENCE detected (reference: {reference}):"]
    lines += [f"  {path}: {', '.join(diffs)}" for path, diffs in divergence.items()]
    lines.append(
        "The agent reads the seeded services block, so arms must agree outside"
        " services.benchmarking, the deploy-rewritten paths and name;"
        " run separate deployments to A/B such a setting."
    )
    return "\n".join(lines)


def _is_config_file(path: str) -> bool:
    try:
        return isinstance(load_config(path), dict)
    except (OSError, yaml.YAMLError):
        return False


def arm_divergence_refusal(paths: list):
    """The refusal message when the arm files in ``paths`` disagree, else ``None``.

    ``archi evaluate`` calls this on the operator's own files, before
    ``ConfigurationManager`` drops a divergent arm and before ``--force`` tears
    down the existing runtime. Files that are not YAML mappings are skipped; the
    manager reports those.
    """
    arms = [p for p in paths if _is_config_file(p)]
    divergence = arm_config_divergence(arms)
    if not divergence:
        return None
    return format_arm_divergence(arms[0], divergence)


def seed(config: dict, cs: ConfigService):
    print("[config-seed] Starting seed with config keys:", list(config.keys()))
    dm = config.get("data_manager", {})
    services = config.get("services", {})
    archi_cfg = config.get("archi", {}) or {}
    mcp_servers = config.get("mcp_servers", {}) or {}
    archi_cfg = {**archi_cfg}
    global_cfg = config.get("global", {})

    # Embedding dimensions fallback TODO why is this here?
    embedding_name = dm.get("embedding_name", "HuggingFaceEmbeddings")
    embedding_class_map = dm.get("embedding_class_map", {})
    embedding_dimensions = embedding_class_map.get(embedding_name, {}).get(
        "dimensions", 384
    )

    chat_app_cfg = services.get("chat_app", {})
    agent_class = chat_app_cfg.get("agent_class")
    # Rendered chat configs use default_provider/default_model; fall back to the
    # legacy provider/model keys for older hand-written configs.
    provider = chat_app_cfg.get("default_provider") or chat_app_cfg.get("provider")
    model = chat_app_cfg.get("default_model") or chat_app_cfg.get("model")
    available_pipelines = [agent_class] if agent_class else []
    available_models = [f"{provider}/{model}"] if provider and model else []
    available_providers = [provider] if provider else []

    cs.initialize_static_config(
        deployment_name=config.get("name", "default"),
        data_path=global_cfg.get("DATA_PATH", "/root/data/"),
        embedding_model=embedding_name,
        embedding_dimensions=embedding_dimensions,
        chunk_size=dm.get("chunk_size", 1000),
        chunk_overlap=dm.get("chunk_overlap", 150),
        distance_metric=dm.get("distance_metric", "cosine"),
        available_pipelines=available_pipelines,
        available_models=available_models,
        available_providers=available_providers,
        auth_enabled=services.get("chat_app", {}).get("auth", {}).get("enabled", False),
        sources_config=dm.get("sources", {}),
        services_config=services,
        mcp_servers_config=mcp_servers,
        data_manager_config=dm,
        archi_config=archi_cfg,
        global_config=global_cfg,
    )

    print("[config-seed] static_config upserted")

    # Initialize dynamic config only if empty
    dynamic = cs.get_dynamic_config()
    if dynamic.updated_by is None:
        retrievers = dm.get("retrievers", {})
        hybrid = retrievers.get("hybrid_retriever", {})
        active_model = f"{provider}/{model}" if provider and model else None
        cs.update_dynamic_config(
            active_pipeline=services.get("chat_app", {}).get(
                "agent_class", "CMSCompOpsAgent"
            ),
            active_model=active_model,
            num_documents_to_retrieve=hybrid.get("num_documents_to_retrieve", 10),
            bm25_weight=hybrid.get("bm25_weight", 0.3),
            semantic_weight=hybrid.get("semantic_weight", 0.7),
            updated_by="seed",
        )
        print("[config-seed] dynamic_config initialized")


def main():
    config_path = os.environ.get("CONFIG_PATH", "/rendered-config/config.yaml")
    seed_entry(config_path, os.environ)


def seed_entry(config_path: str, env: dict):
    candidates = fallback_candidates(config_path)
    if len(candidates) >= 2:
        divergence = arm_config_divergence(candidates)
        if divergence:
            print(
                "[config-seed] " + format_arm_divergence(candidates[0], divergence),
                file=sys.stderr,
            )
            sys.exit(1)
    config_path = resolve_config_path(config_path)
    print(f"[config-seed] Loading config from {config_path}")
    config = load_config(config_path)
    factory = PostgresServiceFactory.from_env(
        password_override=env.get("PGPASSWORD") or env.get("PG_PASSWORD")
    )
    PostgresServiceFactory.set_instance(factory)
    cs = factory.config_service
    seed(config, cs)
    record_deployment(cs, env)
    print("Config seeding completed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Config seeding failed: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
