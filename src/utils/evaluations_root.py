"""Validate the evaluations root path against the fixed compose bind mount.

The check is lexical and runs before the compose teardown (design D2, D3):
after teardown the overlay is gone and the path cannot be probed at runtime.
"""

import posixpath
from pathlib import PurePosixPath

EVALUATIONS_MOUNT_PATH = "/root/archi/evaluations"


def validate_evaluations_root(chat_app_config):
    """Raise ValueError if the configured evaluations root falls outside the mount."""
    root = chat_app_config["evaluations"]["root"]
    candidate = PurePosixPath(posixpath.normpath(root))
    mount = PurePosixPath(EVALUATIONS_MOUNT_PATH)

    if candidate != mount:
        raise ValueError(
            f"evaluations.root {str(root)!r} is outside the compose bind mount "
            f"{EVALUATIONS_MOUNT_PATH!r}; only {EVALUATIONS_MOUNT_PATH} is mounted "
            f"into the container"
        )
