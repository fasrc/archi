import posixpath
from typing import Any, Dict, Optional

LIVE_AGENT_CONFIG_PATH = "/root/archi/configs/config.yaml"

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

    Two values are refused:
    - A missing, non-string, or blank ``agent_config_path``.
    - A path that normalizes to ``LIVE_AGENT_CONFIG_PATH``. A relative value is
      first joined to ``CHAT_CONTAINER_WORKDIR``, because the container — not the
      host CLI — is what resolves it; without that join ``configs/config.yaml``
      resolves against the operator's working directory, passes preflight, and is
      then refused by ``build_evaluation_service`` after deployment. Path
      normalization only — no ``os.path.samefile`` — because on the host the live
      config does not exist and ``samefile`` would raise (design.md D3).

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
