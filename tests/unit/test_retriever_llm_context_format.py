"""
Unit tests for ``_format_documents_for_llm`` — the retriever context the LLM
sees during tool calls.

This is the snippet header the model copies when it cites a source. For the
agent to emit an inline ``[title](url)`` hyperlink instead of a bare ``[1]``,
the per-snippet header must surface the document ``title`` and ``url`` (while
still carrying the ``resource_hash`` the document-fetch tool needs).
"""

import pytest
from langchain_core.documents import Document

from src.archi.pipelines.agents.tools.retriever import _format_documents_for_llm


def _wrap(*docs):
    """Wrap Documents as the (Document, score) tuples the formatter expects."""
    return [(d, 0.5) for d in docs]


class TestSnippetExposesTitleAndUrl:

    def test_header_includes_title_and_url_and_hash(self):
        doc = Document(
            page_content="If the tunnel job is running but connection still fails...",
            metadata={
                "url": "https://docs.rc.fas.harvard.edu/kb/vscode-remote/",
                "title": "VSCode Remote Development via SSH and Tunnel",
                "resource_hash": "abc123",
                "filename": "vscode-remote.md",
            },
        )
        out = _format_documents_for_llm(_wrap(doc), max_documents=4, max_chars=800)

        # The model must SEE the title and url to be able to cite them.
        assert "VSCode Remote Development via SSH and Tunnel" in out
        assert "https://docs.rc.fas.harvard.edu/kb/vscode-remote/" in out
        # The hash is still needed by the fetch_catalog_document companion tool.
        assert "abc123" in out

    def test_each_snippet_carries_its_own_title_and_url(self):
        docs = [
            Document(
                page_content="alpha",
                metadata={
                    "url": "https://example.org/a",
                    "title": "Alpha Doc",
                    "resource_hash": "h1",
                },
            ),
            Document(
                page_content="beta",
                metadata={
                    "url": "https://example.org/b",
                    "title": "Beta Doc",
                    "resource_hash": "h2",
                },
            ),
        ]
        out = _format_documents_for_llm(_wrap(*docs), max_documents=4, max_chars=800)
        assert "Alpha Doc" in out and "https://example.org/a" in out
        assert "Beta Doc" in out and "https://example.org/b" in out


class TestTitleFallback:

    def test_title_falls_back_to_display_name(self):
        doc = Document(
            page_content="body",
            metadata={
                "url": "https://example.org/vscode-remote",
                "display_name": "vscode-remote",
                "resource_hash": "deadbeef",
                # no "title"
            },
        )
        out = _format_documents_for_llm(_wrap(doc), max_documents=4, max_chars=800)
        # display_name is used as the human-readable citation text...
        assert "vscode-remote" in out
        assert "https://example.org/vscode-remote" in out
        # ...and the hash is NEVER used as the citation/title text (it stays a hash= field).
        assert "(hash=deadbeef)" in out
        assert "[1] deadbeef" not in out

    def test_title_falls_back_to_filename_when_no_title_or_display_name(self):
        doc = Document(
            page_content="body",
            metadata={
                "url": "https://example.org/page",
                "filename": "page.md",
                "resource_hash": "h9",
            },
        )
        out = _format_documents_for_llm(_wrap(doc), max_documents=4, max_chars=800)
        assert "page.md" in out
        assert "https://example.org/page" in out


class TestMatchedSpan:
    """The snippet shows the passage that ranked the document, not its opening."""

    PREFIX = "A" * 1000
    PASSAGE = "Large Data Transfer with Globus: start a Globus transfer."
    TEXT = PREFIX + PASSAGE + "B" * 100

    def _doc(self, **span):
        metadata = {"title": "FASSE transfers", "url": "https://example.org/f"}
        metadata.update(span)
        return Document(page_content=self.TEXT, metadata=metadata)

    def test_span_beyond_the_old_window_is_rendered(self):
        start = self.TEXT.index(self.PASSAGE)
        doc = self._doc(matched_start=start, matched_end=start + len(self.PASSAGE))

        out = _format_documents_for_llm(_wrap(doc), max_documents=4, max_chars=800)

        assert self.PASSAGE in out
        assert "AAAA" not in out
        assert "BBBB" not in out

    def test_no_span_renders_the_start(self):
        out = _format_documents_for_llm(
            _wrap(self._doc()), max_documents=4, max_chars=800
        )

        assert ("A" * 800 + "...") in out
        assert self.PASSAGE not in out

    @pytest.mark.parametrize(
        "span",
        [
            pytest.param({"matched_start": 1000, "matched_end": 10_000}, id="past-end"),
            pytest.param({"matched_start": -1, "matched_end": 50}, id="negative"),
            pytest.param({"matched_start": 1000, "matched_end": 1000}, id="empty"),
            pytest.param({"matched_start": 1050, "matched_end": 1000}, id="reversed"),
            pytest.param({"matched_start": 1000.0, "matched_end": 1050.0}, id="floats"),
            pytest.param({"matched_start": True, "matched_end": 50}, id="bool"),
            pytest.param({"matched_start": 1000}, id="end-missing"),
        ],
    )
    def test_invalid_span_renders_the_start(self, span):
        out = _format_documents_for_llm(
            _wrap(self._doc(**span)), max_documents=4, max_chars=800
        )

        assert ("A" * 800 + "...") in out
