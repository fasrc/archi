"""A null ``enabled`` flag reads as absent in the scraper (PR #613 review).

``ConfigurationManager._input_list_flag`` treats ``enabled: null`` and a
``null`` source section as absent, so the CLI infers the source from its
input-list entries. The scraper SHALL read both the same way: it collects the
entries instead of logging the source as disabled and dropping them.
"""

import pytest

from src.cli.managers.config_manager import ConfigurationManager
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager

_NULL_SHAPES = [
    pytest.param({"enabled": None}, id="enabled-null"),
    pytest.param(None, id="section-null"),
]


@pytest.mark.parametrize("source_cfg", _NULL_SHAPES)
def test_scraper_reads_null_flag_as_absent(source_cfg):
    assert ScraperManager._input_list_flag(source_cfg) is None


@pytest.mark.parametrize("source_cfg", _NULL_SHAPES)
def test_scraper_and_cli_agree_on_null_flag(source_cfg):
    cli_flag = ConfigurationManager._input_list_flag({"git": source_cfg}, "git")
    assert ScraperManager._input_list_flag(source_cfg) == cli_flag


@pytest.mark.parametrize(
    "source_cfg, expected",
    [
        pytest.param({"enabled": False}, False, id="enabled-false"),
        pytest.param({"enabled": True}, True, id="enabled-true"),
        pytest.param({}, None, id="key-absent"),
        pytest.param(False, False, id="section-bool-false"),
        pytest.param(True, True, id="section-bool-true"),
    ],
)
def test_scraper_keeps_explicit_and_absent_flags(source_cfg, expected):
    assert ScraperManager._input_list_flag(source_cfg) is expected
