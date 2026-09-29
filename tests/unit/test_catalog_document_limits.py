"""Tests for the server-side catalog document character clamp (issue #260)."""

from src.interfaces.uploader_app.document_limits import (
    DEFAULT_CATALOG_DOCUMENT_CHARS,
    MAX_CATALOG_DOCUMENT_CHARS,
    clamp_document_chars,
)

DOC = "".join(chr(ord("a") + i % 26) for i in range(10000))


def test_ceiling_wins_over_a_large_requested_value():
    result = DOC[: clamp_document_chars("999999")]
    assert len(result) <= 6000
    assert len(result) == MAX_CATALOG_DOCUMENT_CHARS
    result = DOC[: clamp_document_chars(999999)]
    assert len(result) <= 6000
    assert len(result) == MAX_CATALOG_DOCUMENT_CHARS


def test_zero_means_the_ceiling_not_the_whole_document():
    result = DOC[: clamp_document_chars("0")]
    assert len(result) <= 6000
    result = DOC[: clamp_document_chars(0)]
    assert len(result) <= 6000


def test_negative_means_the_ceiling_and_never_slices_from_the_tail():
    result = DOC[: clamp_document_chars("-5")]
    assert len(result) <= 6000
    assert result == DOC[: len(result)]
    result = DOC[: clamp_document_chars(-5)]
    assert len(result) <= 6000
    assert result == DOC[: len(result)]


def test_a_valid_smaller_value_is_honoured_exactly():
    result = DOC[: clamp_document_chars("100")]
    assert result == DOC[:100]
    result = DOC[: clamp_document_chars(100)]
    assert result == DOC[:100]


def test_absent_value_falls_back_to_the_4000_default():
    result = DOC[: clamp_document_chars(None)]
    assert result == DOC[:4000]
    assert DEFAULT_CATALOG_DOCUMENT_CHARS == 4000


def test_malformed_values_fall_back_to_the_ceiling():
    assert len(DOC[: clamp_document_chars("abc")]) <= 6000
    assert len(DOC[: clamp_document_chars("")]) <= 6000
    assert len(DOC[: clamp_document_chars(True)]) <= 6000


def test_max_catalog_document_chars_is_6000():
    assert MAX_CATALOG_DOCUMENT_CHARS == 6000
