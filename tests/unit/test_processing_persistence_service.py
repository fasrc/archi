"""Tests for ProcessingPersistenceService (the wrap-and-delegate seam)."""

from pathlib import Path
from unittest.mock import MagicMock

from src.data_manager.collectors.processing import (
    ProcessingPersistenceService,
    ResourcePipeline,
)


class _RecordingProcessor:
    def __init__(self):
        self.seen = None

    def process(self, resource):
        self.seen = resource
        resource.processed = True
        return resource


class _Resource:
    processed = False


def _wrap(processors=None):
    inner = MagicMock()
    pipeline = ResourcePipeline(processors or [])
    return ProcessingPersistenceService(inner, pipeline), inner


def test_persist_resource_runs_pipeline_then_delegates_with_all_args():
    proc = _RecordingProcessor()
    wrapper, inner = _wrap([proc])
    resource = _Resource()
    target_dir = Path("/data/web")

    wrapper.persist_resource(resource, target_dir, overwrite=True)

    assert proc.seen is resource
    assert resource.processed is True
    inner.persist_resource.assert_called_once_with(resource, target_dir, True)


def test_persist_resource_default_overwrite_forwarded():
    wrapper, inner = _wrap([])
    resource = _Resource()
    target_dir = Path("/data/web")

    wrapper.persist_resource(resource, target_dir)

    inner.persist_resource.assert_called_once_with(resource, target_dir, False)


def test_persist_resource_returns_inner_result():
    wrapper, inner = _wrap([])
    inner.persist_resource.return_value = Path("/data/web/doc.md")
    assert wrapper.persist_resource(_Resource(), Path("/data/web")) == Path(
        "/data/web/doc.md"
    )


def test_delegates_methods_to_inner():
    wrapper, inner = _wrap([])

    wrapper.delete_resource("hash-1", flush=False)
    inner.delete_resource.assert_called_once_with("hash-1", flush=False)

    wrapper.delete_by_metadata_filter("source_type", "web")
    inner.delete_by_metadata_filter.assert_called_once_with("source_type", "web")

    wrapper.reset_directory(Path("/data/web"))
    inner.reset_directory.assert_called_once_with(Path("/data/web"))

    wrapper.flush_index()
    inner.flush_index.assert_called_once_with()


def test_delegates_attributes_to_inner():
    inner = MagicMock()
    inner.catalog = "CATALOG"
    inner.data_path = Path("/data")
    inner.pg_config = {"host": "db"}
    wrapper = ProcessingPersistenceService(inner, ResourcePipeline([]))

    assert wrapper.catalog == "CATALOG"
    assert wrapper.data_path == Path("/data")
    assert wrapper.pg_config == {"host": "db"}
