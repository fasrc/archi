"""Unit tests for the chunk-overlap measurement helper.

Covers scripts/benchmarking/measure_chunk_overlap.py. The script must measure
the chunking the ingest really does, so the tests pin three things:

* the leaf chunks come from the production parent/child hierarchy
  (``HierarchicalNodeParser``, one overlap budget at both levels), byte for
  byte identical to ``build_hierarchical_nodes`` at its fixed budget;
* the text carried across a boundary is read from the splitter's own source
  offsets, so a coincidental shared letter between adjacent chunks counts as
  nothing; the whitespace-normalized string matcher is only the fallback for a
  chunk the splitter did not emit verbatim (it drops repeated punctuation when
  it has to cut a sentence longer than the chunk budget);
* the summary statistics use nearest-rank percentiles.
"""

import pytest
from langchain_core.documents import Document

from scripts.benchmarking import measure_chunk_overlap as mco
from scripts.benchmarking.measure_chunk_overlap import (
    Chunk,
    Record,
    attach_source_text,
    carried_chars,
    load_records,
    longest_overlap_chars,
    normalize_whitespace,
    overlap_text,
    percentile,
    place_chunks,
    reproduced_children,
    split_documents,
    split_records,
    summarize_boundaries,
    sweep_budgets,
)


def _prose(count: int, seed: str = "") -> str:
    return " ".join(
        f"Sentence {seed}{i} explains how the cluster schedules its {i * 3} jobs."
        for i in range(count)
    )


class TestNormalizeWhitespace:
    def test_collapses_runs_to_single_spaces(self):
        assert normalize_whitespace("a  \n\n b\tc") == "a b c"

    def test_strips_ends(self):
        assert normalize_whitespace("  padded  ") == "padded"

    def test_empty_stays_empty(self):
        assert normalize_whitespace("   ") == ""


class TestLongestOverlapChars:
    def test_no_shared_text_is_zero(self):
        assert longest_overlap_chars("alpha beta", "gamma delta") == 0

    def test_exact_tail_head_repeat(self):
        # b opens with the last 11 chars of a ("second one").
        a = "first part second one"
        b = "second one third part"
        assert longest_overlap_chars(a, b) == len("second one")

    def test_whitespace_differences_do_not_hide_overlap(self):
        a = "the end of\n\n\tthe chunk"
        b = "the   end of the chunk and more"
        assert longest_overlap_chars(a, b) == len("the end of the chunk")

    def test_mid_token_split_is_still_detected(self):
        # The URL-boundary case that defeats token-sequence comparison.
        a = "see [Knitro](https://github."
        b = "com/fasrc/User_Codes) for details"
        assert longest_overlap_chars(a, b) == 0
        a2 = "prefix https://github.com/fasrc/User_Codes"
        b2 = "https://github.com/fasrc/User_Codes suffix"
        assert longest_overlap_chars(a2, b2) == len(
            "https://github.com/fasrc/User_Codes"
        )

    def test_full_containment_is_capped_at_shorter_string(self):
        assert longest_overlap_chars("repeat", "repeat") == len("repeat")

    def test_empty_inputs_are_zero(self):
        assert longest_overlap_chars("", "anything") == 0
        assert longest_overlap_chars("anything", "") == 0

    def test_does_not_match_across_the_separator(self):
        # A regression guard for the KMP sentinel: a suffix of `a` must not be
        # allowed to pair with a prefix of `b` by running through the joiner.
        assert longest_overlap_chars("abc", "xyz") == 0

    @pytest.mark.parametrize("size", [1, 2, 50])
    def test_identical_strings_of_various_sizes(self, size):
        s = "ab" * size
        assert longest_overlap_chars(s, s) == len(s)


class TestOverlapText:
    def test_returns_the_shared_span(self):
        a = "lead in shared tail"
        b = "shared tail then more"
        assert overlap_text(a, b) == "shared tail"

    def test_returns_empty_when_nothing_shared(self):
        assert overlap_text("alpha", "beta") == ""


