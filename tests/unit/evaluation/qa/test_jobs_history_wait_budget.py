"""Guard for #655: job waits in test_jobs_history.py must not use a tight limit.

A shared CI runner can take more than 2 s to finish a background job, and
``JobManager.wait`` then raises ``TimeoutError`` in a test that is otherwise
green. A passing wait returns as soon as the job ends, so a generous limit
costs nothing; only a real hang reaches it.
"""

import ast
from pathlib import Path

from tests.unit.evaluation.qa import test_jobs_history

SOURCE = Path(test_jobs_history.__file__).read_text()


def _literal_timeout_waits(source):
    """Line numbers of ``.wait(..., timeout=<number>)`` calls, nested args included."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "wait"
        ):
            continue
        for keyword in node.keywords:
            if keyword.arg == "timeout" and isinstance(keyword.value, ast.Constant):
                lines.append(node.lineno)
    return lines


def test_the_job_wait_limit_is_generous():
    assert test_jobs_history.JOB_WAIT_TIMEOUT >= 30


def test_the_scan_sees_a_wait_with_nested_call_arguments():
    source = 'manager.wait(manager.list()[0]["id"], timeout=2)\n'

    assert _literal_timeout_waits(source) == [1]


def test_every_job_wait_uses_the_shared_limit():
    assert _literal_timeout_waits(SOURCE) == []
