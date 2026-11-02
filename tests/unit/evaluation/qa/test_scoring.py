from src.evaluation.qa.preparation import PreparationRecord
from src.evaluation.qa.scoring import _report_header, build_summary, score_attempt
from src.evaluation.qa.validation import Atom, Judgment


def _judgment(atom_id, outcome):
    return Judgment(
        atom_id=atom_id,
        outcome=outcome,
        rationale="reason",
    )


class TestAttemptScoring:
    def test_required_gate_and_negative_floor(self):
        gold = [
            Atom(id="required", text="required", required=True),
            Atom(id="optional", text="optional", required=False),
        ]

        result = score_attempt(
            gold,
            [
                _judgment("required", "not_mentioned"),
                _judgment("optional", "contradicted"),
            ],
        )

        assert result == {
            "atom_score": 0.0,
            "required_atom_recall": 0.0,
            "passed": False,
        }

    def test_optional_omission_can_pass(self):
        gold = [
            Atom(id="required", text="required", required=True),
            Atom(id="optional", text="optional", required=False),
        ]

        result = score_attempt(
            gold,
            [_judgment("required", "entailed"), _judgment("optional", "not_mentioned")],
        )

        assert result["atom_score"] == 0.5
        assert result["required_atom_recall"] == 1.0
        assert result["passed"] is True


class TestSummaryScoring:
    def test_execution_failures_count_and_evaluation_failures_are_excluded(self):
        gold = Atom(id="g1", text="gold", required=True)
        preparation = [
            PreparationRecord(
                item_id="item",
                status="prepared",
                question="question",
                answer="answer",
                time_sensitive=False,
                gold_atoms=(gold,),
                atom_source="inferred",
            )
        ]
        results = [
            {
                "item_id": "item",
                "status": "scored",
                "passed": True,
                "atom_score": 1.0,
                "required_atom_recall": 1.0,
                "judgments": [{"atom_id": "g1", "outcome": "entailed"}],
            },
            {"item_id": "item", "status": "execution_failed"},
            {"item_id": "item", "status": "evaluation_failed"},
        ]

        summary = build_summary(preparation, results)

        assert summary["item_lifecycle_counts"] == {
            "skipped_time_sensitive": 0,
            "preparation_failed": 0,
            "prepared": 1,
            "skipped_live": 0,
        }
        assert summary["attempt_lifecycle_counts"] == {
            "execution_failed": 1,
            "evaluation_failed": 1,
            "scored": 1,
            "live_validation_failed": 0,
        }
        assert summary["quality_accounted_attempts"] == 2
        assert summary["overall_attempt_pass_rate"] == 0.5
        assert summary["items"][0]["k"] == 2
        assert summary["items"][0]["item_pass_rate"] == 0.5
        assert summary["items"][0]["gold_atom_pass_rates"] == [
            {"atom_id": "g1", "atom_pass_count": 1, "k": 2, "atom_pass_rate": 0.5}
        ]

    def test_scored_items_counts_items_with_at_least_one_scored_attempt(self):
        gold = Atom(id="g1", text="gold", required=True)
        preparation = [
            PreparationRecord(
                item_id="ok1",
                status="prepared",
                question="question",
                answer="answer",
                time_sensitive=False,
                gold_atoms=(gold,),
                atom_source="inferred",
            ),
            PreparationRecord(
                item_id="ok2",
                status="prepared",
                question="question",
                answer="answer",
                time_sensitive=False,
                gold_atoms=(gold,),
                atom_source="inferred",
            ),
            PreparationRecord(
                item_id="exec-only",
                status="prepared",
                question="question",
                answer="answer",
                time_sensitive=False,
                gold_atoms=(gold,),
                atom_source="inferred",
            ),
            PreparationRecord(
                item_id="failed",
                status="preparation_failed",
                error="boom",
            ),
        ]
        results = [
            {
                "item_id": "ok1",
                "status": "scored",
                "passed": True,
                "atom_score": 1.0,
                "required_atom_recall": 1.0,
                "judgments": [{"atom_id": "g1", "outcome": "entailed"}],
            },
            {
                "item_id": "ok2",
                "status": "scored",
                "passed": True,
                "atom_score": 1.0,
                "required_atom_recall": 1.0,
                "judgments": [{"atom_id": "g1", "outcome": "entailed"}],
            },
            {"item_id": "exec-only", "status": "execution_failed"},
        ]

        summary = build_summary(preparation, results, items_total=4)

        assert summary["items_total"] == 4
        assert summary["scored_items"] == 2

        summary_without_total = build_summary(preparation, results)

        assert summary_without_total["items_total"] is None
        assert summary_without_total["scored_items"] == 2


class TestReportHeader:
    _SUMMARY = {
        "overall_attempt_pass_rate": 1.0,
        "passed_attempts": 2,
        "quality_accounted_attempts": 2,
        "macro_mean_item_pass_rate": 1.0,
        "macro_mean_scored_attempt_atom_score": 1.0,
        "macro_mean_scored_attempt_required_atom_recall": 1.0,
        "item_lifecycle_counts": {"prepared": 2},
        "attempt_lifecycle_counts": {"scored": 2},
        "item_macro_exclusion_count": 0,
        "items_total": 3,
        "scored_items": 2,
    }
    _MANIFEST = {"status": "scored", "attempts": 1}

    def test_prints_scored_items_against_items_total(self):
        header = _report_header(self._SUMMARY, self._MANIFEST)

        assert "- Scored items: `2` / items: `3`" in header

    def test_missing_keys_render_unavailable_without_raising(self):
        summary = {
            key: value
            for key, value in self._SUMMARY.items()
            if key not in ("items_total", "scored_items")
        }

        header = _report_header(summary, self._MANIFEST)

        assert "- Scored items: `unavailable` / items: `unavailable`" in header