class TestCarriedChars:
    def test_reads_the_overlap_from_source_offsets(self):
        a = Chunk(text="alpha beta gamma", document=0, start=0, end=16)
        b = Chunk(text="gamma delta", document=0, start=11, end=22)
        assert carried_chars(a, b) == len("gamma")

    def test_adjacent_chunks_carry_nothing_even_when_letters_coincide(self):
        # "cats" | "snake": the string matcher infers a 1-char overlap ("s"),
        # the offsets know the splitter copied nothing.
        a = Chunk(text="cats", document=0, start=0, end=4)
        b = Chunk(text="snake", document=0, start=5, end=10)
        assert longest_overlap_chars(a.text, b.text) == 1
        assert carried_chars(a, b) == 0

    def test_falls_back_to_string_matching_when_an_offset_is_unknown(self):
        a = Chunk(text="lead in shared tail", document=0, start=None, end=None)
        b = Chunk(text="shared tail then more", document=0, start=8, end=29)
        assert carried_chars(a, b) == len("shared tail")


def _word_tokenizer(text: str):
    return text.split()


class TestPlaceChunks:
    def test_places_contiguous_chunks_by_their_document_offsets(self):
        doc = "A one. B two. C three."
        chunks = place_chunks(
            doc,
            ["A one. B two.", "C three."],
            document=3,
            budget=0,
            tokenizer=_word_tokenizer,
        )
        assert [(c.start, c.end, c.document) for c in chunks] == [
            (0, 13, 3),
            (14, 22, 3),
        ]
        assert all(doc[c.start : c.end] == c.text for c in chunks)

    def test_accepts_an_overlap_within_the_budget(self):
        doc = "A one. B two. C three."
        chunks = place_chunks(
            doc,
            ["A one. B two.", "B two. C three."],
            document=0,
            budget=2,
            tokenizer=_word_tokenizer,
        )
        assert [(c.start, c.end) for c in chunks] == [(0, 13), (7, 22)]
        assert carried_chars(chunks[0], chunks[1]) == len("B two.")

    def test_repeated_page_text_is_not_mistaken_for_overlap(self):
        # A page repeated three times, chunks two periods long, no overlap: the
        # first occurrence of chunk 2 after chunk 1 *starts* is inside chunk 1,
        # but would imply carrying 4 tokens against a budget of 0.
        period = "one two three four. "
        doc = period * 6
        two = (period * 2).strip()
        chunks = place_chunks(
            doc, [two, two, two], document=0, budget=0, tokenizer=_word_tokenizer
        )
        assert [(c.start, c.end) for c in chunks] == [(0, 39), (40, 79), (80, 119)]
        assert [carried_chars(a, b) for a, b in zip(chunks, chunks[1:])] == [0, 0]

    def test_a_chunk_may_start_before_a_short_remainder_chunk(self):
        # At a parent join the parent-level overlap can be longer than the
        # previous parent's last (remainder) child, so the following chunk
        # starts before the previous chunk starts. Only its end must advance.
        doc = "A one. B two. C three. D four."
        chunks = place_chunks(
            doc,
            ["A one. B two.", "C three.", "B two. C three. D four."],
            document=0,
            budget=4,
            tokenizer=_word_tokenizer,
        )
        assert [(c.start, c.end) for c in chunks] == [(0, 13), (14, 22), (7, 30)]
        assert carried_chars(chunks[1], chunks[2]) == len("B two. C three.")

    def test_a_repeat_within_the_budget_is_placed_where_the_tiling_requires(self):
        # Both occurrences of the second chunk satisfy the local rules (the
        # first implies a 2-token overlap, within budget), but only the second
        # lets the chunks tile the document without leaving text uncovered.
        doc = "abc XYZ abc XYZ"
        chunks = place_chunks(
            doc, ["abc XYZ", "abc XYZ"], document=0, budget=2, tokenizer=_word_tokenizer
        )
        assert [(c.start, c.end) for c in chunks] == [(0, 7), (8, 15)]
        assert carried_chars(chunks[0], chunks[1]) == 0

    def test_a_repeated_block_before_new_text_is_not_taken_as_overlap(self):
        # A navigation block rendered twice, shorter than the budget: the second
        # chunk starts at the second copy, because the first copy would leave
        # the second one uncovered.
        doc = "MENU A. MENU A. body one. body two."
        chunks = place_chunks(
            doc,
            ["MENU A. MENU A.", "MENU A. body one.", "body two."],
            document=0,
            budget=2,
            tokenizer=_word_tokenizer,
        )
        assert [(c.start, c.end) for c in chunks] == [(0, 15), (8, 25), (26, 35)]
        assert [carried_chars(a, b) for a, b in zip(chunks, chunks[1:])] == [
            len("MENU A."),
            0,
        ]

    def test_many_repeats_are_still_placed_consecutively(self):
        # 15 copies of the same chunk with a one-token budget: every copy
        # admits two positions locally, so a bounded search would give up and a
        # greedy one would stack them all at offset 0.
        doc = " ".join(["abc"] * 15)
        chunks = place_chunks(
            doc, ["abc"] * 15, document=0, budget=1, tokenizer=_word_tokenizer
        )
        assert [c.start for c in chunks] == [4 * i for i in range(15)]
        assert all(carried_chars(a, b) == 0 for a, b in zip(chunks, chunks[1:]))

    def test_reports_whether_the_chunks_tiled_the_document(self):
        from scripts.benchmarking.measure_chunk_overlap import tile_chunks

        doc = "A one. B two. C three."
        assert tile_chunks(
            doc, ["A one. B two.", "C three."], budget=0, tokenizer=_word_tokenizer
        ) == [0, 14]
        # a truncated chunk list leaves text uncovered: no tiling exists
        assert (
            tile_chunks(doc, ["A one. B two."], budget=0, tokenizer=_word_tokenizer)
            is None
        )

    def test_a_chunk_made_only_of_copied_text_ends_where_the_previous_one_ends(self):
        # When the first new sentence of a parent does not fit beside the text
        # copied from the previous parent, the splitter emits the copied text
        # alone: a child that is a pure suffix of the previous chunk.
        doc = "A one. B two. C three."
        chunks = place_chunks(
            doc,
            ["A one. B two.", "B two."],
            document=0,
            budget=2,
            tokenizer=_word_tokenizer,
        )
        assert [(c.start, c.end) for c in chunks] == [(0, 13), (7, 13)]
        assert carried_chars(chunks[0], chunks[1]) == len("B two.")

    def test_the_only_occurrence_is_kept_even_when_it_exceeds_the_budget(self):
        # Tokenizing the joined overlap can count differently from the
        # splitter's per-sentence sizes; a verbatim placement beats no placement.
        doc = "A one. B two. C three."
        chunks = place_chunks(
            doc,
            ["A one. B two.", "B two. C three."],
            document=0,
            budget=1,
            tokenizer=_word_tokenizer,
        )
        assert [(c.start, c.end) for c in chunks] == [(0, 13), (7, 22)]

    def test_unplaceable_chunk_has_no_offsets_and_does_not_move_the_cursor(self):
        doc = "A one. B two. C three."
        chunks = place_chunks(
            doc,
            ["A one.", "not in the document", "B two."],
            document=0,
            budget=0,
            tokenizer=_word_tokenizer,
        )
        assert (chunks[1].start, chunks[1].end) == (None, None)
        assert (chunks[2].start, chunks[2].end) == (7, 13)


