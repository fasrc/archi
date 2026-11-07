"""Keep `docs/docs/configuration.md` in step with restart's normalization (#461).

`archi restart --config` now calls `set_sources_enabled()` like `create` and
`evaluate`, so the docs must not tell operators that restart skips it.
"""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs" / "docs" / "configuration.md"


def test_docs_do_not_claim_restart_skips_normalisation():
    text = DOCS.read_text()
    assert "`archi restart --config` does not do that normalisation" not in text
    assert "Until it is fixed" not in text


def test_docs_name_restart_as_a_normalising_command():
    text = DOCS.read_text()
    assert "`archi restart --config`" in text
    assert "`archi create`, `archi evaluate` and `archi restart --config`" in text
