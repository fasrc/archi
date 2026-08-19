"""Unit tests for the ``final`` stream-event assembly helper."""

import time

from src.interfaces.chat_app.final_event import build_final_event

_EXPECTED_KEYS_NO_ANSWER = {
    "type",
    "response",
    "conversation_id",
    "archi_msg_id",
    "message_id",
    "user_message_id",
    "trace_id",
    "server_response_msg_ts",
    "final_response_msg_ts",
    "usage",
    "model",
    "model_used",
    "source_documents",
    "retriever_scores",
}


def _build(**overrides):
    """A minimal, valid set of build_final_event() kwargs, with overrides applied."""
    kwargs = dict(
        last_output={"answer": "bare answer"},
        response="response with sources appended",
        conversation_id="conv-1",
        message_ids=[7, 8],
        trace_id="trace-1",
        server_response_msg_ts=1234.5,
        usage={"total_tokens": 10},
        model="requested-model",
        model_used="reported-model",
        source_documents=["doc1"],
        retriever_scores=[0.9],
    )
    kwargs.update(overrides)
    return build_final_event(**kwargs)


def test_answer_passthrough_non_empty():
    event = _build(last_output={"answer": "bare answer"})
    assert event["answer"] == "bare answer"


def test_answer_passthrough_empty_string_key_present():
    event = _build(last_output={"answer": ""})
    assert "answer" in event
    assert event["answer"] == ""


def test_answer_omitted_when_last_output_has_no_answer_key():
    event = _build(last_output={})
    assert "answer" not in event


def test_answer_omitted_when_last_output_is_none():
    event = _build(last_output=None)
    assert "answer" not in event


def test_answer_omitted_when_answer_is_none():
    event = _build(last_output={"answer": None})
    assert "answer" not in event


def test_response_passes_through_untouched():
    event = _build(
        response="text with sources appended",
        last_output={"answer": "a different bare answer"},
    )
    assert event["response"] == "text with sources appended"


def test_field_parity_with_multiple_message_ids():
    event = _build(message_ids=[7, 8], last_output={"answer": "bare answer"})
    assert set(event.keys()) == _EXPECTED_KEYS_NO_ANSWER | {"answer"}
    assert event["type"] == "final"
    assert event["archi_msg_id"] == 8
    assert event["message_id"] == 8
    assert event["user_message_id"] == 7


def test_field_parity_with_empty_message_ids():
    event = _build(message_ids=[], last_output=None)
    assert set(event.keys()) == _EXPECTED_KEYS_NO_ANSWER
    assert event["archi_msg_id"] is None
    assert event["message_id"] is None
    assert event["user_message_id"] is None


def test_field_parity_with_single_message_id():
    event = _build(message_ids=[5], last_output=None)
    assert event["archi_msg_id"] == 5
    assert event["message_id"] == 5
    assert event["user_message_id"] is None


def test_server_response_msg_ts_passes_through_unchanged():
    event = _build(server_response_msg_ts=9999.25)
    assert event["server_response_msg_ts"] == 9999.25


def test_final_response_msg_ts_is_close_to_now():
    before = time.time()
    event = _build()
    after = time.time()
    assert isinstance(event["final_response_msg_ts"], float)
    assert before <= event["final_response_msg_ts"] <= after
