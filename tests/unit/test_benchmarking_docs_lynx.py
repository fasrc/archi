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


def test_the_section_gives_the_70b_settings_and_the_gpu_count():
    section = _lynx_section()
    assert "PatronusAI/Llama-3-Patronus-Lynx-70B-Instruct" in section
    assert "`GPUS`" in section
    assert "--tensor-parallel-size" in section
    assert "`tensor_parallel_size`" in section
    assert "cannot load" not in section


def test_the_section_shows_the_form_lynx_writes_and_the_parse_errors():
    section = _lynx_section()
    assert """{"REASONING": ['""" in section
    assert '"SCORE": FAIL}' in section
    assert "single-quoted" in section
    assert "cut off" in section
    assert "no strict" not in section


def test_the_section_gives_the_token_limit_and_the_v11_settings():
    section = _lynx_section()
    assert "--context-tokens" in section
    assert "`CONTEXT_TOKENS`" in section
    assert "PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct-v1.1" in section
    assert "00de9f9062d93a95e4e359d3c92b8ea1d0114738" in section
    assert 'CONTEXT_TOKENS="32000"' in section
    assert "VLLM_ALLOW_LONG_MAX_MODEL_LEN" in section
    assert "`token_budget.context_tokens`" in section
    assert "is more than 8,000 tokens" not in section
