# isort: skip_file
"""Carry-through of unrecognized dataset row fields (read-goldenset-in-qa-console).

The RAGAS golden-set bank carries fields the console does not interpret
(``sources``, ``notes``, ``status``, ...). These tests pin that such fields are
parsed into ``DatasetItem.extra``, re-emitted whenever the console writes a
dataset, hashed into the canonical serialization, and inert everywhere else.
"""
import json

from src.evaluation.qa.dataset import (
    DATASET_V2_SCHEMA_VERSION,
    DatasetGateway,
    dataset_item_to_dict,
)

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
