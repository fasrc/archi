"""Unit tests for the chunk-overlap measurement helper.

Covers scripts/benchmarking/measure_chunk_overlap.py — specifically the
suffix/prefix matcher that decides how much text a splitter actually carried
across a chunk boundary.

The naive way to measure this (compare token-id sequences) silently reports
zero overlap whenever the splitter cuts mid-token: a boundary falling inside a
URL leaves ``https://github.`` at the end of one chunk and ``com/fasrc/...`` at
the start of the next, which re-tokenizes differently on each side even though
the underlying text plainly repeats. These tests pin the character-level,
whitespace-normalized behavior that avoids that artifact.
"""

import pytest

from scripts.benchmarking.measure_chunk_overlap import (
    longest_overlap_chars,
    normalize_whitespace,
    overlap_text,
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