class TestPercentile:
    def test_nearest_rank_at_a_multiple_of_ten_is_not_the_maximum(self):
        assert percentile(list(range(1, 11)), 0.9) == 9

    def test_odd_count_rounds_the_rank_up(self):
        assert percentile([1, 2, 3, 4, 5], 0.9) == 5

    def test_single_value(self):
        assert percentile([7], 0.9) == 7

    def test_empty_is_zero(self):
        assert percentile([], 0.9) == 0

    def test_fraction_one_is_the_maximum(self):
        assert percentile([3, 1, 2], 1.0) == 3


class TestSummarizeBoundaries:
    def test_counts_and_statistics(self):
        carried = [0, 0, 5, 10, 15, 20, 25, 30, 35, 40]
        summary = summarize_boundaries(carried)
        assert summary["boundaries"] == 10
        assert summary["empty_boundaries"] == 2
        assert summary["empty_pct"] == 20.0
        assert summary["mean_tokens"] == 18.0
        assert summary["median_tokens"] == 17.5
        assert summary["p90_tokens"] == 35

    def test_no_boundaries_is_all_zero(self):
        summary = summarize_boundaries([])
        assert summary["boundaries"] == 0
        assert summary["empty_pct"] == 0.0
        assert summary["p90_tokens"] == 0


