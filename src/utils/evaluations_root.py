"""Validate the evaluations root path against the fixed compose bind mount.

The check is lexical and runs before the compose teardown (design D2, D3):
after teardown the overlay is gone and the path cannot be probed at runtime.
"""

import posixpath
from pathlib import PurePosixPath

EVALUATIONS_MOUNT_PATH = "/root/archi/evaluations"


def validate_evaluations_root(chat_app_config):
    """Raise ValueError if the configured evaluations root falls outside the mount."""
    # ``or {}`` mirrors the runtime normalization at ``evaluation_console.py:91``:
    # ``evaluations: null`` is an inert shape the deployed console already accepts
    # as "disabled", so refusing it at create time would reject a config that
    # deploys cleanly. A TRUTHY non-mapping is different -- it is malformed, and
    # an actionable error beats the AttributeError that ``.get`` would raise.
    evaluations = chat_app_config.get("evaluations") or {}
    if not isinstance(evaluations, dict):
        raise ValueError(
            "services.chat_app.evaluations must be a mapping, got "
            f"{type(evaluations).__name__}"
        )
    if evaluations.get("enabled") is not True:
        return None

    root = evaluations.get("root")
    if root is None:
        return None

    if not isinstance(root, str):
        raise ValueError(
            f"services.chat_app.evaluations.root must be a string, got {type(root).__name__}"
        )
    if not root:
        raise ValueError(
            "services.chat_app.evaluations.root must not be empty; an absolute "
            f"container path under {EVALUATIONS_MOUNT_PATH} is required"
        )

    candidate = PurePosixPath(posixpath.normpath(root))
    if not candidate.is_absolute():
        raise ValueError(
            f"evaluations.root {str(root)!r} is not an absolute path; an absolute "
            f"container path under {EVALUATIONS_MOUNT_PATH} is required because no "
            f"working directory is pinned for the container (design D3)"
        )
    mount = PurePosixPath(EVALUATIONS_MOUNT_PATH)

    # A startswith test would accept "/root/archi/evaluations-backup" as a false positive.
    if candidate != mount and mount not in candidate.parents:
        raise ValueError(
            f"evaluations.root {str(root)!r} is outside the compose bind mount "
            f"{EVALUATIONS_MOUNT_PATH!r}; only {EVALUATIONS_MOUNT_PATH} is mounted "
            f"into the container. Set root to {EVALUATIONS_MOUNT_PATH}, or to a "
            f"path beneath it such as {EVALUATIONS_MOUNT_PATH}/trial-a to keep "
            f"catalogs side by side on the same volume"
        )
