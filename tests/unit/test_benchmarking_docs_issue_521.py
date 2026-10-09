"""Keep `docs/docs/benchmarking.md` in step with the #521 fixes.

The `timeout` row must state the validation contract `_positive_number`
enforces, and the record's `configuration` field is no longer called verbatim:
it holds the prompt-expanded file, while `selected_file_digest` covers the
file as written.
"""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs" / "docs" / "benchmarking.md"


def _timeout_row():
    rows = [
        line
        for line in DOCS.read_text().splitlines()
        if line.startswith("| `mode_settings.ragas_settings.timeout`")
    ]
    assert len(rows) == 1
    return rows[0]


def test_timeout_row_states_the_validation_contract():
    row = _timeout_row()
    assert "positive number" in row
    assert "oversized integer" in row
    assert "falls back to `180` with a warning" in row
    assert "`600.0` is recorded as `600`" in row


def test_configuration_is_not_called_verbatim():
    text = " ".join(DOCS.read_text().split())
    assert "The configuration is also recorded verbatim as `configuration`" not in text
    assert "each prompt path replaced by that file's contents" in text
    assert "before that replacement" in text
