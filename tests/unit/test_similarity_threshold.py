"""
Tests for normalize_similarity_threshold.

A configured similarity_score_reference > 1.0 cannot be a cosine similarity.
The helper must substitute 0.0 (no floor) and emit a warning naming the value.
Values <= 1.0 are returned unchanged without a warning.
"""

import logging

import pytest

from src.interfaces.chat_app.similarity_threshold import normalize_similarity_threshold


class TestNormalizeSimilarityThreshold:

    def test_distance_era_value_returns_zero(self):
        """A value of 10 (old distance ceiling) is substituted with 0.0."""
        result = normalize_similarity_threshold(10)
        assert result == 0.0

    def test_distance_era_value_logs_warning(self, caplog):
        """A value > 1.0 emits a warning that names the configured value."""
        with caplog.at_level(
            logging.WARNING, logger="src.interfaces.chat_app.similarity_threshold"
        ):
            normalize_similarity_threshold(10)
        assert any("10" in record.message for record in caplog.records)
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    def test_distance_era_value_no_floor_applied(self):
        """After substitution to 0.0 the returned value acts as no floor."""
        result = normalize_similarity_threshold(10)
        # 0.0 floor means every score (including very low ones like 0.01) passes
        assert result == 0.0
        any_low_score = 0.01
        assert any_low_score >= result

    def test_exact_one_is_applied_unchanged(self):
        """1.0 is a strict but valid floor and is returned without substitution."""
        result = normalize_similarity_threshold(1.0)
        assert result == 1.0

    def test_exact_one_no_warning(self, caplog):
        """1.0 does not trigger a warning."""
        with caplog.at_level(
            logging.WARNING, logger="src.interfaces.chat_app.similarity_threshold"
        ):
            normalize_similarity_threshold(1.0)
        assert not caplog.records

    def test_typical_floor_unchanged(self):
        """0.3 is a normal threshold and is returned unchanged."""
        result = normalize_similarity_threshold(0.3)
        assert result == pytest.approx(0.3)

    def test_typical_floor_no_warning(self, caplog):
        """0.3 does not trigger a warning."""
        with caplog.at_level(
            logging.WARNING, logger="src.interfaces.chat_app.similarity_threshold"
        ):
            normalize_similarity_threshold(0.3)
        assert not caplog.records

    def test_zero_unchanged(self):
        """0.0 (default, no floor) is returned unchanged."""
        result = normalize_similarity_threshold(0.0)
        assert result == 0.0

    def test_just_above_one_is_substituted(self):
        """A value of 1.0001 is above the boundary and is substituted with 0.0."""
        result = normalize_similarity_threshold(1.0001)
        assert result == 0.0
