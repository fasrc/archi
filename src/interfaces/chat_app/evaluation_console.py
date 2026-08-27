"""Chat-app seam for the QA evaluation console.

``app.py`` is not imported by the unit suite, so every decision the console
needs — is it enabled, may this request proceed, does the nav link show —
lives here where the gate can cover it. ``app.py`` keeps thin call sites only
(pattern: ``config_fingerprint.py``).

Three decisions here diverge from upstream on purpose. ``build_evaluation_service``
refuses an enabled console that names no ``agent_config_path``, refuses the live
deployment config outright, because each run copies that file into its own run
directory, and refuses a storage root construction cannot use (details on the
function).

And the ``authorize_request`` callable is narrower than upstream's: it has no
bearer-token or SSO branch, so an SSO deployment that turns the console on gets a
401 instead of a login redirect. That is a recorded trial divergence — the console
is off by default and the dev stack runs auth-off.
"""

import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flask import jsonify, request, session

from src.evaluation.qa.console import EvaluationConsoleService
from src.utils.logging import get_logger
from src.utils.rbac.permission_enum import Permission
from src.utils.rbac.permissions import has_permission

logger = get_logger(__name__)

from src.utils.evaluations_config import LIVE_AGENT_CONFIG_PATH

DEFAULT_EVALUATION_ROOT = "/root/archi/evaluations"
DEFAULT_AGENTS_DIR = "/root/archi/agents"


def _is_live_agent_config(candidate: Path) -> bool:
    """Return ``True`` when ``candidate`` names the live deployment config.

    Two questions, because neither answers alone. ``os.path.samefile`` compares
    device and inode, so it catches a hard link and a bind mount of the live
    config — aliases no amount of path canonicalization can see. It needs both
    files to exist, though, and it raises when either is missing or unreadable.
    So a resolved-path comparison follows: it covers a path that does not exist
    yet, and it stands in wherever the live config itself is absent (a test host,
    anything outside the container), where identity cannot be asked at all.
    """
    live = Path(LIVE_AGENT_CONFIG_PATH)
    try:
        if os.path.samefile(candidate, live):
            return True
    except OSError:
        pass
    return candidate.resolve() == live.resolve()


