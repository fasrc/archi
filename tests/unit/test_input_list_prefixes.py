"""Tests for shared input-list prefix definitions in source_registry (task 1.1)."""

import tempfile
from pathlib import Path

import pytest

from src.cli import source_registry as sr
from src.cli.source_registry import (
    INPUT_LIST_PREFIXES,
    is_elog_url,
    read_input_list_entries,
    source_registry,
    split_prefixed_entry,
)
from src.cli.tools import sources_builder


def test_input_list_prefixes_exact():
    assert INPUT_LIST_PREFIXES == {
        "git-": "git",
        "sso-": "sso",
        "elog-": "elog",
        "indico-": "indico",
    }


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("git-https://x/r.git", ("git", "https://x/r.git")),
        ("sso-https://x/r.git", ("sso", "https://x/r.git")),
        ("elog-https://x/r.git", ("elog", "https://x/r.git")),
        ("indico-https://x/r.git", ("indico", "https://x/r.git")),
        ("https://x/", None),
        ("sitemap-https://x/s.xml", None),
    ],
)
def test_split_prefixed_entry(entry, expected):
    assert split_prefixed_entry(entry) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://h/elog/a", True),
        ("https://h/elogs/a", True),
        ("https://h/blog/", False),
    ],
)
def test_is_elog_url(url, expected):
    assert is_elog_url(url) is expected


def test_read_input_list_entries_filters(tmp_path):
    list_file = tmp_path / "test.list"
    list_file.write_text(
        "# comment\n"
        "\n"
        "https://a.example.com\n"
        "https://b.example.com,2\n"
        "  https://c.example.com  \n"
    )
    result = read_input_list_entries(list_file)
    assert result == [
        "https://a.example.com",
        "https://b.example.com",
        "https://c.example.com",
    ]


def test_elog_in_source_registry():
    assert "elog" in source_registry.names()
    elog_def = source_registry.get("elog")
    assert elog_def.required_secrets == []
    assert elog_def.depends_on == ["links"]


def test_sources_builder_extra_prefixes():
    assert sources_builder._EXTRA_PREFIXES == tuple(INPUT_LIST_PREFIXES)


def test_goldenset_sso_prefix_and_fanout_prefixes():
    from src.utils import goldenset_maintenance

    assert goldenset_maintenance.SSO_PREFIX == "sso-"
    assert goldenset_maintenance.FANOUT_PREFIXES == ("git-", "elog-", "indico-")
