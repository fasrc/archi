"""Catalog search must survive apostrophes in natural-language queries.

``/api/catalog/search`` tokenizes the raw user query. The original implementation
called ``shlex.split`` directly, which treats ``'`` as a quote character. Natural
English breaks it two ways:

* odd number of apostrophes (``can't connect``) -> ``ValueError: No closing
  quotation`` -> HTTP 500 -> the agent's catalog tool fails and it burns its
  recursion budget retrying;
* even number (``don't stop user's job``) -> apostrophes are silently consumed
  and the words between them are fused into a single token
  (``["dont stop users", "job"]``) -> silent retrieval corruption, no error.

Both are user-facing: the chat agent hits this endpoint on every turn. The fix
keeps ``"`` as a grouping quote (so ``key:"two words"`` still works) but makes
``'`` a literal character, and degrades to a plain whitespace split rather than
raising if the double quotes are unbalanced.
"""

from src.utils.catalog_query import parse_metadata_query, tokenize_query


class TestTokenizeQuery:
    def test_apostrophe_contraction_is_one_literal_token(self):
        assert tokenize_query("can't connect") == ["can't", "connect"]

    def test_even_apostrophes_do_not_fuse_words(self):
        # Regression: shlex.split returned ["dont stop users", "job"].
        assert tokenize_query("don't stop user's job") == [
            "don't",
            "stop",
            "user's",
            "job",
        ]

    def test_double_quotes_still_group_a_phrase(self):
        assert tokenize_query('key:"two words" free') == ["key:two words", "free"]

    def test_unbalanced_double_quote_degrades_instead_of_raising(self):
        assert tokenize_query('unbalanced " quote here') == [
            "unbalanced",
            '"',
            "quote",
            "here",
        ]

    def test_hash_is_not_a_comment(self):
        assert tokenize_query("#hashtag topic") == ["#hashtag", "topic"]

    def test_blank_and_whitespace_only(self):
        assert tokenize_query("") == []
        assert tokenize_query("   ") == []


class TestParseMetadataQuery:
    def test_apostrophe_query_yields_no_filters_and_intact_free_text(self):
        filters, free = parse_metadata_query("Jupyter job stuck can't connect")
        assert filters == {}
        assert free == "Jupyter job stuck can't connect"

    def test_single_filter_group(self):
        filters, free = parse_metadata_query("source_type:kb quota")
        assert filters == {"source_type": "kb"}
        assert free == "quota"

    def test_or_splits_into_filter_groups(self):
        filters, free = parse_metadata_query("source_type:kb OR ticket_id:123")
        assert filters == [{"source_type": "kb"}, {"ticket_id": "123"}]
        assert free == ""

    def test_legacy_keys_are_aliased_to_canonical_columns(self):
        filters, _ = parse_metadata_query("resource_type:kb resource_id:7")
        assert filters == {"source_type": "kb", "ticket_id": "7"}

    def test_colon_token_with_empty_side_is_free_text(self):
        # A token needs BOTH sides non-empty to be a filter, so ":bare" and
        # "key:" stay free text. Note "3:1" does become a {"3": "1"} filter --
        # long-standing behavior, preserved here deliberately, not a goal.
        filters, free = parse_metadata_query("ratio 3:1 :bare key:")
        assert filters == {"3": "1"}
        assert free == "ratio :bare key:"

    def test_quoted_filter_value_survives(self):
        filters, free = parse_metadata_query('source_type:"kb docs" quota')
        assert filters == {"source_type": "kb docs"}
        assert free == "quota"
