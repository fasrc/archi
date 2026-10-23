"""Keep the judge offload docs in step with the Slurm scripts and the gates (#733, task 4.6).

`docs/docs/benchmarking.md` holds the operator steps of the offloaded RAGAS judge: the
image and weights download, the scorer image, `judge.env`, the `scrontab` entry,
`judge_mode: deferred`, how to read the queue, and how to copy judged files into
`bench_out/`. `docs/docs/interpreting_benchmark_results.md` lists G11 and G12 in its gate
table and in Procedure C.
"""

from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs" / "docs"
HEADING = "## Offloaded RAGAS judge on Slurm"


def _section():
    text = (DOCS / "benchmarking.md").read_text()
    start = text.index(HEADING)
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


@pytest.mark.parametrize(
    "needle",
    [
        "scripts/benchmarking/slurm/build_scorer_image.sh",
        "docker save",
        "scorer-identity.json",
        "judge.env",
        "JUDGE_MODEL_REVISION",
        "JUDGE_BOOTSTRAP_ROWS_PER_MINUTE",
        "JUDGE_LAB",
        "scrontab",
        "scripts/benchmarking/slurm/judge_submit.sh",
        "judge_submit.sh --status",
        "scripts/benchmarking/slurm/archi_judge.sbatch --dry-run",
        "scripts/benchmarking/slurm/judge_merge.sbatch --dry-run",
        "`judge_mode: deferred`",
        "publish.py",
        "SCORER_STALE",
        "INCOMPATIBLE",
        "MERGED",
        "<basename>.judged.json",
        "bench_out/",
        "#729",
    ],
)
def test_the_operator_section_names(needle):
    assert needle in _section()


def test_the_judge_env_example_loads_with_the_real_parser(tmp_path):
    import importlib.util

    section = _section()
    start = section.index("### 2. Write judge.env")
    block = section[start:].split("```bash\n", 1)[1].split("```", 1)[0]
    path = tmp_path / "judge.env"
    path.write_text(block)
    queue_py = DOCS.parents[1] / "scripts" / "benchmarking" / "judge" / "judge_queue.py"
    spec = importlib.util.spec_from_file_location("docs_judge_queue", queue_py)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = module.read_judge_env(path)
    assert values["JUDGE_PRECISION"] == "bf16"
    assert float(values["JUDGE_BOOTSTRAP_ROWS_PER_MINUTE"]) > 0
    assert values["JUDGE_SERVER_ARGS"] == "--max-model-len 16384"
    assert "#" not in "".join(v or "" for v in values.values())


def test_the_deferred_section_no_longer_says_the_scorer_is_missing():
    text = (DOCS / "benchmarking.md").read_text()
    assert "until then, a deferred result has no RAGAS scores" not in text


def test_the_gate_table_has_g11_and_g12():
    text = (DOCS / "interpreting_benchmark_results.md").read_text()
    assert "| **G11** |" in text and "| **G12** |" in text
    assert "unless all ten items below pass" in text


def test_procedure_c_implements_g3_to_g12_and_says_what_they_refuse():
    text = (DOCS / "interpreting_benchmark_results.md").read_text()
    assert "It implements G3–G12" in text
    assert "`judge_status: pending`" in text
    assert "`ragas_judge_identity_effective`" in text
    assert "(G11)" in text and "(G12)" in text
