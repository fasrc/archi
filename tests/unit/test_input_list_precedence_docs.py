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
    # Join the wrapped lines so a phrase split across a line break still matches.
    return " ".join(text[start : text.index("\n\n", start)].split())


def test_upgrade_note_names_create_force_as_the_only_fix():
    note = _upgrade_note()
    assert "`archi create --force`" in note
    # restart --config refuses any data_manager change
    # (_validate_non_chatbot_sections), so it cannot do this migration.
    assert "or `archi restart --config`" not in note
    assert "`archi restart --config` refuses" in note


def test_upgrade_note_says_a_bare_restart_does_not_re_render():
    note = _upgrade_note()
    assert "without `--config`" in note
    assert "git" in note and "sso" in note and "elog" in note
