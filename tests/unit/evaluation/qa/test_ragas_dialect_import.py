# isort: skip_file
"""Importing the RAGAS golden-set dialect at the catalog boundary.

The benchmark bank is authored in ragas 0.3.5's native dialect
(``user_input``/``reference``). ``EvaluationCatalog.import_dataset`` normalizes
that dialect into the native row schema before validation — the row parser
itself keeps exactly one name per concept.
"""
import json
import os
from pathlib import Path

import pytest

from src.evaluation.qa.catalog import EvaluationCatalog
from src.evaluation.qa.dataset import derive_item_id

# The tracked 5-row anchor bank ships with the repo; the 105-row FASRC bank
# lives in the separate archi-config checkout (config/), which deployments
# have but CI does not — its acceptance test skips when the file is absent.
ANCHOR_BANK = Path("examples/benchmarking/anchor_questions.json")
FASRC_BANK = Path(
    os.environ.get("FASRC_RAGAS_BANK", "config/benchmarking/fasrc_ragas_queries.json")
)

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


def _native_twin_blob():
    """BANK_ROW in the native dialect, serialized byte-identically to the
    adapter's normalized output — so its digest collides with the bank's."""
    question = BANK_ROW["user_input"]
    answer = BANK_ROW["reference"]
    native_row = {
        key: value
        for key, value in BANK_ROW.items()
        if key not in {"user_input", "reference"}
    }
    native_row.update(
        {
            "question": question,
            "answer": answer,
            "time_sensitive": False,
            "id": derive_item_id(question, answer),
        }
    )
    return json.dumps(
        [native_row], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


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

        first_ids = [
            item.id for item in first_catalog.dataset_items(first_metadata["id"])
        ]
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

    def test_import_result_names_the_dialect_and_the_carried_fields(self, tmp_path):
        # Silent success on a misunderstood file is the failure mode this
        # change exists to avoid: the result says what was mapped and what was
        # carried rather than interpreted.
        catalog = EvaluationCatalog(tmp_path)

        metadata, _ = _import_bank(catalog, [BANK_ROW])

        assert metadata["import_dialect"] == "ragas"
        assert metadata["carried_fields"] == [
            "anchor_type",
            "notes",
            "sources",
            "status",
        ]
        # The report is persisted with the dataset, so a deduped re-import
        # reports the same thing.
        reimported, created = _import_bank(catalog, [BANK_ROW])
        assert created is False
        assert reimported["import_dialect"] == "ragas"
        assert reimported["carried_fields"] == metadata["carried_fields"]

    def test_native_import_reports_no_dialect_mapping(self, tmp_path):
        catalog = EvaluationCatalog(tmp_path)
        native = [
            {
                "id": "q1",
                "question": "Q",
                "answer": "A",
                "time_sensitive": False,
            }
        ]

        metadata, created = catalog.import_dataset(
            "Native", "native.json", json.dumps(native).encode()
        )

        assert created is True
        assert "import_dialect" not in metadata
        assert "carried_fields" not in metadata

    def test_dialect_rows_still_reach_the_strict_validator(self, tmp_path):
        # The adapter maps names; it never invents an answer. A bank row with
        # no reference fails validation loudly instead of importing hollow.
        catalog = EvaluationCatalog(tmp_path)
        row = {key: value for key, value in BANK_ROW.items() if key != "reference"}

        with pytest.raises(ValueError, match="answer must be a non-empty string"):
            _import_bank(catalog, [row])

    @pytest.mark.parametrize(
        "dialect_key,native_key,native_value",
        [
            ("user_input", "question", "A different question"),
            ("reference", "answer", "A different answer"),
        ],
    )
    def test_conflicting_aliases_are_refused_not_carried(
        self, tmp_path, dialect_key, native_key, native_value
    ):
        # A row carrying both spellings of one concept would be scored as
        # different content by the two stacks: the console evaluates the
        # native key while the harness's normalize_record prefers the RAGAS
        # one. Refuse loudly rather than guess.
        catalog = EvaluationCatalog(tmp_path)
        row = {**BANK_ROW, native_key: native_value}

        with pytest.raises(ValueError, match=f"{dialect_key}.*{native_key}"):
            _import_bank(catalog, [row])

    def test_dedupe_against_native_equivalent_still_reports_the_dialect(self, tmp_path):
        # A canonically-serialized native dataset and its RAGAS-dialect twin
        # normalize to the same bytes. Importing the bank second must dedupe
        # to the existing dataset AND still report what this operation
        # detected — the report describes the import, not the artifact.
        catalog = EvaluationCatalog(tmp_path)

        first, first_created = catalog.import_dataset(
            "Native twin", "native.json", _native_twin_blob()
        )
        second, second_created = _import_bank(catalog, [BANK_ROW])

        assert first_created is True
        assert "import_dialect" not in first
        assert second_created is False
        assert second["id"] == first["id"]
        assert second["import_dialect"] == "ragas"
        assert second["carried_fields"] == [
            "anchor_type",
            "notes",
            "sources",
            "status",
        ]

    def test_native_reimport_of_a_bank_twin_does_not_claim_the_dialect(self, tmp_path):
        # The reverse order: bank first (report persisted), byte-identical
        # native twin second. This import mapped nothing, so its response
        # must not surface the stored artifact's dialect keys — the console
        # would otherwise toast a native upload as a recognized RAGAS import.
        catalog = EvaluationCatalog(tmp_path)

        _, first_created = _import_bank(catalog, [BANK_ROW])
        second, second_created = catalog.import_dataset(
            "Native twin", "native.json", _native_twin_blob()
        )

        assert first_created is True
        assert second_created is False
        assert "import_dialect" not in second
        assert "carried_fields" not in second

    def test_v2_container_with_user_input_extra_is_not_adapted(self, tmp_path):
        # Detection is a bounded streaming scan keyed on top-level-array rows:
        # a Dataset V2 container bails at its first token, so a native import
        # is never materialized by the dialect check and never mapped. The
        # alias then reaches the row parser, which refuses it as reserved —
        # proof the adapter did not consume it, and no carried alias can
        # diverge the two evaluation stacks.
        catalog = EvaluationCatalog(tmp_path)
        container = {
            "schema_version": "qa-dataset-v2",
            "items": [
                {
                    "id": "q1",
                    "question": "Q",
                    "answer": "A",
                    "time_sensitive": False,
                    "user_input": "not mapped, and not carried either",
                }
            ],
        }

        with pytest.raises(ValueError, match="user_input.*question"):
            catalog.import_dataset(
                "Container", "container.json", json.dumps(container).encode()
            )

    def test_surrogate_in_a_dialect_row_fails_naming_the_field(self, tmp_path):
        # A lone escaped surrogate parses, but encoding the normalized blob
        # to UTF-8 would crash before the row parser ever sees the value.
        # The adapter validates each normalized row first, so the upload is
        # rejected with an error naming the field, not a codec traceback.
        catalog = EvaluationCatalog(tmp_path)
        blob = b'[{"user_input": "Q", "reference": "A", "notes": "bad \\ud800"}]'

        with pytest.raises(ValueError, match="notes"):
            catalog.import_dataset("Bank", "bank.json", blob)

    def test_duplicate_bank_rows_are_refused_with_row_numbers(self, tmp_path):
        # Two id-less rows with the same user_input and reference collide on
        # the content-derived id even when carried metadata differs. That is
        # refused with an error naming both rows in bank terms — not
        # disambiguated: an id salted with carried metadata would change item
        # identity on every sources edit, and an order-based suffix would
        # change it on every reorder. Rows that genuinely must coexist can
        # carry explicit distinct ids.
        catalog = EvaluationCatalog(tmp_path)
        variant = {
            **BANK_ROW,
            "sources": ["https://docs.rc.fas.harvard.edu/kb/gpu-jobs"],
            "anchor_type": "hard_retrieve",
        }

        with pytest.raises(ValueError, match=r"rows 1 and 2.*duplicate"):
            _import_bank(catalog, [BANK_ROW, variant])

    def test_rows_identical_after_newline_normalization_are_duplicates(
        self, tmp_path
    ):
        # Ids are derived from newline-normalized text, exactly as the row
        # parser derives them: rows whose question differs only by CRLF vs LF
        # are the same logical item and must hit the duplicate refusal, not
        # slip through with two ids and get evaluated twice.
        catalog = EvaluationCatalog(tmp_path)
        crlf = {**BANK_ROW, "user_input": "How do I request\r\na GPU?"}
        lf = {**BANK_ROW, "user_input": "How do I request\na GPU?"}

        with pytest.raises(ValueError, match=r"rows 1 and 2.*duplicate"):
            _import_bank(catalog, [crlf, lf])

    def test_ids_are_stable_across_line_ending_variants(self, tmp_path):
        crlf_catalog = EvaluationCatalog(tmp_path / "crlf")
        lf_catalog = EvaluationCatalog(tmp_path / "lf")
        crlf = {**BANK_ROW, "user_input": "How do I request\r\na GPU?"}
        lf = {**BANK_ROW, "user_input": "How do I request\na GPU?"}

        crlf_metadata, _ = _import_bank(crlf_catalog, [crlf])
        lf_metadata, _ = _import_bank(lf_catalog, [lf])

        (crlf_item,) = crlf_catalog.dataset_items(crlf_metadata["id"])
        (lf_item,) = lf_catalog.dataset_items(lf_metadata["id"])
        assert crlf_item.id == lf_item.id
        assert crlf_item.question == lf_item.question

    def test_duplicate_bank_rows_import_with_explicit_ids(self, tmp_path):
        catalog = EvaluationCatalog(tmp_path)
        first = {**BANK_ROW, "id": "gpu-easy"}
        second = {
            **BANK_ROW,
            "id": "gpu-hard",
            "anchor_type": "hard_retrieve",
        }

        metadata, created = _import_bank(catalog, [first, second])

        assert created is True
        assert [item.id for item in catalog.dataset_items(metadata["id"])] == [
            "gpu-easy",
            "gpu-hard",
        ]

    def test_duplicate_keys_pass_through_to_the_strict_pipeline(self, tmp_path):
        # The adapter refuses to parse a duplicate-keyed upload (json.loads
        # would silently collapse the duplicates); the blob falls through
        # unchanged so the strict ijson pipeline reports it.
        catalog = EvaluationCatalog(tmp_path)
        blob = b'[{"user_input": "Q", "user_input": "Q2", "reference": "A"}]'

        with pytest.raises(ValueError, match="duplicate key"):
            catalog.import_dataset("Bank", "bank.json", blob)

    def test_non_finite_numbers_pass_through_to_the_strict_pipeline(self, tmp_path):
        catalog = EvaluationCatalog(tmp_path)
        blob = b'[{"user_input": "Q", "reference": "A", "score": NaN}]'

        with pytest.raises(ValueError, match="invalid JSON dataset"):
            catalog.import_dataset("Bank", "bank.json", blob)

    def test_non_dict_row_in_a_dialect_bank_fails_loudly(self, tmp_path):
        catalog = EvaluationCatalog(tmp_path)

        with pytest.raises(ValueError, match="must be an object"):
            _import_bank(catalog, [BANK_ROW, "not a row"])


class _DeterministicExtractor:
    def extract_gold(self, question, answer):
        return {"atoms": [{"id": "required", "text": answer, "required": True}]}


def _import_bank_file(catalog, path, name):
    return catalog.import_dataset(name, path.name, path.read_bytes())


def _assert_bank_imports_whole(catalog, path, name, expected_rows):
    metadata, created = _import_bank_file(catalog, path, name)
    assert created is True
    assert metadata["item_count"] == expected_rows
    assert metadata["import_dialect"] == "ragas"
    items = catalog.dataset_items(metadata["id"])
    assert len(items) == expected_rows
    for item in items:
        assert item.question.strip()
        assert isinstance(item.answer, str) and item.answer.strip()
        assert item.extra is not None and "sources" in item.extra
    return metadata


class TestTheRealBank:
    def test_anchor_bank_imports_end_to_end(self, tmp_path):
        _assert_bank_imports_whole(
            EvaluationCatalog(tmp_path), ANCHOR_BANK, "Anchors", 5
        )

    def test_fasrc_bank_imports_end_to_end(self, tmp_path):
        # The 105-row curated bank; present on deployments (archi-config
        # checkout), absent in CI. This is the acceptance test for the whole
        # change — before the adapter it failed with the unknown-field error.
        if not FASRC_BANK.is_file():
            pytest.skip(
                f"{FASRC_BANK} not present (archi-config checkout only; "
                "point FASRC_RAGAS_BANK at the bank to run this)"
            )
        _assert_bank_imports_whole(
            EvaluationCatalog(tmp_path), FASRC_BANK, "FASRC golden set", 105
        )

    def test_saved_child_of_the_bank_still_carries_sources(self, tmp_path):
        # The console writes datasets as well as reading them: approving
        # generated atoms publishes an immutable child rebuilt from the item
        # model. This is the failure the whole change exists to prevent — a
        # child that silently lost `sources` is a bank the benchmark can no
        # longer use.
        catalog = EvaluationCatalog(tmp_path)
        metadata, _ = _import_bank_file(catalog, ANCHOR_BANK, "Anchors")

        draft = catalog.create_atom_draft(
            metadata["id"], "builtin", _DeterministicExtractor()
        )
        reviewed = [
            {"item_id": row["item_id"], "atoms": row["atoms"]} for row in draft["items"]
        ]
        child = catalog.save_reviewed_dataset(draft["id"], "Anchors child", reviewed)

        child_rows = json.loads(catalog.dataset_path(child["id"]).read_text())
        assert len(child_rows) == 5
        for row in child_rows:
            # Carried, with its original value — [] on a refusal probe is a
            # value, not an omission.
            assert "sources" in row, row
            assert row["expected_atoms"]
