from pathlib import Path
from typing import Any, Dict, Optional

LIVE_AGENT_CONFIG_PATH = "/root/archi/configs/config.yaml"

_DOTTED_KEY = "services.chat_app.evaluations.agent_config_path"


def validate_evaluations_config(chat_app_config: Optional[Dict[str, Any]]) -> None:
    """Raise ``ValueError`` when an enabled console would be refused at runtime.

    Only fires when ``evaluations.enabled`` is exactly ``True``. A truthy ``1``
    or ``"true"`` does not arm it — mirroring the seam at
    ``evaluation_console.py:90``.

    Two values are refused:
    - A missing, non-string, or blank ``agent_config_path``.
    - A path that normalizes to ``LIVE_AGENT_CONFIG_PATH`` under
      ``Path(...).resolve()``. Path normalization only — no ``os.path.samefile``
      — because on the host the live config does not exist and ``samefile`` would
      raise (design.md D3).

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

    if Path(agent_config_path).resolve() == Path(LIVE_AGENT_CONFIG_PATH).resolve():
        raise ValueError(
            f"{_DOTTED_KEY} names the live deployment config, which is refused. "
            "Name a redacted copy instead."
        )
    return None
