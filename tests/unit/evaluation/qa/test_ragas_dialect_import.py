# isort: skip_file
"""Importing the RAGAS golden-set dialect at the catalog boundary.

The benchmark bank is authored in ragas 0.3.5's native dialect
(``user_input``/``reference``). ``EvaluationCatalog.import_dataset`` normalizes
that dialect into the native row schema before validation — the row parser
itself keeps exactly one name per concept.
"""
import json

import pytest

from src.evaluation.qa.catalog import EvaluationCatalog

BANK_ROW = {
    "user_input": "How do I request a GPU on Cannon?",
    "reference": "Add #SBATCH --gpus=1 to the submission script.",
    "sources": ["https://docs.rc.fas.harvard.edu/kb/running-jobs"],
    "notes": "grounded in Running Jobs page text",
    "status": "draft",
    "anchor_type": "easy_retrieve",
}


def _import_bank(catalog, rows, name="Bank"):
    return catalog.import_dataset(name, "bank.json", json.dumps(rows).encode())


class TestRagasDialectImport:
    def test_bank_row_imports_unconverted(self, tmp_path):
        catalog = EvaluationCatalog(tmp_path)

        metadata, created = _import_bank(catalog, [BANK_ROW])

        assert created is True
        (item,) = catalog.dataset_items(metadata["id"])
        assert item.question == BANK_ROW["user_input"]
        assert item.answer == BANK_ROW["reference"]
        assert item.time_sensitive is False
        assert item.id
        assert item.extra == {
            "sources": BANK_ROW["sources"],
            "notes": BANK_ROW["notes"],
            "status": BANK_ROW["status"],
            "anchor_type": BANK_ROW["anchor_type"],
        }

    def test_synthesized_ids_are_stable_across_imports(self, tmp_path):
        first_catalog = EvaluationCatalog(tmp_path / "first")
        second_catalog = EvaluationCatalog(tmp_path / "second")

        first_metadata, _ = _import_bank(first_catalog, [BANK_ROW])
        second_metadata, _ = _import_bank(second_catalog, [BANK_ROW])

        first_ids = [item.id for item in first_catalog.dataset_items(first_metadata["id"])]
        second_ids = [
            item.id for item in second_catalog.dataset_items(second_metadata["id"])
        ]
        assert first_ids == second_ids

    def test_maintenance_edit_to_sources_creates_new_dataset(self, tmp_path):
        # The dedupe hash is computed over the normalized bytes; a bank
        # differing only in a carried field must not resolve to the dataset
        # already imported, or the operator's edit silently does nothing.
        catalog = EvaluationCatalog(tmp_path)
        edited = {
            **BANK_ROW,
            "sources": ["https://docs.rc.fas.harvard.edu/kb/gpu-jobs"],
        }

        _, first_created = _import_bank(catalog, [BANK_ROW])
        _, second_created = _import_bank(catalog, [edited])

        assert first_created is True
        assert second_created is True

    def test_reimporting_the_same_bank_dedupes(self, tmp_path):
        catalog = EvaluationCatalog(tmp_path)

        first_metadata, first_created = _import_bank(catalog, [BANK_ROW])
        second_metadata, second_created = _import_bank(catalog, [BANK_ROW])

        assert first_created is True
        assert second_created is False
        assert second_metadata["id"] == first_metadata["id"]

    def test_dialect_rows_still_reach_the_strict_validator(self, tmp_path):
        # The adapter maps names; it never invents an answer. A bank row with
        # no reference fails validation loudly instead of importing hollow.
        catalog = EvaluationCatalog(tmp_path)
        row = {key: value for key, value in BANK_ROW.items() if key != "reference"}

        with pytest.raises(ValueError, match="answer must be a non-empty string"):
            _import_bank(catalog, [row])
