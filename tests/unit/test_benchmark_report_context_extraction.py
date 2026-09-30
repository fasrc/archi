"""Unit tests for the shared retrieved-context text extraction.

``extract_context_text`` is the one place both report formatters parse a
LangChain ``Document`` repr string, so a parsing fix lands in both formats at
once. These tests pin the fallback behavior for shapes the slicer does not
understand: usable text always comes back, never an exception.
"""

from __future__ import annotations

from src.utils.generate_benchmark_report import (
    extract_context_text,
    format_html_output,
    format_markdown_output,
)

_CONFIG = {"services": {"benchmarking": {"modes": ["RAGAS"]}}}


def test_parses_document_repr():
    ctx = "page_content='Slurm docs body' metadata={'source': 'x'}"
    assert extract_context_text(ctx) == "Slurm docs body"


def test_single_quotes_inside_content_cut_at_first_metadata_boundary():
    """Documented fragility: the slicer cuts at the first quote+metadata
    boundary. Content with an embedded quote still yields usable text."""
    ctx = "page_content='He said 'hi'' metadata={'source': 'x'}"
    assert extract_context_text(ctx) == "He said 'hi'"


def test_double_quoted_repr_falls_back_to_the_raw_string():
    ctx = 'page_content="double quoted" metadata={}'
    assert extract_context_text(ctx) == ctx


def test_plain_string_passes_through():
    assert extract_context_text("just text") == "just text"


def test_non_string_is_stringified():
    assert extract_context_text(42) == "42"


def test_both_formatters_render_the_same_extracted_text():
    row = {
        "question": "q?",
        "answer": "a",
        "reference_answer": "r",
        "sources_metadata": [{"display_name": "slurm"}],
        "contexts": ["page_content='Shared context body' metadata={'s': 1}"],
    }

    html = format_html_output(_CONFIG, "bench", "2026-08-28", {"q1": row}, {})
    md = format_markdown_output(_CONFIG, "bench", "2026-08-28", {"q1": row}, {})

    # The parsed page text renders in both formats; the repr scaffolding the
    # slicer strips does not leak into either.
    assert "Shared context body" in html
    assert "Shared context body" in md
    assert "metadata={'s': 1}" not in html
    assert "metadata={'s': 1}" not in md
