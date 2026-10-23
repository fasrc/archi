"""The `GET /api/schedules` docs must name only the uploader keys a schedule job shares.

``docs/docs/api_reference.md`` says a manual ingest through the uploader writes the same
``state`` entry that a scheduled refresh writes (last writer wins). That is true only for
an uploader key that is also a schedule job name: the URL upload writes ``web``, but the
link schedule job reads ``links``, so a URL upload never changes the ``links`` job (Codex
review, PR #694).

This reads the backticked keys out of the docs sentence and compares them, as a set,
against the keys the uploader writes with ``state="idle"`` that are also schedule job
names in ``service_data_manager.py``.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_REFERENCE = ROOT / "docs" / "docs" / "api_reference.md"
UPLOADER = ROOT / "src" / "interfaces" / "uploader_app" / "app.py"
DATA_MANAGER = ROOT / "src" / "bin" / "service_data_manager.py"

_UPLOADER_IDLE_KEY = re.compile(
    r'_update_source_status\(\s*"(\w+)",\s*state="idle"', re.MULTILINE
)
_SCHEDULE_KEY = re.compile(r'^\s*"(\w+)": lambda last_run', re.MULTILINE)
_BACKTICKED = re.compile(r"`([^`]+)`")


def _schedules_section() -> str:
    text = API_REFERENCE.read_text()
    start = text.index("### `GET /api/schedules`")
    end = text.index("\n---", start)
    return text[start:end]


def _documented_shared_keys() -> set[str]:
    paragraphs = [p for p in _schedules_section().split("\n\n") if "last writer" in p]
    assert len(paragraphs) == 1, "expected one last-writer-wins paragraph"
    return set(_BACKTICKED.findall(paragraphs[0])) - {"state"}


def test_documented_shared_keys_match_the_code():
    uploader_keys = set(_UPLOADER_IDLE_KEY.findall(UPLOADER.read_text()))
    schedule_keys = set(_SCHEDULE_KEY.findall(DATA_MANAGER.read_text()))
    # The regexes still match the source, so the set comparison is not vacuous.
    assert "web" in uploader_keys and "links" in schedule_keys
    assert _documented_shared_keys() == uploader_keys & schedule_keys


def test_web_upload_is_named_as_the_exception():
    section = _schedules_section()
    assert "`web`" in section and "`links`" in section
