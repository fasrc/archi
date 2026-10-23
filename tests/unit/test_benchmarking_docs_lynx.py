"""Keep the Lynx section of the benchmarking docs in step with the job and the scorer (#735).

`scripts/benchmarking/slurm/lynx_score.sbatch` and `scripts/benchmarking/lynx/score.py`
write `<basename>-arm<N>.lynx.json`, and `compare_runs.py` finds it only in the folder of
the result JSON. So `docs/docs/benchmarking.md` must say where the file goes, what its
counts mean, and that the PASS rate gates nothing (design D12).
"""

from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs" / "docs" / "benchmarking.md"
HEADING = "## Lynx faithfulness (offline)"


def _lynx_section():
    text = DOCS.read_text()
    start = text.index(HEADING)
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_the_section_says_lynx_scores_only_deferred_runs():
    section = _lynx_section()
    assert "`judge_mode: deferred`" in section
    assert "BUNDLE_COMPLETE" in section


def test_the_section_gives_the_job_and_its_dry_run():
    section = _lynx_section()
    assert "sbatch scripts/benchmarking/slurm/lynx_score.sbatch" in section
    assert "--dry-run" in section
    assert "LYNX_LAB" in section


def test_the_section_gives_the_scorer_invocation_and_the_out_dir_rule():
    section = _lynx_section()
    assert "python -m scripts.benchmarking.lynx.score" in section
    assert "--out-dir" in section
    assert "folder of the result JSON" in section
    assert "outside the bundle folder" in section
    assert "`compare_runs.py`" in section


@pytest.mark.parametrize(
    "name",
    [
        "lynx_scored",
        "lynx_pass",
        "lynx_skipped_overflow",
        "lynx_parse_errors",
        "lynx_faithfulness",
    ],
)
def test_the_section_names_each_count(name):
    assert f"`{name}`" in _lynx_section()


def test_the_section_says_how_to_read_the_rate():
    section = _lynx_section()
    assert "`<basename>-arm<N>.lynx.json`" in section
    assert "scored rows only" in section
    assert "gates nothing" in section
