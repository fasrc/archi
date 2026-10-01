import copy
from pathlib import Path

from src.evaluation.qa.console import EvaluationConsoleService
from src.evaluation.qa.run_visibility import (
    VIEWER_HIDDEN_FIELDS,
    VIEWER_HIDDEN_JUDGMENT_FIELDS,
    redact_run_for_viewer,
)


def _full_payload():
    return {
        "id": "history-id",
        "manifest": {"schema_version": 3, "status": "completed"},
        "metadata": {"name": "Run"},
        "capabilities": {"retry_failed": True},
        "summary": {"score": 0.9},
        "report_available": True,
        "cancellation": None,
        "preparation": [
            {
                "item_id": "item-1",
                "status": "prepared",
                "question": "Q1",
                "category": "fact",
                "answer": "the approved answer",
                "answer_sha256": "deadbeef",
                "gold_atoms": [{"id": "A1", "text": "atom"}],
                "oracle": {"source": "manual"},
                "oracle_metadata": {"model": "oracle-1"},
                "oracle_calls": [{"tool": "search"}],
            }
        ],
        "prepared_items": [
            {
                "item_id": "item-1",
                "status": "prepared",
                "question": "Q1",
                "category": "fact",
                "answer": "the approved answer",
                "answer_sha256": "deadbeef",
                "gold_atoms": [{"id": "A1", "text": "atom"}],
                "oracle": {"source": "manual"},
                "oracle_metadata": {"model": "oracle-1"},
                "oracle_calls": [{"tool": "search"}],
            }
        ],
        "live_checks": [
            {
                "item_id": "item-1",
                "answer": "live answer",
                "answer_sha256": "cafebabe",
                "metadata": {"checked_at": "now"},
                "calls": [{"tool": "search"}],
            }
        ],
        "answers": [
            {
                "item_id": "item-1",
                "answer": "attempted answer",
                "duration_ms": 123,
                "tool_calls": [{"tool": "search"}],
            }
        ],
        "evaluation_results": [
            {
                "item_id": "item-1",
                "answer": "attempted answer",
                "judgments": [
                    {
                        "atom_id": "A1",
                        "outcome": "pass",
                        "rationale": "matches gold atom text",
                    }
                ],
            }
        ],
    }


def test_redact_run_for_viewer_hides_every_configured_field():
    payload = _full_payload()
    before = copy.deepcopy(payload)

    redacted = redact_run_for_viewer(payload)

    for section, fields in VIEWER_HIDDEN_FIELDS.items():
        for row in redacted.get(section, []):
            for field in fields:
                assert field not in row

    for result in redacted.get("evaluation_results", []):
        for judgment in result.get("judgments", []):
            for field in VIEWER_HIDDEN_JUDGMENT_FIELDS:
                assert field not in judgment

    assert redacted["id"] == "history-id"
    assert redacted["manifest"] == {"schema_version": 3, "status": "completed"}
    assert redacted["metadata"] == {"name": "Run"}
    assert redacted["capabilities"] == {"retry_failed": True}
    assert redacted["summary"] == {"score": 0.9}
    assert redacted["report_available"] is True
    assert redacted["cancellation"] is None

    for section in ("preparation", "prepared_items", "live_checks"):
        row = redacted[section][0]
        assert row["item_id"] == "item-1"
        assert row.get("question") == "Q1" or "question" not in row
        assert row.get("status") == "prepared" or "status" not in row

    assert redacted["answers"][0]["item_id"] == "item-1"
    assert redacted["answers"][0]["duration_ms"] == 123
    assert redacted["answers"][0]["tool_calls"] == [{"tool": "search"}]

    assert redacted["evaluation_results"][0]["item_id"] == "item-1"
    assert redacted["evaluation_results"][0]["judgments"][0]["atom_id"] == "A1"
    assert redacted["evaluation_results"][0]["judgments"][0]["outcome"] == "pass"

    assert payload == before


def test_redact_run_for_viewer_tolerates_missing_and_malformed_sections():
    payload = {
        "id": "history-id",
        "manifest": {"status": "canceled"},
        "metadata": {},
        "capabilities": {"retry_failed": False},
        "cancellation": {"canceled_at": "now"},
        "prepared_items": [],
        "answers": [],
        "evaluation_results": [
            {
                "item_id": "item-1",
                "answer": "attempted answer",
                "judgments": "not-a-list",
            },
            "not-a-dict-row",
        ],
        "report_available": False,
    }

    redacted = redact_run_for_viewer(payload)

    assert redacted["prepared_items"] == []
    assert redacted["answers"] == []
    assert "live_checks" not in redacted
    assert redacted["evaluation_results"][0]["judgments"] == "not-a-list"
    assert redacted["evaluation_results"][1] == "not-a-dict-row"


def test_viewer_hidden_fields_are_named_literally():
    assert VIEWER_HIDDEN_FIELDS == {
        "preparation": (
            "answer",
            "answer_sha256",
            "gold_atoms",
            "oracle",
            "oracle_metadata",
            "oracle_calls",
        ),
        "prepared_items": (
            "answer",
            "answer_sha256",
            "gold_atoms",
            "oracle",
            "oracle_metadata",
            "oracle_calls",
        ),
        "live_checks": ("answer", "answer_sha256", "metadata", "calls"),
        "answers": ("answer",),
        "evaluation_results": ("answer",),
    }
    assert VIEWER_HIDDEN_JUDGMENT_FIELDS == ("rationale",)


class _StubHistory:
    def __init__(self, payload):
        self._payload = payload

    def get_run(self, history_id):
        return self._payload


def test_console_service_get_run_defaults_to_redacted(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("services: {}\n")
    service = EvaluationConsoleService(
        tmp_path / "console",
        agent_config_path=config_path,
        agents_dir=agents_dir,
    )
    full_payload = _full_payload()
    service.history = _StubHistory(full_payload)

    redacted = service.get_run("h")
    full = service.get_run("h", include_hidden=True)

    assert redacted == redact_run_for_viewer(full_payload)
    assert full == full_payload


def test_resolved_agent_config_is_never_served():
    history_source = Path("src/evaluation/qa/history.py").read_text(encoding="utf-8")
    routes_source = Path("src/interfaces/chat_app/evaluation_routes.py").read_text(
        encoding="utf-8"
    )

    assert "agent_config.resolved.yaml" not in history_source
    assert "agent_config.resolved.yaml" not in routes_source
