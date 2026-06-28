"""
One-shot config seeder for compose deployments.

Expects env:
- PGHOST, PGPORT, PGDATABASE, PGUSER, PG_PASSWORD
- CONFIG_PATH: path to rendered config.yaml inside container

Actions:
1) Ensure schema/columns via ConfigService (it will apply DDL best-effort).
2) Upsert static_config from YAML.
3) Initialize dynamic_config only if empty.
Exits 0 on success, non-zero on failure.
"""

import glob
import os
import sys

import yaml

from src.utils.config_service import ConfigService
from src.utils.postgres_service_factory import PostgresServiceFactory


def load_config(path: str):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def resolve_config_path(config_path: str) -> str:
    """Resolve the config file to seed Postgres from.

    A single-config deployment renders ``config.yaml`` and this returns it
    unchanged. A multi-config benchmarking deployment renders per-variant files
    (e.g. ``fasrc-cannon-v1-strict.yaml``) instead, so the hardcoded
    ``config.yaml`` is absent — fall back to the first ``*.yaml`` in the
    rendered-config directory rather than aborting the whole deployment.
    Seeding from any one config is harmless: the benchmarker reads the YAML
    files directly and never consumes the seeded static_config. If nothing is
    found, return the original path so ``load_config`` raises a clear error.
    """
    if os.path.isfile(config_path):
        return config_path
    directory = (
        config_path
        if os.path.isdir(config_path)
        else (os.path.dirname(config_path) or ".")
    )
    candidates = sorted(glob.glob(os.path.join(directory, "*.yaml")))
    if candidates:
        return candidates[0]
    return config_path


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
    config_path = resolve_config_path(config_path)
    print(f"[config-seed] Loading config from {config_path}")
    config = load_config(config_path)
    factory = PostgresServiceFactory.from_env(
        password_override=env.get("PGPASSWORD") or env.get("PG_PASSWORD")
    )
    PostgresServiceFactory.set_instance(factory)
    cs = factory.config_service
    seed(config, cs)
    print("Config seeding completed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Config seeding failed: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