class TestSplitRecords:
    def test_nul_separates_documents_and_blank_records_are_dropped(self):
        assert split_records("doc one\x00\x00doc two\x00   ") == ["doc one", "doc two"]

    def test_text_without_nul_is_one_document(self):
        assert split_records("just one\n\ndocument") == ["just one\n\ndocument"]


class TestLoadRecords:
    def test_json_lines_carry_text_metadata_and_stored_children(self):
        dump = (
            '{"text": "Doc one.", "metadata": {"source": "/a.md"}, '
            '"children": ["Doc one."]}\n'
            "\n"
            '{"text": "Doc two.", "metadata": {"source": "/b.pdf", "page": 3}}\n'
        )
        records = load_records(dump)
        assert records == [
            Record(
                text="Doc one.", metadata={"source": "/a.md"}, children=["Doc one."]
            ),
            Record(
                text="Doc two.", metadata={"source": "/b.pdf", "page": 3}, children=None
            ),
        ]

    def test_json_lines_without_metadata_get_an_empty_mapping(self):
        assert load_records('{"text": "Doc."}') == [
            Record(text="Doc.", metadata={}, children=None)
        ]

    def test_plain_text_falls_back_to_nul_separated_documents(self):
        assert load_records("doc one\x00doc two") == [
            Record(text="doc one", metadata={}, children=None),
            Record(text="doc two", metadata={}, children=None),
        ]

    def test_blank_json_text_is_dropped(self):
        assert load_records('{"text": "   "}\n{"text": "kept"}') == [
            Record(text="kept", metadata={}, children=None)
        ]


class _Doc:
    def __init__(self, text, metadata):
        self.page_content = text
        self.metadata = metadata


