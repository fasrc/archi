"""Deploy-time provenance: which config the running deployment was built from.

``ensure_config`` (``deploy/scripts/lib.sh``) already derives the deployed config
HEAD, the pin, the match verdict and the dirty paths — and then only writes them
to the deploy log, where the application cannot see them. ``CONFIG_REF`` is a
shell variable; it never enters a container or the database.

This module closes that gap. The deploy exports those values as environment
variables, they reach the ``config-seed`` container (which already receives
``os.environ`` and already has ``APP_VERSION`` baked in), and one row per deploy
is written here.

The record is provenance, not product: every failure is swallowed and logged so
a deploy can never fail because its commentary could not be stored.
"""

import logging
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# The environment variables this module reads. ARCHI_CONFIG_* are exported by
# ensure_config; APP_VERSION is already baked into the image by Dockerfile-chat.
DEPLOYMENT_ENV_KEYS = (
    "ARCHI_CONFIG_REF",
    "ARCHI_CONFIG_SHA",
    "ARCHI_CONFIG_HEAD",
    "ARCHI_CONFIG_PIN_MATCHED",
    "ARCHI_CONFIG_DIRTY_PATHS",
    "APP_VERSION",
)

# APP_VERSION defaults to the literal "unknown" when no build arg is passed
# (see get_git_version in templates_manager). Storing it would put the word
# "unknown" in front of an operator as though it were a version.
_VERSION_PLACEHOLDERS = {"", "unknown"}

# How the deployed config relates to the pin. Kept as three distinct states so
# "we did not observe a verdict" can never be rendered as "someone edited it".
PIN_STATE_MATCHED = "matched"
PIN_STATE_LIVE_EDITED = "live_edited"
PIN_STATE_UNKNOWN = "unknown"

_INSERT_DEPLOYMENT_RECORD = """
    INSERT INTO deployment_record (
        config_ref, config_sha, config_head, pin_matched, dirty_paths, app_version
    ) VALUES (%s, %s, %s, %s, %s, %s)
"""


def _clean(env: Mapping, key: str) -> Optional[str]:
    """Read ``key``, strip it, and return ``None`` when it carries nothing."""
    raw = env.get(key) if isinstance(env, Mapping) else None
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _parse_pin_matched(env: Mapping) -> Optional[bool]:
    """Parse ``ensure_config``'s ``match=yes|no`` verdict.

    Anything else — unset, empty, unrecognised — is ``None`` rather than
    ``False``, so "we do not know" stays distinguishable from "it did not
    match". :func:`is_live_edited` then treats both as untrustworthy.
    """
    raw = _clean(env, "ARCHI_CONFIG_PIN_MATCHED")
    if raw is None:
        return None
    lowered = raw.lower()
    if lowered == "yes":
        return True
    if lowered == "no":
        return False
    return None


def build_deployment_record(env: Mapping) -> Dict[str, Any]:
    """Build the deployment record from an environment mapping. Pure."""
    app_version = _clean(env, "APP_VERSION")
    if app_version is not None and app_version.lower() in _VERSION_PLACEHOLDERS:
        app_version = None

    dirty_raw = (
        env.get("ARCHI_CONFIG_DIRTY_PATHS") if isinstance(env, Mapping) else None
    )
    dirty_paths = None if dirty_raw is None else str(dirty_raw)

    return {
        "config_ref": _clean(env, "ARCHI_CONFIG_REF"),
        "config_sha": _clean(env, "ARCHI_CONFIG_SHA"),
        "config_head": _clean(env, "ARCHI_CONFIG_HEAD"),
        "pin_matched": _parse_pin_matched(env),
        "dirty_paths": dirty_paths,
        "app_version": app_version,
    }


def pin_state(record: Mapping) -> str:
    """Classify the deployed config against the pin. Three states, not two.

    ``live_edited`` needs evidence: either the deploy ran off the pin, or the
    tree carried tracked edits. That second case is the subtle one —
    ``ensure_config`` permits it, so showing the pin alone would misrepresent
    what is running.

    ``unknown`` is deliberately distinct. A hand-run ``archi create`` records no
    verdict, and treating that silence as a live edit would accuse a clean
    deployment of an edit nothing observed. Unknown is not clean either, so it
    gets its own state rather than being folded into ``matched``.
    """
    matched = record.get("pin_matched")
    has_tracked_edits = bool((record.get("dirty_paths") or "").strip())

    if has_tracked_edits or matched is False:
        return PIN_STATE_LIVE_EDITED
    if matched is None:
        return PIN_STATE_UNKNOWN
    return PIN_STATE_MATCHED


def is_live_edited(record: Mapping) -> bool:
    """Whether there is positive evidence the deployed config is not the pin."""
    return pin_state(record) == PIN_STATE_LIVE_EDITED


def write_deployment_record(conn: Any, env: Mapping) -> bool:
    """Insert one deployment record. Returns whether the write landed.

    Never raises: a deploy must not fail because its provenance could not be
    stored (design D9).
    """
    record = build_deployment_record(env)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                _INSERT_DEPLOYMENT_RECORD,
                (
                    record["config_ref"],
                    record["config_sha"],
                    record["config_head"],
                    record["pin_matched"],
                    record["dirty_paths"],
                    record["app_version"],
                ),
            )
        finally:
            cursor.close()
        conn.commit()
        logger.info(
            "deployment record written: ref=%s head=%s matched=%s",
            record["config_ref"],
            record["config_head"],
            record["pin_matched"],
        )
        return True
    except Exception as exc:  # provenance must never break the deploy
        logger.warning("Failed to write deployment record: %s", exc)
        return False


def record_deployment(config_service: Any, env: Mapping) -> bool:
    """Record this deploy using a ``ConfigService``'s connection.

    This is the seam ``config_seed`` calls, so that module stays a single call
    site. The connection is released in a ``finally`` — a leaked one on the seed
    path would outlive the deploy.
    """
    try:
        conn = config_service._get_connection()
    except Exception as exc:
        logger.warning(
            "Failed to acquire a connection for the deployment record: %s", exc
        )
        return False

    try:
        return write_deployment_record(conn, env)
    finally:
        try:
            config_service._release_connection(conn)
        except Exception as exc:
            logger.warning(
                "Failed to release the deployment-record connection: %s", exc
            )
