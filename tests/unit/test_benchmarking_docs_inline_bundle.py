"""The docs name the third judge mode and the checkpoint precision
(add-inline-bundle-judge-mode, task 7.1)."""

from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs" / "docs"


def _text():
    return (DOCS / "benchmarking.md").read_text()


def _judge_mode_row():
    rows = [
        line for line in _text().splitlines() if line.startswith("| `judge_mode` |")
    ]
    assert len(rows) == 1
    return rows[0]


def test_the_judge_mode_row_names_the_three_values():
    row = _judge_mode_row()
    for value in ("`inline`", "`deferred`", "`inline+bundle`"):
        assert value in row


def test_the_judge_mode_row_no_longer_promises_only_two_values():
    assert "Any other value, including `false` and `Deferred`" not in _judge_mode_row()


@pytest.mark.parametrize(
    "needle",
    [
        "`judge_mode: inline+bundle`",
        "before it builds the judge",
        "`judge_bundle`",
        "no `judge_status`",
        "publish.py` copies only `deferred` bundles",
    ],
)
def test_the_inline_bundle_paragraph_says_what_it_does(needle):
    assert needle in _text()


def test_the_judge_env_example_names_the_checkpoint_precision():
    text = _text()
    assert "# bf16, fp16, fp8, or checkpoint" in text
    assert "JUDGE_PRECISION=bf16" in text