class TestAttachSourceText:
    def test_replaces_text_and_metadata_with_the_loader_document(self, tmp_path):
        (tmp_path / "websites").mkdir()
        (tmp_path / "websites" / "a.md").write_text(
            "original  text\n", encoding="utf-8"
        )
        records = [
            Record(
                text="rejoined text",
                metadata={"source": "/root/data/websites/a.md"},
                path="websites/a.md",
            )
        ]
        loaded = lambda path: [_Doc("original  text\n", {"source": str(path)})]
        attached, count = attach_source_text(records, tmp_path, load=loaded)
        assert count == 1
        assert attached[0].text == "original  text\n"
        # The loader stamps the host path; the stored production path is kept so
        # the metadata token count matches what production subtracted.
        assert attached[0].metadata == {"source": "/root/data/websites/a.md"}
        assert attached[0].path == "websites/a.md"

    def test_matches_a_pdf_page_by_its_page_metadata(self, tmp_path):
        (tmp_path / "doc.pdf").write_bytes(b"%PDF-stub")
        records = [
            Record(
                text="page two rejoined",
                metadata={"source": "/d/doc.pdf", "page": 1},
                path="doc.pdf",
                children=["x"],
            )
        ]
        loaded = lambda path: [
            _Doc("page one", {"source": str(path), "page": 0}),
            _Doc("page two", {"source": str(path), "page": 1}),
        ]
        attached, count = attach_source_text(records, tmp_path, load=loaded)
        assert count == 1
        assert attached[0].text == "page two"
        assert attached[0].metadata["page"] == 1
        assert attached[0].children == ["x"]

    def test_keeps_the_record_when_the_file_or_page_is_missing(self, tmp_path):
        (tmp_path / "present.md").write_text("x", encoding="utf-8")
        records = [
            Record(text="kept", metadata={}, path="missing.md"),
            Record(text="kept too", metadata={"page": 7}, path="present.md"),
            Record(text="no path", metadata={}),
        ]
        loaded = lambda path: [_Doc("page zero", {"page": 0})]
        attached, count = attach_source_text(records, tmp_path, load=loaded)
        assert count == 0
        assert [r.text for r in attached] == ["kept", "kept too", "no path"]

    def test_a_file_is_loaded_once_for_all_its_pages(self, tmp_path):
        (tmp_path / "doc.pdf").write_bytes(b"%PDF-stub")
        records = [
            Record(text="p0", metadata={"page": 0}, path="doc.pdf"),
            Record(text="p1", metadata={"page": 1}, path="doc.pdf"),
            Record(text="p2", metadata={"page": 2}, path="doc.pdf"),
        ]
        calls = []

        def loaded(path):
            calls.append(path)
            return [_Doc(f"page {n}", {"page": n}) for n in range(3)]

        attached, count = attach_source_text(records, tmp_path, load=loaded)
        assert count == 3
        assert len(calls) == 1
        assert [r.text for r in attached] == ["page 0", "page 1", "page 2"]

    def test_default_loader_is_the_ingest_loader(self, tmp_path):
        (tmp_path / "notes.txt").write_text("plain text file\n", encoding="utf-8")
        records = [Record(text="rejoined", metadata={}, path="notes.txt")]
        attached, count = attach_source_text(records, tmp_path)
        assert count == 1
        assert attached[0].text == "plain text file\n"
        assert attached[0].metadata == {"source": str(tmp_path / "notes.txt")}


class TestSweepBudgets:
    def test_reports_the_effective_budget_and_collapses_duplicates(self):
        # 600 clamps to the child size; 64 and 128 both clamp to a 48-token child.
        assert sweep_budgets(
            [128, 600, 64], chunk_size=512, parent_chunk_size=2048
        ) == [
            (64, 64),
            (128, 128),
            (600, 512),
        ]
        assert sweep_budgets([64, 128], chunk_size=48, parent_chunk_size=2048) == [
            (64, 48)
        ]

    def test_negative_requests_measure_zero(self):
        assert sweep_budgets([-5, 0], chunk_size=512, parent_chunk_size=2048) == [
            (-5, 0)
        ]


class TestEmbeddedText:
    def test_nul_bytes_are_removed_like_the_ingest_does(self):
        from scripts.benchmarking.measure_chunk_overlap import embedded_text

        assert embedded_text("ab\x00c") == "abc"

    def test_parity_and_fallback_matching_ignore_nul_bytes(self):
        # Placement keeps the raw text; token accounting and the parity check
        # see what production stored, with every NUL removed.
        chunks = [Chunk(text="al\x00pha", document=0, start=None, end=None)]
        records = [Record(text="al\x00pha", children=["alpha"])]
        assert reproduced_children(records, chunks) == (1, 1)
        a = Chunk(text="lead in shared\x00 tail", document=0, start=None, end=None)
        b = Chunk(text="shared tail then more", document=0, start=None, end=None)
        assert carried_chars(a, b) == len("shared tail")


class TestReproducedChildren:
    def test_counts_stored_children_the_split_reproduces(self):
        chunks = [
            Chunk(text="alpha", document=0, start=0, end=5),
            Chunk(text="beta", document=0, start=6, end=10),
            Chunk(text="gamma", document=1, start=0, end=5),
        ]
        records = [
            Record(text="alpha beta", metadata={}, children=["alpha", "beta", "extra"]),
            Record(text="gamma", metadata={}, children=["different"]),
        ]
        assert reproduced_children(records, chunks) == (2, 4)

    def test_records_without_children_are_not_counted(self):
        records = [Record(text="alpha", metadata={}, children=None)]
        chunks = [Chunk(text="alpha", document=0, start=0, end=5)]
        assert reproduced_children(records, chunks) == (0, 0)


