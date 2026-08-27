"""Validate the evaluations root path against the fixed compose bind mount.

The check is lexical and runs before the compose teardown (design D2, D3):
after teardown the overlay is gone and the path cannot be probed at runtime.
"""

import posixpath
from pathlib import PurePosixPath

EVALUATIONS_MOUNT_PATH = "/root/archi/evaluations"


def validate_evaluations_root(chat_app_config):
    """Raise ValueError if the configured evaluations root falls outside the mount."""
    evaluations = chat_app_config.get("evaluations", {})
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
            f"into the container"
        )
