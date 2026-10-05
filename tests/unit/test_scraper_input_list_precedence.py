"""Input-list enabled/disabled precedence for the scraper (issue #460, task 2.2).

An explicit ``enabled: false`` (or section is the boolean ``False``) SHALL win
over input-list entries; an absent or true ``enabled`` key SHALL let entries be
collected.  ``schedule_collect_elog`` respects ``elog.enabled: false``.  Boolean
``sso: False`` and ``git: False`` sections no longer crash or mis-set the flag.
"""

import logging
import types

import pytest

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(monkeypatch, tmp_path, sources_config=None):
    monkeypatch.setattr(
        sm_module, "get_global_config", lambda: {"DATA_PATH": str(tmp_path)}
    )
    dm_config = {"sources": sources_config} if sources_config is not None else {}
    return ScraperManager(dm_config)


def _fake_persistence(tmp_path):
    p = types.SimpleNamespace()
    p.data_path = tmp_path
    catalog = types.SimpleNamespace()
    catalog.get_metadata_by_filter = lambda *a, **kw: []
    p.catalog = catalog
    return p


def _write_list(tmp_path, lines):
    weblists = tmp_path / "weblists"
    weblists.mkdir(exist_ok=True)
    (weblists / "test.list").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Parametrize matrix: 4 sources × 4 flag states
# ---------------------------------------------------------------------------

_SOURCE_ENTRY = {
    "git": "git-https://x/r.git",
    "sso": "sso-https://x/s",
    "elog": "elog-https://h/elog/lb",
    "indico": "indico-https://indico.h/event/1",
}

_SOURCE_STRIPPED = {
    "git": "https://x/r.git",
    "sso": "https://x/s",
    "elog": "https://h/elog/lb",
    "indico": "https://indico.h/event/1",
}

_KWARG = {
    "git": "git_urls",
    "sso": "sso_urls",
    "elog": "extra_urls",
    "indico": "indico_urls",
}

# (source, config_value, expect_disabled)
_CASES = []
for _src in ("git", "sso", "elog", "indico"):
    _CASES.append(
        pytest.param(_src, {"enabled": False}, True, id=f"{_src}-enabled-false")
    )
    _CASES.append(pytest.param(_src, {}, False, id=f"{_src}-key-absent"))
    _CASES.append(
        pytest.param(_src, {"enabled": True}, False, id=f"{_src}-enabled-true")
    )
    _CASES.append(pytest.param(_src, False, True, id=f"{_src}-section-bool-false"))


@pytest.mark.parametrize("source, source_cfg, expect_disabled", _CASES)
def test_enabled_precedence(
    source, source_cfg, expect_disabled, monkeypatch, tmp_path, caplog
):
    """Explicit false/bool-False disables; absent/true passes list entries through."""
    sources_config = {
        "links": {"input_lists": ["test.list"]},
        source: source_cfg,
    }
    manager = _make_manager(monkeypatch, tmp_path, sources_config)
    _write_list(tmp_path, [_SOURCE_ENTRY[source]])
    monkeypatch.chdir(tmp_path)

    kwarg = _KWARG[source]
    recorded = {}

    def _fake_collect(persistence, **kwargs):
        recorded["urls"] = kwargs.get(kwarg, [])
        recorded["enabled_at_call"] = getattr(manager, f"{source}_enabled", None)
        return 0

    monkeypatch.setattr(manager, f"collect_{source}", _fake_collect)
    # Stub the other collectors to no-ops to avoid side-effects
    for other in ("git", "sso", "elog", "indico", "links"):
        if other != source:
            monkeypatch.setattr(manager, f"collect_{other}", lambda *a, **k: 0)
    # Stub sitemap expansion (not needed here)
    monkeypatch.setattr(manager, "_refresh_sitemap_lastmod_map", lambda *a, **k: [])

    persistence = _fake_persistence(tmp_path)

    with caplog.at_level(
        logging.INFO, logger="src.data_manager.collectors.scrapers.scraper_manager"
    ):
        manager.collect_all_from_config(persistence)

    assert "urls" in recorded, "collector was not called"

    if expect_disabled:
        assert (
            recorded["urls"] == [] or not recorded["urls"]
        ), f"disabled {source}: expected empty list, got {recorded['urls']}"
        # Exactly one WARNING mentioning the source name and the entry count (1)
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and source in r.message and "1" in r.message
        ]
        assert (
            len(warnings) == 1
        ), f"expected 1 WARNING for disabled {source}, got: " + str(
            [(r.levelno, r.message) for r in caplog.records]
        )
    else:
        # URL was passed through
        assert _SOURCE_STRIPPED[source] in (
            recorded["urls"] or []
        ), f"enabled {source}: expected URL in collector args, got {recorded['urls']}"
        if source_cfg == {}:
            # "absent" case: manager must have enabled the source at call time
            if source in ("git", "sso", "indico"):
                assert (
                    recorded["enabled_at_call"] is True
                ), f"absent key: {source}_enabled should be True at call time"
            # Exactly one INFO mentioning the source name
            infos = [
                r
                for r in caplog.records
                if r.levelno == logging.INFO and source in r.message
            ]
            assert (
                len(infos) >= 1
            ), f"absent key: expected INFO for {source}, got: " + str(
                [(r.levelno, r.message) for r in caplog.records]
            )


