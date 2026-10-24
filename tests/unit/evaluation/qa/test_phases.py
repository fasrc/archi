from collections import Counter

from src.evaluation.qa.phases import (
    execute_attempts,
    run_attempt,
    score_answer,
    score_attempts,
)
from src.evaluation.qa.preparation import PreparationRecord
from src.evaluation.qa.validation import Atom

SHA = "a" * 64


def _prepared(item_id):
    return PreparationRecord(
        item_id=item_id,
        status="prepared",
        question=f"Question {item_id}",
        answer="Gold",
        time_sensitive=False,
        gold_atoms=(Atom(id="A1", text="Gold", required=True),),
        atom_source="supplied",
    )


def _identity(item_id, ordinal=1):
    return {
        "item_id": item_id,
        "attempt_id": f"{item_id}-attempt-{ordinal}",
        "ordinal": ordinal,
        "agent_config_sha256": SHA,
        "agent_spec_sha256": SHA,
    }


def test_execute_attempts_preserves_task_order_with_worker_local_runtimes():
    constructed = Counter()

    class Runtime:
        tool_calls = []

        def __init__(self):
            constructed["runtimes"] += 1

        def run(self, question):
            return f"answer:{question}"

    tasks = [(_prepared(item_id), _identity(item_id)) for item_id in ("a", "b", "c")]

    answers = list(
        execute_attempts(
            tasks,
            Runtime,
            2,
            thread_name_prefix="test-execution",
        )
    )

    assert [row["item_id"] for row in answers] == ["a", "b", "c"]
    assert [row["answer"] for row in answers] == [
        "answer:Question a",
        "answer:Question b",
        "answer:Question c",
    ]
    assert 1 <= constructed["runtimes"] <= 2


def test_execute_attempts_does_not_construct_runtime_for_empty_retry_batch():
    def unexpected_runtime():
        raise AssertionError("runtime must not be constructed")

    assert (
        list(
            execute_attempts(
                [],
                unexpected_runtime,
                1,
                thread_name_prefix="test-empty-execution",
            )
        )
        == []
    )


def test_score_attempts_does_not_construct_evaluator_for_execution_failures():
    prepared = _prepared("item")
    failed_answer = {
        **_identity("item"),
        "status": "execution_failed",
        "duration_ms": 10,
        "tool_calls": [],
        "error": {"type": "RuntimeError", "message": "failure"},
    }

    def unexpected_evaluator():
        raise AssertionError("evaluator must not be constructed")

    assert list(
        score_attempts(
            [(prepared, failed_answer)],
            unexpected_evaluator,
            2,
            thread_name_prefix="test-scoring",
        )
    ) == [
        {
            **_identity("item"),
            "status": "execution_failed",
            "error": {"type": "RuntimeError", "message": "failure"},
        }
    ]


# --- #582: usage keys on run and score rows ---


def test_run_attempt_writes_usage_on_answer_ready_row():
    _usage = {
        "input_tokens": 8,
        "output_tokens": 3,
        "calls": 1,
        "unreported_calls": 0,
        "by_model": [],
    }

    class Runtime:
        tool_calls = []
        usage = None

        def run(self, question):
            self.usage = _usage
            return "answer text"

    row = run_attempt(Runtime(), _prepared("item"), _identity("item"))

    assert row["status"] == "answer_ready"
    assert row["usage"] == _usage


def test_run_attempt_writes_usage_on_execution_failed_row():
    _usage = {
        "input_tokens": 4,
        "output_tokens": 1,
        "calls": 1,
        "unreported_calls": 0,
        "by_model": [],
    }

    class Runtime:
        tool_calls = []
        usage = None

        def run(self, question):
            self.usage = _usage
            raise RuntimeError("agent error")

    row = run_attempt(Runtime(), _prepared("item"), _identity("item"))

    assert row["status"] == "execution_failed"
    assert row["usage"] == _usage


def test_run_attempt_writes_none_usage_for_runtime_without_usage_attribute():
    class Runtime:
        tool_calls = []

        def run(self, question):
            return "answer text"

    row = run_attempt(Runtime(), _prepared("item"), _identity("item"))

    assert row["usage"] is None


def test_score_answer_writes_evaluator_usage_on_scored_row():
    _usage = {
        "input_tokens": 10,
        "output_tokens": 4,
        "calls": 1,
        "unreported_calls": 0,
        "by_model": [],
    }

    class FakeEvaluator:
        last_usage = None

        def compare(self, question, gold_atoms, answer):
            self.last_usage = _usage
            return {
                "judgments": [
                    {"atom_id": "A1", "outcome": "entailed", "rationale": "match"}
                ]
            }

    answer_row = {
        **_identity("item"),
        "status": "answer_ready",
        "duration_ms": 10,
        "tool_calls": [],
        "answer": "Gold",
    }

    row = score_answer(_prepared("item"), answer_row, FakeEvaluator())

    assert row["status"] == "scored"
    assert row["usage"] == _usage


def test_score_answer_writes_evaluator_usage_on_evaluation_failed_row():
    _usage = {
        "input_tokens": 5,
        "output_tokens": 2,
        "calls": 1,
        "unreported_calls": 0,
        "by_model": [],
    }

    class FakeEvaluator:
        last_usage = None

        def compare(self, question, gold_atoms, answer):
            self.last_usage = _usage
            raise RuntimeError("judge failed")

    answer_row = {
        **_identity("item"),
        "status": "answer_ready",
        "duration_ms": 10,
        "tool_calls": [],
        "answer": "Gold",
    }

    row = score_answer(_prepared("item"), answer_row, FakeEvaluator())

    assert row["status"] == "evaluation_failed"
    assert row["usage"] == _usage


def test_score_attempts_execution_failed_passthrough_has_no_usage_key():
    """execution_failed pass-through in score_attempts has no usage key."""
    prepared = _prepared("item")
    failed_answer = {
        **_identity("item"),
        "status": "execution_failed",
        "duration_ms": 10,
        "tool_calls": [],
        "usage": {
            "input_tokens": 5,
            "output_tokens": 1,
            "calls": 1,
            "unreported_calls": 0,
            "by_model": [],
        },
        "error": {"type": "RuntimeError", "message": "failure"},
    }

    def unexpected_evaluator():
        raise AssertionError("evaluator must not be constructed")

    results = list(
        score_attempts(
            [(prepared, failed_answer)],
            unexpected_evaluator,
            1,
            thread_name_prefix="test-scoring-passthrough",
        )
    )

    assert results[0]["status"] == "execution_failed"
    assert "usage" not in results[0]
