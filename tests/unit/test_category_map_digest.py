"""The category-map digest must bind a per-category slice to the map a run used.

The corpus fingerprint never hashes ``documents.extra_json``, where ``category``
lives, so a metadata-only change moves the URL -> category map at a constant
fingerprint (#524). These helpers record that map per arm: one canonical,
escaped record per document, sorted so the digest does not depend on row order
(#538 rule 5), and rendered as the exact text that is both hashed and written
to the per-arm snapshot file.
"""

import hashlib

import pytest

from src.utils.benchmark_provenance import (
    canonical_source_url,
    category_map_digest,
    category_map_records,
    category_map_text,
    prompt_text_sha256,
)

KB = "https://docs.rc.fas.harvard.edu/kb"


# --- canonical_source_url: the PR #106 rule, shared by matching and the digest ---


def test_strips_one_trailing_slash_from_a_kb_path():
    assert canonical_source_url(f"{KB}/running-jobs/") == f"{KB}/running-jobs"


def test_root_path_keeps_its_slash():
    assert (
        canonical_source_url("https://docs.rc.fas.harvard.edu/")
        == "https://docs.rc.fas.harvard.edu/"
    )


def test_strips_surrounding_whitespace():
    assert canonical_source_url(f"  {KB}/storage  ") == f"{KB}/storage"


def test_query_and_fragment_are_kept():
    assert canonical_source_url(f"{KB}/storage/?a=/b/#x") == f"{KB}/storage?a=/b/#x"


def test_case_is_kept():
    assert canonical_source_url(f"{KB}/Running-Jobs/") == f"{KB}/Running-Jobs"


def test_non_string_input_is_rendered():
    assert canonical_source_url(42) == "42"


# --- records and digest ---------------------------------------------------------


def test_record_is_canonical_url_tab_category():
    assert category_map_records([(f"{KB}/storage/", "Storage")]) == [
        f"{KB}/storage\tStorage"
    ]


def test_missing_category_is_an_empty_field():
    assert category_map_records([(f"{KB}/storage", None)]) == [f"{KB}/storage\t"]


def test_document_without_url_contributes_no_record():
    assert category_map_records([(None, "Storage"), ("", "Storage")]) == []


def test_records_are_sorted():
    rows = [(f"{KB}/b", "B"), (f"{KB}/a", "A")]
    assert category_map_records(rows) == [f"{KB}/a\tA", f"{KB}/b\tB"]


def test_duplicate_urls_are_kept_as_separate_records():
    rows = [(f"{KB}/a", "A"), (f"{KB}/a/", "B")]
    assert category_map_records(rows) == [f"{KB}/a\tA", f"{KB}/a\tB"]


@pytest.mark.parametrize(
    "category, escaped",
    [("a\tb", "a%09b"), ("a\nb", "a%0Ab"), ("a\rb", "a%0Db"), ("10%", "10%25")],
)
def test_separators_inside_a_value_are_escaped(category, escaped):
    [record] = category_map_records([(f"{KB}/a", category)])
    assert record == f"{KB}/a\t{escaped}"
    assert record.count("\t") == 1 and "\n" not in record


def test_text_is_newline_joined_records():
    records = category_map_records([(f"{KB}/a", "A"), (f"{KB}/b", "B")])
    assert category_map_text(records) == f"{KB}/a\tA\n{KB}/b\tB"


def test_digest_is_sha256_of_the_text():
    records = category_map_records([(f"{KB}/a", "A")])
    expected = hashlib.sha256(category_map_text(records).encode("utf-8")).hexdigest()
    assert category_map_digest(records) == f"sha256:{expected}"


def test_row_order_does_not_change_the_digest():
    rows = [(f"{KB}/a", "A"), (f"{KB}/b", "B"), (f"{KB}/c", None)]
    assert category_map_digest(category_map_records(rows)) == category_map_digest(
        category_map_records(list(reversed(rows)))
    )


def test_a_category_change_moves_the_digest():
    before = category_map_records([(f"{KB}/a", "Storage")])
    after = category_map_records([(f"{KB}/a", "Software")])
    assert category_map_digest(before) != category_map_digest(after)


def test_empty_map_has_a_stable_digest():
    empty = hashlib.sha256(b"").hexdigest()
    assert category_map_digest(category_map_records([])) == f"sha256:{empty}"


# --- prompt_text_sha256: the prompt as load_agent_spec reads it -----------------


def test_prompt_digest_hashes_the_text_load_agent_spec_parses(tmp_path):
    prompt = tmp_path / "arm.md"
    prompt.write_bytes(b"---\nname: x\n---\r\nbody\r\n")

    expected = hashlib.sha256(prompt.read_text().encode("utf-8")).hexdigest()
    assert prompt_text_sha256(prompt) == expected


def test_prompt_digest_is_none_without_a_path():
    assert prompt_text_sha256(None) is None


def test_prompt_digest_of_an_unreadable_file_is_a_marker(tmp_path):
    digest = prompt_text_sha256(tmp_path / "missing.md")
    assert digest.startswith("<unavailable:")
