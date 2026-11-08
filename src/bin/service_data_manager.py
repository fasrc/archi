#!/bin/python
import os
import threading
from pathlib import Path
from typing import Callable, Dict, Optional

from flask import Flask, jsonify

from src.data_manager.data_manager import DataManager
from src.data_manager.scheduler import CronScheduler
from src.data_manager.vectorstore import parent_nodes
from src.data_manager.vectorstore.schema import ensure_chunks_parent_id_index
from src.interfaces.uploader_app.app import FlaskAppWrapper
from src.utils.config_access import (
    get_full_config,
    get_global_config,
    get_services_config,
)
from src.utils.config_service import ConfigService
from src.utils.env import read_secret
from src.utils.ingestion_status import build_ingestion_helpers
from src.utils.logging import get_logger, setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory
from src.utils.source_status_file import (
    load_source_status,
    seed_source_schedules,
    set_source_schedules,
    update_source_status,
)
from src.utils.telemetry import instrument_flask_app

logger = get_logger(__name__)


def build_parent_id_index_at_startup(factory) -> bool:
    """Build ``idx_chunks_parent_id`` once, in its own committed transaction (#667).

    ``init.sql`` creates the index only on a fresh volume. The data-manager start
    is the single owner of the build: no request or ingest path runs DDL. The
    statement is the plain ``CREATE INDEX IF NOT EXISTS`` (no ``CONCURRENTLY``).

    Return ``True`` when the build committed. Return ``False`` and run no DDL
    when ``document_parent_nodes`` does not exist. Return ``False`` and log a
    WARNING when the connection or the build fails: the service still starts,
    and the parent deletes scan ``document_chunks`` until a later start.
    """
    try:
        with factory.connection_pool.get_connection() as conn:
            with conn.cursor() as cursor:
                if not parent_nodes.parent_table_exists(cursor):
                    conn.rollback()
                    logger.info(
                        "document_parent_nodes does not exist; "
                        "skipped the idx_chunks_parent_id build"
                    )
                    return False
                ensure_chunks_parent_id_index(cursor)
            conn.commit()
    except Exception:
        logger.warning(
            "Could not build idx_chunks_parent_id at data-manager start; "
            "parent deletes scan document_chunks until a later start builds it",
            exc_info=True,
        )
        return False
    logger.info("Ensured idx_chunks_parent_id on document_chunks")
    return True


