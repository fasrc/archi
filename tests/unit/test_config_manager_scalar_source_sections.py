"""`set_sources_enabled` must accept scalar source sections (fasrc/archi#460).

`data_manager.sources.<name>: true|false|null` is a valid config spelling, and
`elog` is now a managed source. The setter must normalize the scalar to the
mapping form rather than raise `TypeError` during `archi create`.
"""

import pytest

from src.cli.managers.config_manager import ConfigurationManager


def _manager(sources):
    mgr = object.__new__(ConfigurationManager)
    mgr.configs = [{"name": "test", "data_manager": {"sources": dict(sources)}}]
    return mgr


def _sources(mgr):
    return mgr.configs[0]["data_manager"]["sources"]


@pytest.mark.parametrize("name", ["elog", "git"])
def test_bool_false_section_stays_disabled(name):
    mgr = _manager({name: False})
    mgr.set_sources_enabled([])
    assert _sources(mgr)[name] == {"enabled": False}


@pytest.mark.parametrize("name", ["elog", "git"])
def test_bool_true_section_stays_enabled(name):
    mgr = _manager({name: True})
    mgr.set_sources_enabled([name])
    assert _sources(mgr)[name] == {"enabled": True}


def test_bool_true_section_keeps_its_explicit_true_when_not_selected():
    mgr = _manager({"elog": True})
    mgr.set_sources_enabled([])
    assert _sources(mgr)["elog"] == {"enabled": True}


def test_null_section_is_treated_as_absent():
    mgr = _manager({"elog": None})
    mgr.set_sources_enabled([])
    assert _sources(mgr)["elog"] == {"enabled": False}
    mgr = _manager({"elog": None})
    mgr.set_sources_enabled(["elog"])
    assert _sources(mgr)["elog"] == {"enabled": True}
