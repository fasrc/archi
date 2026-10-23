"""The retriever tool's default rendering: every result, each sized to fit.

The model must read every document the retriever returns (the same set the user
is shown as sources), and the complete result must stay under the 8000-character
ceiling the context-editing arithmetic assumes (``DEFAULT_RETRIEVER_RESULT_CHARS``,
issue #235). So each body is limited to ``min(max_chars, share)``: its fair share
of that ceiling after the headers, separators and ``...`` markers.
"""

import string

from langchain_core.documents import Document

from src.archi.pipelines.agents.tools.result_limits import TRUNCATION_MARKER
from src.archi.pipelines.agents.tools.retriever import (
    DEFAULT_RETRIEVER_RESULT_CHARS,
    create_retriever_tool,
)


class _FixedRetriever:
    """Stands in for the vector store; the real tool renders its documents."""

    def __init__(self, docs):
        self._docs = docs

    def invoke(self, query):
        return self._docs


def _docs(count, *, title_chars=45, body_chars=3000):
    """Documents shaped like production results: one distinct letter per body."""
    return [
        Document(
            page_content=string.ascii_lowercase[i] * body_chars,
            metadata={
                "title": ("T" * title_chars)[: title_chars - 2] + f"{i:02d}",
                "url": f"https://docs.rc.fas.harvard.edu/kb/fasse-protected-data-transfers-{i:02d}/",
                "resource_hash": f"{i:032x}",
                "rerank_score": 0.9 - i / 100,
            },
        )
        for i in range(count)
    ]


def _render(docs):
    tool = create_retriever_tool(
        _FixedRetriever(docs), name="search_vectorstore_hybrid"
    )
    return tool.invoke({"query": "How do I transfer large data to FASSE?"})


def _snippets(out):
    return out.count("\nScore: ")


def test_five_results_render_full_length_bodies():
    out = _render(_docs(5))

    assert _snippets(out) == 5
    for letter in "abcde":
        assert (letter * 1200 + "...") in out
        assert letter * 1201 not in out
    assert len(out) <= DEFAULT_RETRIEVER_RESULT_CHARS
    assert TRUNCATION_MARKER not in out


def test_eight_results_all_fit_under_the_ceiling():
    out = _render(_docs(8))

    assert _snippets(out) == 8
    for letter in "abcdefgh":
        assert (letter * 200) in out
    assert TRUNCATION_MARKER not in out
    assert len(out) <= DEFAULT_RETRIEVER_RESULT_CHARS
    # The bodies take their full share: less than one character per document
    # is left over from the floor division.
    assert len(out) > DEFAULT_RETRIEVER_RESULT_CHARS - 8


def test_pathological_metadata_floors_bodies_and_the_ceiling_still_holds():
    out = _render(_docs(5, title_chars=3000))

    # Headers alone exceed the ceiling, so the share bottoms out at the floor...
    assert ("a" * 200 + "...") in out
    assert "a" * 201 not in out
    # ...and the whole-result clamp still bounds and marks the output (#235).
    assert len(out) <= DEFAULT_RETRIEVER_RESULT_CHARS
    assert TRUNCATION_MARKER in out


def test_a_non_positive_ceiling_sizes_nothing():
    """``clamp_result`` reads a ceiling <= 0 as "no ceiling"; so does the share."""
    tool = create_retriever_tool(
        _FixedRetriever(_docs(5)),
        name="search_vectorstore_hybrid",
        max_result_chars=0,
    )

    out = tool.invoke({"query": "q"})

    assert ("a" * 1200 + "...") in out
    assert "a" * 1201 not in out
