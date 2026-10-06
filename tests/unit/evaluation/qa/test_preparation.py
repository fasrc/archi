# isort: off
import pytest

from src.evaluation.qa.artifacts import write_jsonl
from src.evaluation.qa.preparation import (
    PreparationRecord,
    iter_preparation_records,
    load_preparation_records,
    prepare_dataset_item,
    prepare_dataset_items,
    preparation_record_from_dict,
)
from src.evaluation.qa.validation import Atom, DatasetItem

# isort: on


def _item(
    item_id,
    *,
    time_sensitive=False,
    expected_atoms=None,
):
    return DatasetItem(
        id=item_id,
        question=f"Question {item_id}",
        answer=f"Answer {item_id}",
        time_sensitive=time_sensitive,
        category="category",
        answer_mode="direct_answer",
        answer_source="source",
        expected_atoms=expected_atoms,
    )


class _Extractor:
    def extract_gold(self, question, answer):
        if answer.endswith("failed"):
            raise RuntimeError("extraction failed")
        return {
            "atoms": [
                {
                    "id": "A1",
                    "text": answer,
                    "required": True,
                }
            ]
        }


def _record_fields(item, *, prepared=False):
    fields = {
        "item_id": item.id,
        "category": item.category,
        "answer_mode": item.answer_mode,
        "answer_source": item.answer_source,
    }
    if prepared:
        fields.update(
            {
                "question": item.question,
                "answer": item.answer,
                "time_sensitive": False,
            }
        )
    return fields


class TestPreparationRecord:
    @pytest.mark.parametrize(
        "record, message",
        [
            (
                lambda: PreparationRecord(
                    **_record_fields(_item("prepared"), prepared=True),
                    status="prepared",
                    gold_atoms=None,
                    atom_source="inferred",
                ),
                "requires gold atoms",
            ),
            (
                lambda: PreparationRecord(
                    **_record_fields(_item("failed")),
                    status="preparation_failed",
                ),
                "requires an error",
            ),
            (
                lambda: PreparationRecord(
                    **_record_fields(_item("skipped", time_sensitive=True)),
                    status="skipped_time_sensitive",
                    error="unexpected",
                ),
                "cannot contain output",
            ),
            (
                lambda: PreparationRecord(
                    **_record_fields(_item("unknown", time_sensitive=True)),
                    status="unknown",
                ),
                "unsupported preparation status",
            ),
            (
                lambda: PreparationRecord(
                    **_record_fields(_item("prepared"), prepared=True),
                    status="prepared",
                    gold_atoms=(Atom(id="A1", text="answer", required=True),),
                    atom_source="unknown",
                ),
                "requires an atom source",
            ),
            (
                lambda: PreparationRecord(
                    **_record_fields(_item("prepared"), prepared=True),
                    status="prepared",
                    gold_atoms=(Atom(id="A1", text="answer", required=True),),
                    atom_source="inferred",
                    error="unexpected",
                ),
                "cannot contain an error",
            ),
            (
                lambda: PreparationRecord(
                    **_record_fields(_item("failed")),
                    status="preparation_failed",
                    question="unexpected",
                    error="unexpected",
                ),
                "cannot contain prepared output",
            ),
        ],
    )
    def test_rejects_status_field_mismatches(self, record, message):
        with pytest.raises(ValueError, match=message):
            record()

    def test_prepares_one_terminal_record_per_input(self):
        supplied_atom = Atom(id="S1", text="supplied", required=True)
        items = [
            _item("supplied", expected_atoms=[supplied_atom]),
            _item("inferred"),
            _item("skipped", time_sensitive=True),
            _item("failed"),
        ]

        records = prepare_dataset_items(items, _Extractor())

        assert [record.status for record in records] == [
            "prepared",
            "prepared",
            "skipped_time_sensitive",
            "preparation_failed",
        ]
        assert records[0].atom_source == "supplied"
        assert records[0].prepared_gold_atoms == (supplied_atom,)
        assert records[1].atom_source == "inferred"
        assert records[2].to_dict() == {
            "item_id": "skipped",
            "status": "skipped_time_sensitive",
            "category": "category",
            "answer_mode": "direct_answer",
            "answer_source": "source",
        }
        assert records[3].error == "extraction failed"

    def test_prepares_one_item_without_accumulating_a_collection(self):
        record = prepare_dataset_item(_item("inferred"), _Extractor())

        assert record.status == "prepared"
        assert record.item_id == "inferred"
        assert record.atom_source == "inferred"


