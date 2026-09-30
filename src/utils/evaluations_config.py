import os
import posixpath
from pathlib import Path
from typing import Any, Dict, Optional

LIVE_AGENT_CONFIG_PATH = "/root/archi/configs/config.yaml"
AGENT_CONFIG_STAGED_FILENAME = "qa_agent_config.yaml"

# ``WORKDIR`` of the chatbot image
# (``src/cli/templates/dockerfiles/Dockerfile-chat:4``). ``agent_config_path`` is
# consumed inside that container, so a relative value resolves against this
# directory at runtime — never against the host directory ``archi create`` ran in.
CHAT_CONTAINER_WORKDIR = "/root/archi"

_DOTTED_KEY = "services.chat_app.evaluations.agent_config_path"


def validate_evaluations_config(chat_app_config: Optional[Dict[str, Any]]) -> None:
    """Raise ``ValueError`` when an enabled console would be refused at runtime.

    Only fires when ``evaluations.enabled`` is exactly ``True``. A truthy ``1``
    or ``"true"`` does not arm it — mirroring the seam at
    ``evaluation_console.py:90``.

    Since #371, ``agent_config_path`` is a host path (absolute or relative to the
    deployment YAML). The container-workdir join is retained as a conservative
    refusal: a value that would name the live config inside the container is
    refused on the host too, even though host resolution is what ultimately
    matters.

    Two values are refused:
    - A missing, non-string, or blank ``agent_config_path``.
    - A path that normalizes to ``LIVE_AGENT_CONFIG_PATH`` when joined to
      ``CHAT_CONTAINER_WORKDIR`` (the chatbot image's WORKDIR). Path normalization
      only — no ``os.path.samefile`` — because on the host the live config does
      not exist and ``samefile`` would raise.

    Both messages contain ``services.chat_app.evaluations.agent_config_path``.
    The live-config message also states that the live deployment config is refused
    and that a redacted copy should be named instead.
    """
    if not isinstance(chat_app_config, dict):
        return None
    evaluations = chat_app_config.get("evaluations")
    if not isinstance(evaluations, dict):
        return None
    if evaluations.get("enabled") is not True:
        return None

    agent_config_path = evaluations.get("agent_config_path")
    if not isinstance(agent_config_path, str) or not agent_config_path.strip():
        raise ValueError(f"{_DOTTED_KEY} is required when evaluations.enabled is true")

    if _container_path(agent_config_path) == _container_path(LIVE_AGENT_CONFIG_PATH):
        raise ValueError(
            f"{_DOTTED_KEY} names the live deployment config, which is refused. "
            "Name a redacted copy instead."
        )
    return None


def resolve_agent_config_source(config: Dict[str, Any]) -> Optional[Path]:
    """Return the resolved host source path for an enabled console, else None."""
    chat_app = (config.get("services") or {}).get("chat_app") or {}
    evaluations = chat_app.get("evaluations") if isinstance(chat_app, dict) else {}
    if not isinstance(evaluations, dict) or evaluations.get("enabled") is not True:
        return None

    validate_evaluations_config(chat_app)

    raw: str = evaluations["agent_config_path"]
    p = Path(raw).expanduser()
    if not p.is_absolute():
        config_path_str = config.get("_config_path")
        if config_path_str is None:
            raise ValueError(
                f"{_DOTTED_KEY} is a relative path but the config has no _config_path key"
            )
        p = (Path(config_path_str).parent / p).resolve()
    else:
        p = p.resolve()

    if not p.exists():
        raise ValueError(f"{_DOTTED_KEY} not found: {p}")
    if not p.is_file():
        raise ValueError(f"{_DOTTED_KEY} must be a file: {p}")
    if not os.access(p, os.R_OK):
        raise ValueError(f"{_DOTTED_KEY} is not readable: {p}")

    config_path_str = config.get("_config_path")
    if config_path_str is not None:
        try:
            if os.path.samefile(p, config_path_str):
                raise ValueError(
                    f"{_DOTTED_KEY} names the live deployment config, which is refused. "
                    "Name a redacted copy instead."
                )
        except OSError:
            pass

    return p


def _container_path(raw: str) -> str:
    """Normalize ``raw`` the way the chatbot container will read it.

    ``posixpath`` rather than ``Path.resolve()``: the value names a path inside
    the container, so consulting the host filesystem for it is meaningless and
    would make the verdict depend on the host's own ``/root/archi``.
    """
    candidate = raw.strip()
    if not posixpath.isabs(candidate):
        candidate = posixpath.join(CHAT_CONTAINER_WORKDIR, candidate)
    candidate = posixpath.normpath(candidate)
    # ``normpath`` collapses three or more leading slashes but keeps exactly two,
    # because POSIX leaves ``//foo`` implementation-defined. Linux treats it as
    # ``/foo``, so ``//root/archi/configs/config.yaml`` IS the live config and the
    # runtime seam refuses it on inode identity. Collapse it here too, or the
    # preflight would accept a path the deployed console then rejects.
    if candidate.startswith("//"):
        candidate = "/" + candidate.lstrip("/")
    return candidate
