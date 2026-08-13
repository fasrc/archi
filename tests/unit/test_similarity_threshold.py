"""
Tests for the similarity floor helpers.

`normalize_similarity_threshold` decides whether a configured
`similarity_score_reference` is a usable floor at all. Two configured values mean
"no floor": one greater than 1.0 (a distance ceiling left over from the retired
convention, which would otherwise filter everything), and one at or below 0.0
(the shipped default, which must filter nothing). Both are reported as `None` --
an explicit "disabled" -- rather than as the number 0.0, because a cosine
similarity is `1.0 - distance` over a 0..2 distance and so runs to -1.0: a 0.0
floor silently drops anti-correlated sources, and with best-first ordering it
drops every source after them too.

`order_and_filter_by_similarity` applies that floor. It is the seam that keeps
the ordering and sentinel rules testable, since they cannot be exercised through
the Flask app.
"""

import logging

import pytest

from src.interfaces.chat_app.similarity_threshold import (
    normalize_similarity_threshold,
    order_and_filter_by_similarity,
)


class _Doc:
    """Minimal stand-in for a LangChain Document."""

    def __init__(self, name):
        self.metadata = {"display_name": name}
        self.page_content = name

    def __repr__(self):
        return f"_Doc({self.metadata['display_name']!r})"


class TestNormalizeSimilarityThreshold:

    def test_distance_era_value_disables_the_floor(self):
        """A value of 10 (old distance ceiling) cannot be a floor; it is disabled."""
        assert normalize_similarity_threshold(10) is None

    def test_distance_era_value_logs_warning(self, caplog):
        """A value > 1.0 emits a warning that names the configured value."""
        with caplog.at_level(
            logging.WARNING, logger="src.interfaces.chat_app.similarity_threshold"
        ):
            normalize_similarity_threshold(10)
        assert any("10" in record.message for record in caplog.records)
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    def test_shipped_default_disables_the_floor(self):
        """0.0 is the shipped default and must filter nothing at all.

        Returning the number 0.0 would make it a real floor: cosine similarity is
        `1.0 - distance` over a 0..2 distance, so it reaches -1.0, and a source
        scoring below zero would be dropped along with every source after it.
        """
        assert normalize_similarity_threshold(0.0) is None

    def test_shipped_default_logs_no_warning(self, caplog):
        """The default is a normal configuration, not a misconfiguration."""
        with caplog.at_level(
            logging.WARNING, logger="src.interfaces.chat_app.similarity_threshold"
        ):
            normalize_similarity_threshold(0.0)
        assert not caplog.records

    def test_negative_value_disables_the_floor(self):
        """Anything at or below zero reads as 'off', not as a very low floor."""
        assert normalize_similarity_threshold(-0.5) is None

    def test_unset_value_disables_the_floor(self):
        """A config that omits the key entirely must not crash the comparison."""
        assert normalize_similarity_threshold(None) is None

    def test_exact_one_is_applied_unchanged(self):
        """1.0 is a strict but valid floor and is returned without substitution."""
        assert normalize_similarity_threshold(1.0) == 1.0

    def test_exact_one_no_warning(self, caplog):
        """1.0 does not trigger a warning."""
        with caplog.at_level(
            logging.WARNING, logger="src.interfaces.chat_app.similarity_threshold"
        ):
            normalize_similarity_threshold(1.0)
        assert not caplog.records

    def test_typical_floor_unchanged(self):
        """0.3 is a normal threshold and is returned unchanged."""
        assert normalize_similarity_threshold(0.3) == pytest.approx(0.3)

    def test_typical_floor_no_warning(self, caplog):
        """0.3 does not trigger a warning."""
        with caplog.at_level(
            logging.WARNING, logger="src.interfaces.chat_app.similarity_threshold"
        ):
            normalize_similarity_threshold(0.3)
        assert not caplog.records

    def test_just_above_one_is_disabled(self):
        """A value of 1.0001 is above the boundary and disables the floor."""
        assert normalize_similarity_threshold(1.0001) is None


class TestOrderAndFilterBySimilarity:

    def test_orders_descending(self):
        low, high = _Doc("low.md"), _Doc("high.md")
        result = order_and_filter_by_similarity([low, high], [0.3, 0.9], None)
        assert [doc for _, doc in result] == [high, low]

    def test_no_scores_yields_documents_in_order_with_none(self):
        a, b = _Doc("a.md"), _Doc("b.md")
        result = order_and_filter_by_similarity([a, b], [], None)
        assert result == [(None, a), (None, b)]

    def test_disabled_floor_keeps_negative_scores(self):
        """A disabled floor must keep anti-correlated sources, not stop at them.

        This is the shipped default, so `0.0` reaching here as a number rather
        than as `None` would silently truncate the citation list.
        """
        good, bad = _Doc("good.md"), _Doc("bad.md")
        result = order_and_filter_by_similarity([good, bad], [0.4, -0.2], None)
        assert [doc for _, doc in result] == [good, bad]

    def test_floor_stops_at_the_first_source_below_it(self):
        best, weak = _Doc("best.md"), _Doc("weak.md")
        result = order_and_filter_by_similarity([best, weak], [0.9, 0.1], 0.3)
        assert [doc for _, doc in result] == [best]

    def test_sentinel_survives_a_below_floor_score(self):
        """`-1.0` means 'no score available' and bypasses the floor entirely.

        Descending order puts the sentinel last, so a `break` on the first weak
        real score reaches it and drops it -- even though it was never eligible
        for threshold filtering. Scores [0.9, 0.1, -1.0] with a 0.3 floor must
        keep the 0.9 source and the sentinel, and drop only the 0.1.
        """
        best, weak, unscored = _Doc("best.md"), _Doc("weak.md"), _Doc("unscored.md")
        result = order_and_filter_by_similarity(
            [best, weak, unscored], [0.9, 0.1, -1.0], 0.3
        )
        assert [doc for _, doc in result] == [best, unscored]

    def test_sentinels_sort_after_every_real_score(self):
        unscored, low = _Doc("unscored.md"), _Doc("low.md")
        result = order_and_filter_by_similarity([unscored, low], [-1.0, 0.2], None)
        assert [doc for _, doc in result] == [low, unscored]

    def test_only_sentinels_are_all_kept_under_a_floor(self):
        a, b = _Doc("a.md"), _Doc("b.md")
        result = order_and_filter_by_similarity([a, b], [-1.0, -1.0], 0.9)
        assert [doc for _, doc in result] == [a, b]
