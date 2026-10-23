import os

REPO_SELECTION_VARS = frozenset(
    [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
    ]
)


def git_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in REPO_SELECTION_VARS}
