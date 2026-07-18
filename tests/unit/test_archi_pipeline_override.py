"""Unit tests for the optional ``pipeline=`` override on the archi orchestrator.

These exercise the request-local override seam (issue #86): a caller may hand
``archi.stream()`` a pipeline to stream from instead of the shared
``self.pipeline``, without the orchestrator ever touching the shared pipeline.

The ``archi`` instance is constructed with ``object.__new__`` (bypassing
``__init__``) and only the attributes ``stream`` actually reads are set, so
these stay pure unit tests with no config, vectorstore, or pipeline
construction.
"""

from unittest.mock import MagicMock

from src.archi.archi import archi
from src.archi.utils.output_dataclass import PipelineOutput


def _make_archi(shared_pipeline):
    """Build an archi instance without running __init__ (unit-test only)."""
    inst = object.__new__(archi)
    inst.pipeline = shared_pipeline
    inst.pipeline_name = "SharedPipeline"
    vs_connector = MagicMock()
    vs_connector.get_vectorstore.return_value = MagicMock()
    inst.vs_connector = vs_connector
    return inst


def _pipeline_yielding(marker):
    """A stub pipeline whose ``stream`` yields a single tagged PipelineOutput."""
    pipeline = MagicMock()
    pipeline.stream.return_value = iter([PipelineOutput(answer=marker)])
    return pipeline


def test_stream_uses_supplied_pipeline_and_leaves_shared_untouched():
    shared = _pipeline_yielding("shared")
    other = _pipeline_yielding("other")
    inst = _make_archi(shared)

    outputs = list(inst.stream("query", pipeline=other))

    assert [o.answer for o in outputs] == ["other"]
    other.stream.assert_called_once()
    shared.stream.assert_not_called()


def test_stream_without_override_uses_shared_pipeline():
    shared = _pipeline_yielding("shared")
    inst = _make_archi(shared)

    outputs = list(inst.stream("query"))

    assert [o.answer for o in outputs] == ["shared"]
    shared.stream.assert_called_once()


def test_stream_supports_check_evaluates_supplied_pipeline():
    # Shared pipeline can stream; the override cannot -> AttributeError.
    shared = _pipeline_yielding("shared")
    other = MagicMock(spec=[])  # no ``stream`` attribute
    inst = _make_archi(shared)

    gen = inst.stream("query", pipeline=other)
    try:
        list(gen)
        raised = False
    except AttributeError:
        raised = True

    assert raised
    shared.stream.assert_not_called()
