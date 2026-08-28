# isort: skip_file
"""Carry-through of unrecognized dataset row fields (read-goldenset-in-qa-console).

The RAGAS golden-set bank carries fields the console does not interpret
(``sources``, ``notes``, ``status``, ...). These tests pin that such fields are
parsed into ``DatasetItem.extra``, re-emitted whenever the console writes a
dataset, hashed into the canonical serialization, and inert everywhere else.
"""
import json

import pytest

from src.evaluation.qa.catalog import EvaluationCatalog
from src.evaluation.qa.dataset import (
    DATASET_V2_SCHEMA_VERSION,
    DatasetGateway,
    dataset_item_to_dict,
)
from src.evaluation.qa.oracle import canonical_json
from src.evaluation.qa.preparation import prepare_dataset_item

BANK_EXTRAS = {
    "sources": ["https://docs.rc.fas.harvard.edu/kb/running-jobs"],
    "notes": "grounded in Running Jobs page text",
}


def _write_v1(path, rows):
    path.write_text(json.dumps(rows), encoding="utf-8")


def _write_v2(path, rows):
    path.write_text(
        json.dumps({"schema_version": DATASET_V2_SCHEMA_VERSION, "items": rows}),
        encoding="utf-8",
    )


class TestExtraFieldRoundTrip:
    def test_v1_row_extras_survive_the_round_trip(self, tmp_path):
        row = {
            "id": "q1",
            "question": "How do I request a GPU?",
            "answer": "Add #SBATCH --gpus=1.",
            "time_sensitive": False,
            **BANK_EXTRAS,
        }
        path = tmp_path / "dataset.json"
        _write_v1(path, [row])

        items = list(DatasetGateway().read(path))

        assert len(items) == 1
        emitted = dataset_item_to_dict(items[0])
        assert emitted["sources"] == BANK_EXTRAS["sources"]
        assert emitted["notes"] == BANK_EXTRAS["notes"]

    def test_v2_row_extras_survive_the_round_trip(self, tmp_path):
        row = {
            "id": "q1",
            "question": "How do I request a GPU?",
            "answer": "Add #SBATCH --gpus=1.",
            "time_sensitive": False,
            **BANK_EXTRAS,
        }
        path = tmp_path / "dataset.json"
        _write_v2(path, [row])

        items = list(DatasetGateway().read(path))

        assert len(items) == 1
        emitted = dataset_item_to_dict(items[0])
        assert emitted["sources"] == BANK_EXTRAS["sources"]
        assert emitted["notes"] == BANK_EXTRAS["notes"]

    def test_carried_fields_change_canonical_serialization(self, tmp_path):
        # canonical_json(dataset_item_to_dict(...)) is the content-addressed
        # serialization; a maintenance edit to a carried field must change it,
        # or the catalog's sha256 dedupe collapses the edit into "already
        # imported, nothing to do".
        base = {
            "id": "q1",
            "question": "Q",
            "answer": "A",
            "time_sensitive": False,
            "sources": ["https://docs.rc.fas.harvard.edu/kb/running-jobs"],
        }
        edited = {
            **base,
            "sources": ["https://docs.rc.fas.harvard.edu/kb/gpu-jobs"],
        }
        base_path = tmp_path / "base.json"
        edited_path = tmp_path / "edited.json"
        _write_v1(base_path, [base])
        _write_v1(edited_path, [edited])

        (base_item,) = list(DatasetGateway().read(base_path))
        (edited_item,) = list(DatasetGateway().read(edited_path))

        assert canonical_json(dataset_item_to_dict(base_item)) != canonical_json(
            dataset_item_to_dict(edited_item)
        )

    def test_import_differing_only_in_carried_field_creates_new_dataset(self, tmp_path):
        base = {
            "id": "q1",
            "question": "Q",
            "answer": "A",
            "time_sensitive": False,
            "sources": ["https://docs.rc.fas.harvard.edu/kb/running-jobs"],
        }
        edited = {
            **base,
            "sources": ["https://docs.rc.fas.harvard.edu/kb/gpu-jobs"],
        }
        catalog = EvaluationCatalog(tmp_path / "catalog")

        first, first_created = catalog.import_dataset(
            "Bank", "bank.json", json.dumps([base]).encode()
        )
        second, second_created = catalog.import_dataset(
            "Bank", "bank.json", json.dumps([edited]).encode()
        )

        assert first_created is True
        assert second_created is True
        assert second["id"] != first["id"]

    def test_rows_without_extras_emit_no_extra_keys(self, tmp_path):
        row = {
            "id": "q1",
            "question": "Q",
            "answer": "A",
            "time_sensitive": False,
        }
        path = tmp_path / "dataset.json"
        _write_v1(path, [row])

        (item,) = list(DatasetGateway().read(path))

        assert item.extra is None
        assert dataset_item_to_dict(item) == row


