"""Tests for the retrieval metadata merge — title overlay from catalog extra_json.

At retrieval, chunk metadata (``c.metadata``) is merged with document-level fields
from the ``documents`` row. ``url`` is overlaid from its column; ``title`` lives in
``extra_json`` and is overlaid here too, so a title backfilled onto the catalog
surfaces even for chunks embedded before the title existed (and not re-embedded).
"""

import json

from src.data_manager.vectorstore.postgres_vectorstore import _merge_row_metadata


def _row(metadata=None, extra_json=None, **cols):
    row = {
        "metadata": metadata if metadata is not None else {},
        "resource_hash": cols.get("resource_hash"),
        "display_name": cols.get("display_name"),
        "source_type": cols.get("source_type"),
        "url": cols.get("url"),
        "extra_json": extra_json,
    }
    return row


class TestTitleOverlay:

    def test_title_overlaid_from_extra_json_dict(self):
        row = _row(metadata={"chunk_id": "c1"}, extra_json={"title": "VSCode Remote"})
        out = _merge_row_metadata(row)
        assert out["title"] == "VSCode Remote"

    def test_title_overlaid_from_extra_json_string(self):
        row = _row(extra_json=json.dumps({"title": "Slurm Guide", "other": 1}))
        out = _merge_row_metadata(row)
        assert out["title"] == "Slurm Guide"

    def test_backfilled_title_overrides_absent_chunk_title(self):
        # Old chunk had no title; catalog now carries one.
        row = _row(metadata={"url": "https://x/y"}, extra_json={"title": "Backfilled"})
        out = _merge_row_metadata(row)
        assert out["title"] == "Backfilled"

    def test_empty_extra_title_not_overlaid(self):
        row = _row(metadata={"title": "Existing"}, extra_json={"title": "   "})
        out = _merge_row_metadata(row)
        # whitespace-only catalog title must not clobber an existing chunk title
        assert out["title"] == "Existing"

    def test_missing_extra_json_is_safe(self):
        row = _row(metadata={"chunk_id": "c1"}, extra_json=None)
        out = _merge_row_metadata(row)
        assert "title" not in out

    def test_malformed_extra_json_string_does_not_raise(self):
        row = _row(extra_json="{not valid json")
        out = _merge_row_metadata(row)
        assert "title" not in out


class TestExistingOverlaysPreserved:

    def test_url_and_hash_still_overlaid(self):
        row = _row(
            metadata={"chunk_id": "c1"},
            resource_hash="h1",
            display_name="page.md",
            source_type="web",
            url="https://x/y",
            extra_json={"title": "T"},
        )
        out = _merge_row_metadata(row)
        assert out["resource_hash"] == "h1"
        assert out["display_name"] == "page.md"
        assert out["source_type"] == "web"
        assert out["url"] == "https://x/y"
        assert out["title"] == "T"

    def test_string_chunk_metadata_is_parsed(self):
        row = _row(metadata=json.dumps({"chunk_id": "c9"}), url="https://x")
        out = _merge_row_metadata(row)
        assert out["chunk_id"] == "c9"
        assert out["url"] == "https://x"
