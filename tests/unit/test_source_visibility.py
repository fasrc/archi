import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.interfaces.chat_app.source_visibility import (
    is_visible,
    visibility_config_key,
    visible_sources,
)


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
        # Older ingests stored the source name itself as `source_type`.
        ({"source_type": "links"}, "links"),
        ({"source_type": "elog"}, "elog"),
        ({"source_type": "jira"}, "jira"),
        ({"source_type": "redmine"}, "redmine"),
    ],
)
def test_visibility_config_key(metadata, expected_key):
    assert visibility_config_key(metadata) == expected_key


def test_a_legacy_source_type_obeys_its_own_flag():
    # Before bfba6303 (2026-01-29) Jira tickets were stored with `source_type`
    # "jira" and no provider marker; the old direct lookup honoured the flag.
    config = {"jira": {"visible": False}, "links": {"visible": False}}
    assert not is_visible({"source_type": "jira"}, config)
    assert not is_visible({"source_type": "links"}, config)
    assert is_visible({"source_type": "redmine"}, {"redmine": {"visible": True}})


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


# --- visible_sources: the /v1 citation filter (issue #769) ---------------------------

_HIDE_JIRA = {"jira": {"visible": False}, "git": {"visible": True}}


def _visibility_doc(**metadata):
    return SimpleNamespace(metadata=metadata)


def test_visible_sources_drops_a_hidden_document_with_its_own_score():
    hidden = _visibility_doc(source_type="ticket", ticket_provider="jira")
    shown = _visibility_doc(source_type="git")

    documents, scores = visible_sources([hidden, shown], [0.9, 0.4], _HIDE_JIRA)

    assert documents == [shown]
    assert scores == [0.4]


def test_visible_sources_leaves_a_missing_score_missing():
    hidden = _visibility_doc(source_type="ticket", ticket_provider="jira")
    first = _visibility_doc(source_type="git")
    second = _visibility_doc(source_type="git")

    documents, scores = visible_sources([hidden, first, second], [0.9, 0.4], _HIDE_JIRA)

    assert documents == [first, second]
    assert scores == [0.4]


def test_visible_sources_drops_a_score_that_has_no_document():
    shown = _visibility_doc(source_type="git")

    documents, scores = visible_sources([shown], [0.9, 0.4], _HIDE_JIRA)

    assert documents == [shown]
    assert scores == [0.9]


@pytest.mark.parametrize("sources_config", [None, MagicMock()])
def test_visible_sources_without_a_config_dict_filters_nothing(sources_config, caplog):
    hidden = _visibility_doc(source_type="ticket", ticket_provider="jira")
    input_documents = [hidden]
    input_scores = [0.9]

    with caplog.at_level(logging.DEBUG):
        documents, scores = visible_sources(
            input_documents, input_scores, sources_config
        )

    assert documents is input_documents
    assert scores is input_scores
    assert caplog.records == []


@pytest.mark.parametrize("document", [SimpleNamespace(metadata=None), object()])
def test_visible_sources_keeps_a_document_without_a_metadata_dict(document):
    documents, scores = visible_sources([document], [0.7], _HIDE_JIRA)

    assert documents == [document]
    assert scores == [0.7]


@pytest.mark.parametrize("empty", [None, []])
def test_visible_sources_with_no_documents_or_scores_returns_empty_lists(empty):
    assert visible_sources(empty, empty, _HIDE_JIRA) == ([], [])