def build_evaluation_service(
    chat_app_config: Dict[str, Any]
) -> Optional[EvaluationConsoleService]:
    """Return the console service, or ``None`` when evaluations are off.

    ``enabled`` must be exactly ``True``: a truthy ``1`` or ``"true"`` left in a
    config by mistake must not expose the console.

    ``agent_config_path`` has no default, and the live deployment config
    ``/root/archi/configs/config.yaml`` is refused outright. Both rules are fork
    policy, not upstream's. Every run copies the named file into its own run
    directory as ``agent_config.resolved.yaml``, on a host mount the console then
    serves, so naming the live config would publish that config's secrets. Name a
    redacted copy instead. The refusal covers every way of naming that one file:
    an odd spelling (a ``..`` segment, a doubled separator), a symlink, and a
    hard link or bind mount that shares its inode (see
    ``_is_live_agent_config``). ``archi create`` refuses both of these values
    up front as a fast preflight diagnostic; this seam remains the authority
    because the running configuration can be changed after create.

    The same refusal covers ``evaluations.root``: when constructing the service
    hits an ``OSError`` — an unwritable or unusable root, or a stale-job sweep
    that cannot write during construction — the console disables itself rather
    than raise. The net is ``OSError`` only, not ``Exception``, because a wider
    net would catch a programming error too and report it as a disabled console
    instead of surfacing the defect.

    Each refusal logs an error and returns ``None``. ``app.py`` calls this during
    init, so the console turns itself off while chat stays up.
    """
    chat_app_config = chat_app_config or {}
    evaluations_config = chat_app_config.get("evaluations") or {}
    if evaluations_config.get("enabled") is not True:
        return None

    agent_config_path = evaluations_config.get("agent_config_path")
    if not isinstance(agent_config_path, str) or not agent_config_path.strip():
        logger.error(
            "Evaluation console disabled: evaluations.agent_config_path is "
            "required when evaluations.enabled is true. Every run copies that "
            "file into its run directory, so name a redacted copy of the agent "
            "config, never the live deployment config."
        )
        return None
    if _is_live_agent_config(Path(agent_config_path)):
        logger.error(
            "Evaluation console disabled: evaluations.agent_config_path must not "
            "be the live deployment config %s. Every run copies that file into "
            "its run directory, where the console serves it, secrets included. "
            "Name a redacted copy instead.",
            LIVE_AGENT_CONFIG_PATH,
        )
        return None

    mcp_config_path = evaluations_config.get("mcp_config_path")
    root = evaluations_config.get("root", DEFAULT_EVALUATION_ROOT)
    try:
        service = EvaluationConsoleService(
            Path(root),
            agent_config_path=Path(agent_config_path),
            agents_dir=Path(chat_app_config.get("agents_dir") or DEFAULT_AGENTS_DIR),
            mcp_config_path=Path(mcp_config_path) if mcp_config_path else None,
        )
        # Construction alone does not prove the storage is usable. It only calls
        # `mkdir(parents=True, exist_ok=True)` seven times and sweeps stale jobs,
        # which writes solely when it finds a job to interrupt — so a read-only
        # mount already holding the five directories, the shape an operator gets
        # restoring a snapshot onto a read-only volume, raises nothing at all.
        # The console would register and the first dataset import would 500.
        #
        # Probe the five directories the console writes into, not the root: the
        # root itself is never written: imports and drafts stage through
        # `TemporaryDirectory(dir=...)` inside `datasets`, `profiles` and
        # `drafts`, runs are workspaces under `runs`, and job records live in
        # `jobs`. A writable root with one tighter-permissioned or separately
        # mounted child passes a root-only probe and fails on first use. Each
        # probe file is deleted on close, so the tree is left as found, and the
        # `OSError` carries the offending path into the message below.
        for directory in (
            service.catalog.datasets_dir,
            service.catalog.profiles_dir,
            service.catalog.drafts_dir,
            service.catalog.runs_dir,
            service.catalog.jobs_dir,
        ):
            with tempfile.NamedTemporaryFile(
                dir=directory, prefix=".archi-write-probe-"
            ):
                pass
        return service
    except OSError as exc:
        logger.error(
            "Evaluation console disabled: evaluations.root %s is not usable: %s",
            root,
            exc,
        )
        return None


def build_authorize_request(auth_enabled: bool) -> Callable[[str], Optional[Any]]:
    """Return the permission check the evaluation blueprint calls per route.

    The returned callable answers ``None`` when the request may proceed, and a
    ``(response, status)`` pair otherwise.

    Auth scope — fail-closed on purpose. With auth on, the one credential this
    check accepts is a Flask login session (``session["logged_in"]``) that
    carries roles. Every other credential the main app accepts — a bearer
    token, an SSO session, an OIDC redirect — gets a flat 401 here, because
    this callable holds no bearer branch and no SSO branch at all.

    That is the recorded scope of this trial, not an oversight. The capability
    ships dark: the console exists only when ``evaluations.enabled`` is exactly
    ``True``, which no deployed config sets, and the dev stack runs auth-off. An
    SSO-aware or bearer-aware ``authorize_request`` is a written adoption
    precondition (proposal.md "Not in scope"; adopt writeup, tasks.md 7.3), so
    do not enable this console on an auth-on deployment before that lands.
    """

    def authorize_request(permission: str) -> Optional[Any]:
        if not auth_enabled:
            return None

        if not session.get("logged_in"):
            return (
                jsonify(
                    {"error": "Unauthorized", "message": "Authentication required"}
                ),
                401,
            )

        roles = session.get("roles", [])
        if not has_permission(permission, roles):
            user_email = session.get("user", {}).get("email", "unknown")
            logger.warning(
                f"Permission denied: user {user_email} with roles {roles} "
                f"lacks '{permission}' on {request.path}"
            )
            return (
                jsonify(
                    {
                        "error": "Forbidden",
                        "message": f"Permission denied: requires {permission}",
                        "required_permission": permission,
                    }
                ),
                403,
            )

        return None

    return authorize_request


def can_view_evaluations(evaluations_enabled: bool, auth_enabled: bool) -> bool:
    """Whether the chat header shows the evaluation console link."""
    if not evaluations_enabled:
        return False
    return not auth_enabled or has_permission(Permission.Evaluations.VIEW)