class TestPreparationArtifact:
    def test_round_trips_records_without_loading_the_input_snapshot(self, tmp_path):
        supplied_atom = Atom(id="S1", text="supplied", required=True)
        items = [
            _item("supplied", expected_atoms=[supplied_atom]),
            _item("skipped", time_sensitive=True),
        ]
        records = prepare_dataset_items(items, _Extractor())
        path = tmp_path / "preparation.jsonl"
        write_jsonl(path, (record.to_dict() for record in records))

        loaded = load_preparation_records(path, expected_count=2)

        assert loaded == records

    def test_rejects_duplicate_item_records(self, tmp_path):
        item = _item("item", time_sensitive=True)
        row = PreparationRecord(
            **_record_fields(item),
            status="skipped_time_sensitive",
        ).to_dict()
        path = tmp_path / "preparation.jsonl"
        write_jsonl(path, [row, row])

        with pytest.raises(ValueError, match="duplicate item IDs"):
            load_preparation_records(path, expected_count=2)

    def test_rejects_fields_inconsistent_with_status(self, tmp_path):
        item = _item("item", time_sensitive=True)
        row = PreparationRecord(
            **_record_fields(item),
            status="skipped_time_sensitive",
        ).to_dict()
        row["error"] = "not allowed"
        path = tmp_path / "preparation.jsonl"
        write_jsonl(path, [row])

        with pytest.raises(ValueError, match="invalid fields"):
            load_preparation_records(path, expected_count=1)

    def test_rejects_non_normalized_prepared_text(self, tmp_path):
        record = prepare_dataset_item(_item("item"), _Extractor())
        row = record.to_dict()
        row["question"] = "Question\r\nitem"
        path = tmp_path / "preparation.jsonl"
        write_jsonl(path, [row])

        with pytest.raises(ValueError, match="normalized newlines"):
            load_preparation_records(path, expected_count=1)

    def test_rejects_record_count_that_disagrees_with_manifest(self, tmp_path):
        item = _item("item", time_sensitive=True)
        path = tmp_path / "preparation.jsonl"
        write_jsonl(
            path,
            [
                PreparationRecord(
                    **_record_fields(item),
                    status="skipped_time_sensitive",
                ).to_dict()
            ],
        )

        with pytest.raises(ValueError, match="exactly one row per input item"):
            load_preparation_records(path, expected_count=2)

        with pytest.raises(ValueError, match="exactly one row per input item"):
            list(iter_preparation_records(path, expected_count=2))

    def test_preserves_authoritative_artifact_order(self, tmp_path):
        first = _item("first", time_sensitive=True)
        second = _item("second", time_sensitive=True)
        path = tmp_path / "preparation.jsonl"
        write_jsonl(
            path,
            [
                PreparationRecord(
                    **_record_fields(second),
                    status="skipped_time_sensitive",
                ).to_dict(),
                PreparationRecord(
                    **_record_fields(first),
                    status="skipped_time_sensitive",
                ).to_dict(),
            ],
        )

        records = load_preparation_records(path, expected_count=2)

        assert [record.item_id for record in records] == ["second", "first"]

    def test_iterates_records_lazily(self, tmp_path):
        first = prepare_dataset_item(_item("first"), _Extractor())
        second = prepare_dataset_item(_item("second"), _Extractor())
        path = tmp_path / "preparation.jsonl"
        write_jsonl(path, [first.to_dict(), second.to_dict()])

        records = iter_preparation_records(path)

        assert next(records) == first
        assert next(records) == second
        with pytest.raises(StopIteration):
            next(records)


_SAMPLE_USAGE = {
    "input_tokens": 100,
    "output_tokens": 20,
    "calls": 1,
    "unreported_calls": 0,
    "by_model": [
        {
            "provider": "test",
            "model": "m",
            "input_tokens": 100,
            "output_tokens": 20,
            "calls": 1,
            "unreported_calls": 0,
        }
    ],
}


class _ExtractorWithLastUsage:
    last_usage = _SAMPLE_USAGE

    def extract_gold(self, question, answer):
        return {"atoms": [{"id": "A1", "text": answer, "required": True}]}


class _FailingExtractorWithLastUsage:
    last_usage = _SAMPLE_USAGE

    def extract_gold(self, question, answer):
        raise RuntimeError("extraction failed")


class _FailingExtractorWithoutUsage:
    # A recorder-backed extractor whose call failed before on_llm_end.
    last_usage = None

    def extract_gold(self, question, answer):
        raise RuntimeError("auth failed")


def _prepared_row(item_id="item"):
    return {
        "item_id": item_id,
        "status": "prepared",
        "category": "category",
        "answer_mode": "direct_answer",
        "answer_source": "source",
        "question": f"Question {item_id}",
        "answer": f"Answer {item_id}",
        "time_sensitive": False,
        "atom_source": "inferred",
        "gold_atoms": [{"id": "A1", "text": f"Answer {item_id}", "required": True}],
    }


def _failed_row(item_id="item"):
    return {
        "item_id": item_id,
        "status": "preparation_failed",
        "category": "category",
        "answer_mode": "direct_answer",
        "answer_source": "source",
        "error": "extraction failed",
    }