# ---------------------------------------------------------------------------
# schedule_collect_elog obeys elog.enabled: false
# ---------------------------------------------------------------------------


def test_schedule_collect_elog_disabled(monkeypatch, tmp_path, caplog):
    """schedule_collect_elog must not call collect_elog when elog.enabled is false."""
    sources_config = {"elog": {"enabled": False, "url": "https://h/elog/lb"}}
    manager = _make_manager(monkeypatch, tmp_path, sources_config)

    collect_elog_called = []

    def _fake_collect_elog(*a, **k):
        collect_elog_called.append(True)
        return 0

    monkeypatch.setattr(manager, "collect_elog", _fake_collect_elog)
    persistence = _fake_persistence(tmp_path)
    persistence.catalog.get_metadata_by_filter = lambda *a, **kw: [
        ("d1", {"url": "https://h/elog/x"})
    ]

    with caplog.at_level(
        logging.WARNING, logger="src.data_manager.collectors.scrapers.scraper_manager"
    ):
        manager.schedule_collect_elog(persistence)

    assert (
        not collect_elog_called
    ), "collect_elog must not be called when elog.enabled: false"
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "elog" in r.message.lower()
    ]
    assert warnings, "expected a WARNING when schedule_collect_elog is skipped"


# ---------------------------------------------------------------------------
# Constructor: boolean sections must not crash or mis-set the flag
# ---------------------------------------------------------------------------


def test_constructor_sso_bool_false_does_not_raise(monkeypatch, tmp_path):
    """sources: {sso: False} must construct without AttributeError."""
    sources_config = {"sso": False}
    manager = _make_manager(monkeypatch, tmp_path, sources_config)
    assert manager.sso_enabled is False


def test_constructor_git_bool_false_sets_disabled(monkeypatch, tmp_path):
    """sources: {git: False} must set git_enabled to False, not True."""
    sources_config = {"git": False}
    manager = _make_manager(monkeypatch, tmp_path, sources_config)
    assert manager.git_enabled is False


def test_schedule_collect_git_skips_when_git_disabled(monkeypatch, tmp_path):
    """schedule_collect_git must not call _collect_git_resources when git is disabled."""
    sources_config = {"git": False}
    manager = _make_manager(monkeypatch, tmp_path, sources_config)

    collect_git_called = []

    def _fake_collect_git_resources(git_urls, persistence, git_dir):
        collect_git_called.append(git_urls)

    monkeypatch.setattr(manager, "_collect_git_resources", _fake_collect_git_resources)

    persistence = _fake_persistence(tmp_path)
    persistence.catalog.get_metadata_by_filter = lambda *a, **kw: [
        ("row1", {"url": "https://x/r.git"})
    ]

    manager.schedule_collect_git(persistence)

    assert (
        not collect_git_called
    ), "_collect_git_resources must not be called when git_enabled is False"