def main() -> None:
    setup_logging()
    # set API keys in env for downstream clients
    os.environ["ANTHROPIC_API_KEY"] = read_secret("ANTHROPIC_API_KEY")
    os.environ["OPENAI_API_KEY"] = read_secret("OPENAI_API_KEY")
    os.environ["HUGGING_FACE_HUB_TOKEN"] = read_secret("HUGGING_FACE_HUB_TOKEN")

    factory = PostgresServiceFactory.from_env(
        password_override=read_secret("PG_PASSWORD")
    )
    PostgresServiceFactory.set_instance(factory)
    build_parent_id_index_at_startup(factory)
    config = get_full_config()
    services_config = get_services_config()
    data_manager_cfg = services_config.get("data_manager", {})
    status_file = Path(get_global_config().get("DATA_PATH")) / "ingestion_status.json"

    data_manager = DataManager(run_ingestion=False, factory=factory)
    lock = threading.RLock()
    _ing = build_ingestion_helpers(data_manager.run_ingestion, lock)

    def load_status() -> Dict[str, Dict[str, str]]:
        return load_source_status(status_file)

    def set_source_status(
        source: str, *, state: str | None = None, last_run: str | None = None
    ) -> None:
        update_source_status(status_file, source, state=state, last_run=last_run)

    def run_locked(name: str, func: Callable[[], None]) -> None:
        update = data_manager.update_vectorstore
        _ing["run_source_refresh"](name, func, update, set_source_status)

    def trigger_update() -> None:
        _ing["run_upload_update"](data_manager.update_vectorstore)

    schedule_map: Dict[str, Callable[[Optional[str]], None]] = {
        "local_files": lambda last_run=None: data_manager.localfile_manager.schedule_collect_local_files(
            data_manager.persistence, last_run=last_run
        ),
        "links": lambda last_run=None: data_manager.scraper_manager.schedule_collect_links(
            data_manager.persistence, last_run=last_run
        ),
        "git": lambda last_run=None: data_manager.scraper_manager.schedule_collect_git(
            data_manager.persistence, last_run=last_run
        ),
        "sso": lambda last_run=None: data_manager.scraper_manager.schedule_collect_sso(
            data_manager.persistence, last_run=last_run
        ),
        "jira": lambda last_run=None: data_manager.ticket_manager.schedule_collect_jira(
            data_manager.persistence, last_run=last_run
        ),
        "redmine": lambda last_run=None: data_manager.ticket_manager.schedule_collect_redmine(
            data_manager.persistence, last_run=last_run
        ),
        "indico": lambda last_run=None: data_manager.scraper_manager.schedule_collect_indico(
            data_manager.persistence, last_run=last_run
        ),
        "elog": lambda last_run=None: data_manager.scraper_manager.schedule_collect_elog(
            data_manager.persistence, last_run=last_run
        ),
    }

    scheduler = CronScheduler()
    sources_cfg = config.get("data_manager", {}).get("sources", {}) or {}

    # Initialize ConfigService for database access
    config_service: Optional[ConfigService] = None
    try:
        pg_config = {
            "password": read_secret("PG_PASSWORD"),
            **services_config["postgres"],
        }
        config_service = ConfigService(pg_config)
    except Exception as e:
        logger.warning("Could not initialize ConfigService: %s", e)

    def load_schedules_from_db() -> Dict[str, str]:
        """Load schedules from database, merging with YAML defaults."""
        db_schedules: Dict[str, str] = {}
        if config_service:
            try:
                db_schedules = config_service.get_source_schedules()
            except Exception as e:
                logger.warning("Could not load schedules from database: %s", e)

        # Merge YAML and database schedules (database takes priority)
        result: Dict[str, str] = {}
        all_sources = set(sources_cfg.keys()) | set(db_schedules.keys())
        for source_name in all_sources:
            if source_name not in schedule_map:
                continue

            # Database schedule takes priority over YAML
            if source_name in db_schedules and db_schedules[source_name]:
                result[source_name] = db_schedules[source_name]
            else:
                source_cfg = sources_cfg.get(source_name) or {}
                schedule = source_cfg.get("schedule")
                if schedule:
                    result[source_name] = schedule

        return result

    def create_job_callback(source_name: str) -> Callable[[], None]:
        """Create a callback function for a scheduled job."""

        def callback():
            status = load_status()
            last_run = status.get(source_name, {}).get("last_run")
            run_locked(source_name, lambda: schedule_map[source_name](last_run))

        return callback

    # Set up dynamic schedule reloading
    scheduler.set_config_loader(load_schedules_from_db, create_job_callback)

    # Load initial schedules from database
    db_schedules = load_schedules_from_db()
    logger.info("Loaded source schedules: %s", db_schedules)

    # seed status with schedules
    seed_source_schedules(status_file, db_schedules)

    for source_name, schedule in db_schedules.items():
        if schedule:
            scheduler.add_job(
                name=source_name,
                cron=schedule,
                callback=create_job_callback(source_name),
            )

    if scheduler.jobs:
        scheduler.start()

    app = Flask(
        __name__,
        template_folder=data_manager_cfg.get("template_folder"),
        static_folder=data_manager_cfg.get("static_folder"),
    )
    instrument_flask_app(app)

    ingestion_thread = threading.Thread(
        target=_ing["run_initial_ingestion_async"],
        name="ingestion-thread",
        daemon=True,
    )
    ingestion_thread.start()

    uploader = FlaskAppWrapper(
        app, post_update_hook=trigger_update, status_file=status_file
    )

    def get_ingestion_status():
        return jsonify(_ing["get_ingestion_status"]())

    app.add_url_rule(
        "/api/ingestion/status",
        "ingestion_status",
        get_ingestion_status,
        methods=["GET"],
    )

    def reload_schedules():
        """Trigger a reload of all schedules from the database."""
        try:
            new_schedules = scheduler.reload_schedules()
            # If schedules were added after startup, ensure the scheduler loop is running.
            if scheduler.jobs:
                scheduler.start()
            # Update status file with new schedules
            set_source_schedules(status_file, new_schedules)
            return jsonify(
                {
                    "success": True,
                    "schedules": new_schedules,
                    "jobs": scheduler.get_job_status(),
                }
            )
        except Exception as e:
            logger.exception("Failed to reload schedules")
            return jsonify({"success": False, "error": str(e)}), 500

    app.add_url_rule(
        "/api/reload-schedules", "reload_schedules", reload_schedules, methods=["POST"]
    )

    def get_schedule_status():
        """Get current schedule status for all jobs."""
        status = load_status()
        jobs = scheduler.get_job_status()
        for job in jobs:
            source_name = job.get("name")
            source_status = status.get(source_name, {}) if source_name else {}
            job["last_run"] = source_status.get("last_run")
            job["state"] = source_status.get("state", "idle")
        return jsonify({"jobs": jobs})

    app.add_url_rule(
        "/api/schedules", "get_schedules", get_schedule_status, methods=["GET"]
    )

    uploader.run(
        debug=data_manager_cfg["flask_debug_mode"],
        port=data_manager_cfg["port"],
        host=data_manager_cfg["host"],
    )


if __name__ == "__main__":
    main()
