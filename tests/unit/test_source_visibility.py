import logging

import pytest

from src.interfaces.chat_app.source_visibility import is_visible, visibility_config_key


@pytest.mark.parametrize(
    "metadata,expected_key",
    [
        ({"source_type": "git"}, "git"),
        ({"source_type": "sso"}, "sso"),
        ({"source_type": "local_files"}, "local_files"),
        ({"source_type": "web"}, "links"),
        ({"source_type": "web", "scraper": "Indico "}, "indico"),
        ({"source_type": "web", "scraper": "elog"}, "elog"),
        ({"source_type": "ticket", "ticket_provider": "jira"}, "jira"),
        ({"source_type": "ticket", "ticket_provider": "redmine"}, "redmine"),
        ({"source_type": "ticket"}, None),
        ({}, None),
        ({"source_type": "uploaded"}, None),
    ],
)
def test_visibility_config_key(metadata, expected_key):
    assert visibility_config_key(metadata) == expected_key


def test_jira_hidden_redmine_visible():
    config = {"jira": {"visible": False}, "redmine": {"visible": True}}
    assert not is_visible({"source_type": "ticket", "ticket_provider": "jira"}, config)
    assert is_visible({"source_type": "ticket", "ticket_provider": "redmine"}, config)


def test_indico_hidden_plain_web_visible():
    config = {"indico": {"visible": False}, "links": {"visible": True}}
    assert not is_visible({"source_type": "web", "scraper": "indico"}, config)
    assert is_visible({"source_type": "web"}, config)


def test_links_hidden():
    config = {"links": {"visible": False}}
    assert not is_visible({"source_type": "web"}, config)


def test_no_source_type_visible_no_error(caplog):
    with caplog.at_level(logging.ERROR):
        result = is_visible({}, {"links": {"visible": True}})
    assert result is True
    assert not any(r.levelno == logging.ERROR for r in caplog.records)


def test_mapped_key_missing_from_config_logs_warning(caplog):
    with caplog.at_level(logging.WARNING):
        result = is_visible({"source_type": "git"}, {})
    assert result is True
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_non_dict_entry_visible_no_raise():
    config = {"links": None}
    result = is_visible({"source_type": "web"}, config)
    assert result is True