class TestPreparationUsage:
    def test_inferred_usage_written_to_dict(self):
        record = prepare_dataset_item(_item("u1"), _ExtractorWithLastUsage())

        assert record.atom_source == "inferred"
        assert record.usage == _SAMPLE_USAGE
        row = record.to_dict()
        assert row["usage"] == _SAMPLE_USAGE

    def test_extractor_raise_writes_usage_on_failed_row(self):
        record = prepare_dataset_item(_item("u2"), _FailingExtractorWithLastUsage())

        assert record.status == "preparation_failed"
        assert record.usage == _SAMPLE_USAGE
        row = record.to_dict()
        assert row["usage"] == _SAMPLE_USAGE

    def test_failed_call_without_usage_writes_null_usage(self):
        record = prepare_dataset_item(_item("u11"), _FailingExtractorWithoutUsage())

        assert record.status == "preparation_failed"
        assert record.usage is None
        row = record.to_dict()
        assert "usage" in row
        assert row["usage"] is None

    def test_failed_row_round_trips_null_usage(self):
        row = _failed_row("u12")
        row["usage"] = None

        loaded = preparation_record_from_dict(row)

        assert "usage" in loaded.to_dict()
        assert loaded.to_dict()["usage"] is None

    def test_failed_row_without_usage_key_stays_without(self):
        loaded = preparation_record_from_dict(_failed_row("u13"))

        assert "usage" not in loaded.to_dict()

    def test_skipped_with_usage_key_raises_in_post_init(self):
        with pytest.raises(ValueError, match="cannot contain output"):
            PreparationRecord(
                item_id="u14",
                status="skipped_live",
                category="category",
                answer_mode="direct_answer",
                answer_source="source",
                usage_recorded=True,
            )

    def test_supplied_atoms_row_has_no_usage_key(self):
        supplied_atom = Atom(id="S1", text="supplied", required=True)
        item = _item("u3", expected_atoms=[supplied_atom])
        record = prepare_dataset_item(item, _ExtractorWithLastUsage())

        assert record.atom_source == "supplied"
        assert record.usage is None
        row = record.to_dict()
        assert "usage" not in row

    def test_extractor_without_last_usage_attr_no_usage_key(self):
        record = prepare_dataset_item(_item("u4"), _Extractor())

        assert record.atom_source == "inferred"
        assert record.usage is None
        row = record.to_dict()
        assert "usage" not in row

    def test_record_from_row_round_trips_usage_prepared(self):
        row = _prepared_row("u5")
        row["usage"] = _SAMPLE_USAGE

        loaded = preparation_record_from_dict(row)

        assert loaded.usage == _SAMPLE_USAGE
        assert loaded.to_dict()["usage"] == _SAMPLE_USAGE

    def test_record_from_row_round_trips_usage_failed(self):
        row = _failed_row("u6")
        row["usage"] = _SAMPLE_USAGE

        loaded = preparation_record_from_dict(row)

        assert loaded.usage == _SAMPLE_USAGE
        assert loaded.to_dict()["usage"] == _SAMPLE_USAGE

    def test_record_from_row_round_trips_null_usage(self):
        row = _prepared_row("u7")
        row["usage"] = None

        loaded = preparation_record_from_dict(row)

        assert loaded.usage is None

    def test_record_from_row_loads_row_without_usage(self):
        row = _prepared_row("u8")

        loaded = preparation_record_from_dict(row)

        assert loaded.usage is None
        assert "usage" not in loaded.to_dict()

    def test_skipped_with_usage_raises_in_post_init(self):
        with pytest.raises(ValueError, match="cannot contain output"):
            PreparationRecord(
                item_id="u9",
                status="skipped_time_sensitive",
                category="category",
                answer_mode="direct_answer",
                answer_source="source",
                usage=_SAMPLE_USAGE,
            )

    def test_non_dict_usage_raises_on_load(self):
        row = _prepared_row("u10")
        row["usage"] = "not-a-dict"

        with pytest.raises(ValueError, match="usage must be a dict or null"):
            preparation_record_from_dict(row)


class TestGoldExtractionAttempts:
    def test_prepared_and_failed_rows_round_trip_attempts(self):
        prepared = PreparationRecord(
            **_record_fields(_item("ga1"), prepared=True),
            status="prepared",
            gold_atoms=(Atom(id="A1", text="answer", required=True),),
            atom_source="inferred",
            gold_extraction_attempts=2,
        )
        prepared_row = prepared.to_dict()
        assert prepared_row["gold_extraction_attempts"] == 2
        assert preparation_record_from_dict(prepared_row) == prepared

        failed = PreparationRecord(
            **_record_fields(_item("ga2")),
            status="preparation_failed",
            error="extraction failed",
            gold_extraction_attempts=2,
        )
        failed_row = failed.to_dict()
        assert failed_row["gold_extraction_attempts"] == 2
        assert preparation_record_from_dict(failed_row) == failed

    def test_default_attempts_has_no_key_in_to_dict(self):
        record = PreparationRecord(
            **_record_fields(_item("ga3")),
            status="preparation_failed",
            error="extraction failed",
        )

        assert "gold_extraction_attempts" not in record.to_dict()

    @pytest.mark.parametrize("value", [True, 0, "2"])
    def test_rejects_invalid_attempts(self, value):
        with pytest.raises(ValueError, match="gold_extraction_attempts"):
            PreparationRecord(
                **_record_fields(_item("ga4")),
                status="preparation_failed",
                error="extraction failed",
                gold_extraction_attempts=value,
            )

    def test_row_without_attempts_key_still_loads(self):
        loaded = preparation_record_from_dict(_failed_row("ga5"))

        assert loaded.gold_extraction_attempts is None
