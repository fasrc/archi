"""Guard for #655: job waits in test_jobs_history.py must not use a tight limit.

A shared CI runner can take more than 2 s to finish a background job, and
``JobManager.wait`` then raises ``TimeoutError`` in a test that is otherwise
green. A passing wait returns as soon as the job ends, so a generous limit
costs nothing; only a real hang reaches it.
"""

import re
from pathlib import Path

from tests.unit.evaluation.qa import test_jobs_history

SOURCE = Path(test_jobs_history.__file__).read_text()


def test_the_job_wait_limit_is_generous():
    assert test_jobs_history.JOB_WAIT_TIMEOUT >= 30


def test_every_job_wait_uses_the_shared_limit():
    literal_waits = re.findall(r"\.wait\([^)]*timeout=\d", SOURCE)

    assert literal_waits == []