class TestNearMissKeysAreRefused:
    def test_misspelled_expected_atoms_hands_authorship_to_the_extractor(
        self, tmp_path
    ):
        # `expectd_atoms` silently carried would mean expected_atoms is absent,
        # and absent means "invoke the atom extractor": the reviewer's approved
        # obligations would be swapped for LLM-inferred ones on a run that
        # reports success. Near-miss keys are refused, naming the likely intent.
        row = {
            "id": "q1",
            "question": "Q",
            "answer": "A",
            "time_sensitive": False,
            "expectd_atoms": [{"id": "a", "text": "A", "required": True}],
        }
        path = tmp_path / "dataset.json"
        _write_v1(path, [row])

        with pytest.raises(ValueError, match=r"expectd_atoms.*expected_atoms"):
            list(DatasetGateway().read(path))

    def test_the_real_bank_extras_are_not_near_misses(self, tmp_path):
        # The rule must refuse typos without refusing the bank: every extra the
        # golden set actually carries is far from every known field name.
        bank_extras = {
            "sources": ["https://docs.rc.fas.harvard.edu/kb/running-jobs"],
            "notes": "operator note",
            "status": "draft",
            "anchor_type": "easy_retrieve",
            "source_match_field": ["url"],
        }
        row = {
            "id": "q1",
            "question": "Q",
            "answer": "A",
            "time_sensitive": False,
            **bank_extras,
        }
        path = tmp_path / "dataset.json"
        _write_v1(path, [row])

        (item,) = list(DatasetGateway().read(path))

        assert item.extra == bank_extras


class TestKnownFieldsKeepTheirContract:
    def test_wrong_typed_known_field_is_still_rejected(self, tmp_path):
        # Carrying unknowns must not loosen the known fields: a wrong type is
        # still an error even on a row that also carries extras.
        row = {
            "id": "q1",
            "question": "Q",
            "answer": "A",
            "time_sensitive": "false",
            **BANK_EXTRAS,
        }
        path = tmp_path / "dataset.json"
        _write_v1(path, [row])

        with pytest.raises(ValueError, match="time_sensitive must be a boolean"):
            list(DatasetGateway().read(path))

    def test_missing_required_field_is_still_rejected(self, tmp_path):
        row = {
            "id": "q1",
            "question": "Q",
            "time_sensitive": False,
            **BANK_EXTRAS,
        }
        path = tmp_path / "dataset.json"
        _write_v1(path, [row])

        with pytest.raises(ValueError, match="answer must be a non-empty string"):
            list(DatasetGateway().read(path))


class TestExtraValuesAreValidated:
    def test_lone_surrogate_in_an_extra_is_refused_at_import(self, tmp_path):
        # An escaped unmatched surrogate parses as JSON but cannot be written
        # back (`ensure_ascii=False` raises at child-save time). Extras get
        # the same JSON-value validation as the known structured fields, so
        # the upload fails at import instead of stranding a catalog entry
        # that cannot complete the promised round trip.
        path = tmp_path / "dataset.json"
        path.write_text(
            '[{"id": "q1", "question": "Q", "answer": "A",'
            ' "time_sensitive": false, "notes": "bad \\ud800 text"}]',
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="notes"):
            list(DatasetGateway().read(path))

    def test_nested_surrogate_in_an_extra_is_refused_at_import(self, tmp_path):
        path = tmp_path / "dataset.json"
        path.write_text(
            '[{"id": "q1", "question": "Q", "answer": "A",'
            ' "time_sensitive": false, "sources": ["ok", "bad \\udfff"]}]',
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="sources"):
            list(DatasetGateway().read(path))


class _DeterministicExtractor:
    def extract_gold(self, question, answer):
        return {"atoms": [{"id": "required", "text": answer, "required": True}]}


class TestExtrasAreInert:
    def test_preparation_is_identical_with_and_without_extras(self, tmp_path):
        # Carried fields are data in transit: they must never reach
        # preparation (and therefore running and scoring).
        plain_row = {
            "id": "q1",
            "question": "Q",
            "answer": "A",
            "time_sensitive": False,
        }
        extra_row = {**plain_row, **BANK_EXTRAS, "status": "draft"}
        _write_v1(tmp_path / "plain.json", [plain_row])
        _write_v1(tmp_path / "extra.json", [extra_row])

        (plain_item,) = list(DatasetGateway().read(tmp_path / "plain.json"))
        (extra_item,) = list(DatasetGateway().read(tmp_path / "extra.json"))
        plain_record = prepare_dataset_item(plain_item, _DeterministicExtractor())
        extra_record = prepare_dataset_item(extra_item, _DeterministicExtractor())

        assert plain_record == extra_record
