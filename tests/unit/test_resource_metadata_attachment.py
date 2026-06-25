"""Tests for processor-driven metadata attachment across resource types.

`BaseResource.set_metadata_field` must let processors attach values
(`converted_from`, `llm_category`) uniformly, and those values must surface in
`get_metadata().as_dict()` so they reach `catalog.upsert_resource`. This matters
most for `LocalFileResource`, which had no mutable metadata field.
"""

from pathlib import Path

from src.data_manager.collectors.localfile_resource import LocalFileResource
from src.data_manager.collectors.resource_base import BaseResource
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource
from src.data_manager.collectors.tickets.ticket_resource import TicketResource


def test_scraped_resource_attaches_metadata_field():
    resource = ScrapedResource(
        url="https://example.com/page",
        content="<h1>Hi</h1>",
        suffix="html",
        source_type="web",
    )

    resource.set_metadata_field("llm_category", "compute")

    assert resource.get_metadata().as_dict()["llm_category"] == "compute"


def test_ticket_resource_attaches_metadata_field():
    resource = TicketResource(
        ticket_id="ABC-1",
        content="body",
        source_type="redmine",
    )

    resource.set_metadata_field("llm_category", "storage")

    assert resource.get_metadata().as_dict()["llm_category"] == "storage"


def test_localfile_resource_attaches_metadata_field(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("hello")
    resource = LocalFileResource(
        file_name="note.txt",
        source_path=source,
        content=b"hello",
    )

    resource.set_metadata_field("llm_category", "policy")
    resource.set_metadata_field("converted_from", "html")

    metadata = resource.get_metadata().as_dict()
    assert metadata["llm_category"] == "policy"
    assert metadata["converted_from"] == "html"


def test_localfile_resource_skips_none_metadata_values(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("hello")
    resource = LocalFileResource(
        file_name="note.txt",
        source_path=source,
        content=b"hello",
        metadata={"keep": "yes", "drop": None},
    )

    metadata = resource.get_metadata().as_dict()
    assert metadata["keep"] == "yes"
    assert "drop" not in metadata


class _NoMetadataResource(BaseResource):
    """A resource type with no ``metadata`` field, exercising the helper's
    create-on-demand branch on BaseResource."""

    def get_hash(self) -> str:
        return "nm-1"

    def get_filename(self) -> str:
        return "doc.txt"

    def get_content(self):
        return "body"


def test_set_metadata_field_creates_dict_when_absent():
    resource = _NoMetadataResource()
    assert not hasattr(resource, "metadata")

    resource.set_metadata_field("llm_category", "compute")

    assert resource.metadata == {"llm_category": "compute"}


def test_localfile_label_reaches_inner_persist_via_pipeline(tmp_path):
    """End-to-end (B4 contract): a label attached to a LocalFileResource by a
    processor surfaces all the way to persistence. Run the resource through a
    ProcessingPersistenceService whose pipeline categorizes it, with a MOCK inner
    persist_resource, and assert the inner receives a resource whose get_metadata()
    carries the attached llm_category."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from src.data_manager.collectors.processing import (
        CategorizationProcessor,
        ProcessingPersistenceService,
        ResourcePipeline,
    )

    source = tmp_path / "doc.txt"
    source.write_text("some body about compute clusters")
    resource = LocalFileResource(
        file_name="doc.txt",
        source_path=source,
        content=b"some body about compute clusters",
    )

    fake_model = SimpleNamespace(
        invoke=lambda messages: SimpleNamespace(content="compute")
    )
    categorizer = CategorizationProcessor(
        categories=["compute", "storage"],
        provider="local",
        model="qwen",
        provider_config={"base_url": "http://vllm:8001"},
        model_factory=lambda p, m, cfg: fake_model,
    )

    inner = MagicMock()
    wrapper = ProcessingPersistenceService(inner, ResourcePipeline([categorizer]))

    wrapper.persist_resource(resource, tmp_path / "local_files", False)

    inner.persist_resource.assert_called_once()
    persisted_resource = inner.persist_resource.call_args[0][0]
    metadata = persisted_resource.get_metadata().as_dict()
    assert metadata["llm_category"] == "compute"
