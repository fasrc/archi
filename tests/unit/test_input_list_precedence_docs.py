"""Keep the #460 upgrade step in `docs/docs/configuration.md`.

Deployments rendered before #460 carry an `enabled: false` that the old CLI
wrote for every unselected source, which the old scraper ignored when the
input lists held entries. `archi restart` without `--config` keeps those
rendered configs, so the new scraper skips those entries. The docs must tell
operators to re-render after the upgrade.
"""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs" / "docs" / "configuration.md"


def _upgrade_note():
    text = DOCS.read_text()
    marker = "**CAUTION: re-render the configs after you upgrade to this change.**"
    assert marker in text, "the #460 upgrade caution is missing"
    start = text.index(marker)
    return text[start : text.index("\n\n", start)]


def test_upgrade_note_names_both_re_render_commands():
    note = _upgrade_note()
    assert "`archi create --force`" in note
    assert "`archi restart --config`" in note


def test_upgrade_note_says_a_bare_restart_does_not_re_render():
    note = _upgrade_note()
    assert "without `--config`" in note
    assert "git" in note and "sso" in note and "elog" in note
