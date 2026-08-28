# isort: skip_file
"""Carry-through of unrecognized dataset row fields (read-goldenset-in-qa-console).

The RAGAS golden-set bank carries fields the console does not interpret
(``sources``, ``notes``, ``status``, ...). These tests pin that such fields are
parsed into ``DatasetItem.extra``, re-emitted whenever the console writes a
dataset, hashed into the canonical serialization, and inert everywhere else.
"""
import json

from src.evaluation.qa.catalog import EvaluationCatalog
from src.evaluation.qa.dataset import (
    DATASET_V2_SCHEMA_VERSION,
    DatasetGateway,
    dataset_item_to_dict,
)
from src.evaluation.qa.oracle import canonical_json

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

    def test_import_differing_only_in_carried_field_creates_new_dataset(
        self, tmp_path
    ):
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