class TestSplitDocuments:
    """Runs the real parser, exactly as the production unit tests do."""

    def test_matches_the_ingest_child_chunks_byte_for_byte(self):
        from src.data_manager.vectorstore.node_parsing import (
            CHILD_CHUNK_OVERLAP,
            build_hierarchical_nodes,
        )

        text = _prose(80)
        expected = [
            child
            for node in build_hierarchical_nodes(
                Document(page_content=text, metadata={}),
                parent_chunk_size=128,
                child_chunk_size=48,
            )
            for child in node.child_texts
        ]
        chunks = split_documents(
            [Record(text=text, metadata={}, children=None)],
            chunk_size=48,
            parent_chunk_size=128,
            overlap=CHILD_CHUNK_OVERLAP,
        )
        assert len(chunks) > 8
        assert [chunk.text for chunk in chunks] == expected

    def test_metadata_shrinks_the_budget_exactly_as_the_ingest_does(self):
        # The splitter subtracts the metadata string's tokens from every level's
        # budget, so the loader metadata production attaches must be replayed.
        from src.data_manager.vectorstore.node_parsing import (
            CHILD_CHUNK_OVERLAP,
            build_hierarchical_nodes,
        )

        text = _prose(80)
        metadata = {"source": "/root/data/websites/1.md", "page": 3, "total_pages": 9}
        expected = [
            child
            for node in build_hierarchical_nodes(
                Document(page_content=text, metadata=metadata),
                parent_chunk_size=128,
                child_chunk_size=64,
            )
            for child in node.child_texts
        ]
        with_metadata = split_documents(
            [Record(text=text, metadata=metadata, children=None)],
            chunk_size=64,
            parent_chunk_size=128,
            overlap=CHILD_CHUNK_OVERLAP,
        )
        without = split_documents(
            [Record(text=text, metadata={}, children=None)],
            chunk_size=64,
            parent_chunk_size=128,
            overlap=CHILD_CHUNK_OVERLAP,
        )
        assert [chunk.text for chunk in with_metadata] == expected
        assert [chunk.text for chunk in without] != expected

    def test_spans_are_verbatim_document_offsets(self):
        text = _prose(60)
        chunks = split_documents(
            [Record(text=text, metadata={}, children=None)],
            chunk_size=48,
            parent_chunk_size=128,
            overlap=16,
        )
        assert len(chunks) > 4
        assert all(text[chunk.start : chunk.end] == chunk.text for chunk in chunks)

    def test_documents_are_split_independently_and_in_order(self):
        docs = [_prose(30, "a"), _prose(30, "b")]
        chunks = split_documents(
            [Record(text=doc) for doc in docs],
            chunk_size=48,
            parent_chunk_size=128,
            overlap=16,
        )
        owners = [chunk.document for chunk in chunks]
        assert set(owners) == {0, 1}
        assert owners == sorted(owners)
        assert all(docs[c.document][c.start : c.end] == c.text for c in chunks)

    def test_offsets_and_string_matching_agree_on_clean_prose(self):
        text = _prose(60)
        chunks = split_documents(
            [Record(text=text, metadata={}, children=None)],
            chunk_size=48,
            parent_chunk_size=128,
            overlap=16,
        )
        pairs = list(zip(chunks, chunks[1:]))
        assert pairs
        assert [carried_chars(a, b) for a, b in pairs] == [
            longest_overlap_chars(a.text, b.text) for a, b in pairs
        ]
        assert any(carried_chars(a, b) > 0 for a, b in pairs)


class TestDefaultsMirrorProduction:
    def test_default_sizes_match_node_parsing(self):
        from src.data_manager.vectorstore import node_parsing

        assert mco.DEFAULT_PARENT_CHUNK_SIZE == node_parsing.DEFAULT_PARENT_CHUNK_SIZE
        assert mco.DEFAULT_CHUNK_SIZE == node_parsing.DEFAULT_CHILD_CHUNK_SIZE
        assert mco.PRODUCTION_OVERLAP == node_parsing.CHILD_CHUNK_OVERLAP
