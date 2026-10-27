"""The links schedule obeys a stored scraper marker, whatever the URL shape (#640).

Spec: openspec/changes/fix-issue-640-scraper-marker-survives-refetch/specs/scraper-source-marker/spec.md
Requirement: The links schedule SHALL NOT re-fetch a marked row of an explicitly
disabled source.

These are guards (design D3). With the upsert clause of design D1, a plain link re-fetch
keeps the marker, so a disabled source's row stays out of the links schedule. The
sibling assertions are in tests/unit/test_catalog_postgres_upsert_scraper_marker.py; the
two files together are the whole chain.
"""

import types

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager

_PLAIN = ("h1", {"url": "https://x/page", "source_type": "web"})
# An explicit elog- entry can carry any path (tests/unit/test_input_list_prefixes.py:33),
# so the /elog/ shape fallback cannot rescue this row: only its marker names the source.
_ELOG_NO_PATH = (
    "h2",
    {"url": "https://x/r.git", "source_type": "web", "scraper": "elog"},
)
_INDICO = (
    "h3",
    {"url": "https://indico.h/event/1/", "source_type": "web", "scraper": "indico"},
)


def _links_schedule_urls(monkeypatch, tmp_path, sources_config, web_rows):
    monkeypatch.setattr(
        sm_module, "get_global_config", lambda: {"DATA_PATH": str(tmp_path)}
    )
    manager = ScraperManager({"sources": sources_config})
    monkeypatch.setattr(
        manager, "_collect_urls_from_lists_by_type", lambda _l: ([], [], [], [], [], [])
    )
    seen = {}
    monkeypatch.setattr(
        manager,
        "collect_links",
        lambda persistence, link_urls=None: seen.setdefault("urls", link_urls),
    )

    def get_metadata_by_filter(field, value=None, metadata_keys=None, **kw):
        rows = [(h, m) for h, m in web_rows if m.get(field) == kw.get(field, value)]
        if metadata_keys:
            rows = [(h, {k: m[k] for k in metadata_keys if k in m}) for h, m in rows]
        return rows

    persistence = types.SimpleNamespace(
        data_path=tmp_path,
        catalog=types.SimpleNamespace(get_metadata_by_filter=get_metadata_by_filter),
    )
    manager.schedule_collect_links(persistence)
    return seen["urls"]


def test_links_schedule_skips_a_marked_elog_row_with_no_elog_path_when_disabled(
    monkeypatch, tmp_path
):
    urls = _links_schedule_urls(
        monkeypatch, tmp_path, {"elog": {"enabled": False}}, [_PLAIN, _ELOG_NO_PATH]
    )
    assert urls == ["https://x/page"]


def test_links_schedule_skips_a_marked_indico_row_when_disabled(monkeypatch, tmp_path):
    urls = _links_schedule_urls(
        monkeypatch, tmp_path, {"indico": {"enabled": False}}, [_PLAIN, _INDICO]
    )
    assert urls == ["https://x/page"]


def test_links_schedule_keeps_marked_rows_when_flags_absent(monkeypatch, tmp_path):
    urls = _links_schedule_urls(
        monkeypatch, tmp_path, {}, [_PLAIN, _ELOG_NO_PATH, _INDICO]
    )
    assert urls == ["https://x/page", "https://x/r.git", "https://indico.h/event/1/"]
