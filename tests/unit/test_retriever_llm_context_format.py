"""
Unit tests for ``_format_documents_for_llm`` — the retriever context the LLM
sees during tool calls.

This is the snippet header the model copies when it cites a source. For the
agent to emit an inline ``[title](url)`` hyperlink instead of a bare ``[1]``,
the per-snippet header must surface the document ``title`` and ``url`` (while
still carrying the ``resource_hash`` the document-fetch tool needs).
"""

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
