"""Unit tests for the RAGAS golden-set maintenance detection passes.

Covers group 2 of the openspec change `maintain-ragas-goldenset`: the read-only
corpus accessor and the coverage / orphan detection built on it.

Two invariants from the design drive most of these tests:

- **The persisted corpus is not a reliable mirror of the live KB** (design D6).
  It never prunes pages that vanish from a later sitemap, and it skips the
  content write for a URL it already holds, so corpus *absence* is not evidence
  a page was removed. Orphan detection therefore keys on a freshly expanded live
  source inventory, never on corpus-absence alone.
- **`expand_sitemaps` fails open per document** — a sitemap that fails to fetch
  or parse contributes zero URLs with only a WARNING. An unguarded pass would
  read that as "the whole KB was removed", so an incomplete inventory abstains.

Everything here is hermetic: the corpus read takes an injected row fetcher and
the inventory build takes an injected fetch callable, so no test touches
Postgres or the network.
"""

from __future__ import annotations

from src.utils.goldenset_maintenance import (
    CorpusDoc,
    NearMiss,
    bank_source_urls,
    filter_docs,
    find_coverage_gaps,
    group_by_parent,
    parent_source,
    read_corpus_docs,
    reconcile,
    reconciliation_key,
)


def _rows(*rows):
    """Build a fake corpus row fetcher over literal `documents` rows."""
    return lambda: list(rows)


class TestReadCorpusDocs:
    """2.1 — read-only corpus accessor over the existing Postgres path."""

    def test_returns_url_source_type_and_parent_per_row(self):
        docs = read_corpus_docs(
            _rows(
                {
                    "url": "https://docs.rc.fas.harvard.edu/kb/running-jobs/",
                    "source_type": "web",
                },
            )
        )

        assert docs == [
            CorpusDoc(
                url="https://docs.rc.fas.harvard.edu/kb/running-jobs",
                source_type="web",
                parent="https://docs.rc.fas.harvard.edu",
            )
        ]

    def test_normalizes_urls_and_dedupes_slash_variants(self):
        # The ingest stored both slash forms before #118; after normalization
        # they are one page, and coverage must not report the same gap twice.
        docs = read_corpus_docs(
            _rows(
                {
                    "url": "https://docs.rc.fas.harvard.edu/kb/python/",
                    "source_type": "web",
                },
                {
                    "url": "HTTPS://Docs.RC.FAS.Harvard.EDU/kb/python#top",
                    "source_type": "web",
                },
            )
        )

        assert [doc.url for doc in docs] == [
            "https://docs.rc.fas.harvard.edu/kb/python"
        ]

    def test_skips_rows_without_a_usable_url(self):
        docs = read_corpus_docs(
            _rows(
                {"url": None, "source_type": "local_files"},
                {"url": "", "source_type": "web"},
                {"source_type": "web"},
                {
                    "url": "https://docs.rc.fas.harvard.edu/kb/python",
                    "source_type": "web",
                },
            )
        )

        assert [doc.url for doc in docs] == [
            "https://docs.rc.fas.harvard.edu/kb/python"
        ]

    def test_skips_unparseable_urls_without_crashing(self):
        docs = read_corpus_docs(
            _rows(
                {"url": "http://[", "source_type": "web"},
                {
                    "url": "https://docs.rc.fas.harvard.edu/kb/python",
                    "source_type": "web",
                },
            )
        )

        assert [doc.url for doc in docs] == [
            "https://docs.rc.fas.harvard.edu/kb/python"
        ]

    def test_skips_soft_deleted_rows(self):
        # The SQL filters `NOT is_deleted`; this is belt-and-braces so a fetcher
        # that forgets the predicate cannot surface tombstones as coverage gaps.
        docs = read_corpus_docs(
            _rows(
                {
                    "url": "https://docs.rc.fas.harvard.edu/kb/gone",
                    "source_type": "web",
                    "is_deleted": True,
                },
                {
                    "url": "https://docs.rc.fas.harvard.edu/kb/python",
                    "source_type": "web",
                    "is_deleted": False,
                },
            )
        )

        assert [doc.url for doc in docs] == [
            "https://docs.rc.fas.harvard.edu/kb/python"
        ]

    def test_does_not_consult_the_resource_hash(self):
        # `ScrapedResource.get_hash()` is md5(url) — it carries no content
        # signal, so nothing in the accessor may key on it (design D6).
        docs = read_corpus_docs(
            _rows(
                {
                    "url": "https://docs.rc.fas.harvard.edu/kb/python",
                    "source_type": "web",
                    "resource_hash": "deadbeef",
                },
            )
        )

        assert docs[0] == CorpusDoc(
            url="https://docs.rc.fas.harvard.edu/kb/python",
            source_type="web",
            parent="https://docs.rc.fas.harvard.edu",
        )


class TestParentSource:
    """2.1 — the `parent` label, mirroring the catalog's source grouping."""

    def test_web_parent_is_the_host(self):
        assert (
            parent_source("https://docs.rc.fas.harvard.edu/kb/python", "web")
            == "https://docs.rc.fas.harvard.edu"
        )

    def test_git_parent_is_the_repository_not_the_file(self):
        # A git source contributes one URL per file; grouping on the repo is what
        # keeps a 5000-file repo from flooding the coverage report (task 2.5).
        assert (
            parent_source("https://github.com/fasrc/archi/blob/dev/README.md", "git")
            == "https://github.com/fasrc/archi"
        )

    def test_local_files_parent_is_a_fixed_label(self):
        assert parent_source("/n/holylabs/notes.md", "local_files") == "Local files"

    def test_unknown_source_type_falls_back_to_the_type_then_unknown(self):
        assert parent_source("https://example.org/x", "jira") == "Jira"
        assert parent_source("https://example.org/x", "") == "Unknown"

    def test_url_that_does_not_match_the_pattern_is_its_own_parent(self):
        assert parent_source("https://github.com", "git") == "https://github.com"


KB = "https://docs.rc.fas.harvard.edu"


class TestReconciliationKey:
    """2.2 — the near-miss grouping key: final slug, minus a WP `-N` alias."""

    def test_key_is_the_final_path_segment(self):
        assert reconciliation_key(f"{KB}/kb/running-jobs") == "running-jobs"

    def test_key_ignores_the_path_prefix_so_a_moved_page_still_pairs(self):
        assert reconciliation_key(f"{KB}/kb/running-jobs") == reconciliation_key(
            f"{KB}/docs/running-jobs"
        )

    def test_key_strips_a_wordpress_collision_suffix(self):
        assert reconciliation_key(f"{KB}/kb/running-jobs-2") == "running-jobs"

    def test_key_strips_an_html_extension(self):
        assert reconciliation_key(f"{KB}/faq.html") == reconciliation_key(
            f"{KB}/kb/faq"
        )

    def test_key_is_case_insensitive_and_slash_tolerant(self):
        assert reconciliation_key(f"{KB}/kb/Running-Jobs/") == "running-jobs"

    def test_distinct_slugs_do_not_share_a_key(self):
        # A rename is a real gap/orphan, not a near-miss — the operator must see
        # it, so the rule deliberately stops short of fuzzy matching.
        assert reconciliation_key(f"{KB}/kb/running-jobs") != reconciliation_key(
            f"{KB}/kb/submitting-jobs"
        )
        assert reconciliation_key(f"{KB}/kb/python") != reconciliation_key(
            f"{KB}/kb/python-packages"
        )

    def test_a_url_with_no_path_segment_has_no_key(self):
        # A bare host cannot be paired with anything by slug.
        assert reconciliation_key(f"{KB}/") is None
        assert reconciliation_key(KB) is None

    def test_a_malformed_url_has_no_key(self):
        # The key is a public entry point, so it must be safe on a raw URL that
        # has not already been through `canonical_url`.
        assert reconciliation_key("http://[") is None

    def test_an_all_numeric_slug_keeps_its_digits(self):
        # Stripping `-\d+` must not erase a slug that IS a number, or every
        # numeric page would collapse onto one empty key.
        assert reconciliation_key(f"{KB}/kb/2024") == "2024"


class TestReconcile:
    """2.2 — exact / near-miss / unmatched partition, used by both passes."""

    def test_exact_match_after_canonicalization_is_matched_not_a_near_miss(self):
        result = reconcile(
            [f"{KB}/kb/running-jobs"],
            [f"{KB}/kb/running-jobs"],
        )

        assert result.matched == (f"{KB}/kb/running-jobs",)
        assert result.near_misses == ()
        assert result.unmatched == ()

    def test_moved_prefix_is_a_near_miss_not_an_unmatched_url(self):
        result = reconcile([f"{KB}/docs/running-jobs"], [f"{KB}/kb/running-jobs"])

        assert result.matched == ()
        assert result.unmatched == ()
        assert result.near_misses == (
            NearMiss(
                url=f"{KB}/docs/running-jobs",
                candidates=(f"{KB}/kb/running-jobs",),
                key="running-jobs",
            ),
        )

    def test_wordpress_alias_is_a_near_miss(self):
        result = reconcile([f"{KB}/kb/running-jobs-2"], [f"{KB}/kb/running-jobs"])

        assert [near.url for near in result.near_misses] == [f"{KB}/kb/running-jobs-2"]

    def test_a_genuinely_absent_url_is_unmatched(self):
        result = reconcile([f"{KB}/kb/brand-new-page"], [f"{KB}/kb/running-jobs"])

        assert result.unmatched == (f"{KB}/kb/brand-new-page",)
        assert result.near_misses == ()

    def test_all_candidates_sharing_a_key_are_listed_once_sorted(self):
        result = reconcile(
            [f"{KB}/docs/python"],
            [f"{KB}/kb/python-2", f"{KB}/kb/python"],
        )

        assert result.near_misses == (
            NearMiss(
                url=f"{KB}/docs/python",
                candidates=(f"{KB}/kb/python", f"{KB}/kb/python-2"),
                key="python",
            ),
        )

    def test_input_order_is_preserved_and_duplicates_collapse(self):
        result = reconcile(
            [f"{KB}/kb/b", f"{KB}/kb/a/", f"{KB}/kb/a"],
            [f"{KB}/kb/a"],
        )

        assert result.matched == (f"{KB}/kb/a",)
        assert result.unmatched == (f"{KB}/kb/b",)

    def test_a_path_case_variant_is_a_near_miss_not_an_exact_match(self):
        # The ingest does NOT fold path case (`/kb/A` and `/kb/a` are distinct
        # resources on a case-sensitive server), but the slug key does — so the
        # pair lands in the review bucket instead of being silently merged or
        # reported as a spurious gap.
        result = reconcile([f"{KB}/kb/Python"], [f"{KB}/kb/python"])

        assert result.matched == ()
        assert [near.url for near in result.near_misses] == [f"{KB}/kb/Python"]

    def test_unparseable_urls_are_dropped_rather_than_crashing_the_report(self):
        result = reconcile(["http://["], [f"{KB}/kb/a", "http://["])

        assert result.matched == ()
        assert result.near_misses == ()
        assert result.unmatched == ()

    def test_keyless_urls_never_pair_with_each_other(self):
        # Two bare hosts have no slug; they must not be reported as reconcilable.
        result = reconcile([f"{KB}/"], ["https://example.org/"])

        assert result.unmatched == (f"{KB}/",)
        assert result.near_misses == ()

    def test_reconcile_is_direction_agnostic(self):
        # Orphan detection runs the same primitive with bank sources as the
        # subject and the live inventory as the reference.
        result = reconcile([f"{KB}/kb/removed"], [f"{KB}/kb/still-here"])

        assert result.unmatched == (f"{KB}/kb/removed",)


def _doc(path, source_type="web", host=KB):
    """A corpus doc at `host + path`, with the parent label derived like the read."""
    url = f"{host}{path}"
    return CorpusDoc(
        url=url, source_type=source_type, parent=parent_source(url, source_type)
    )


def _row(*sources, **extra):
    """A minimal bank row citing `sources`."""
    row = {"user_input": "q?", "reference": "a", "sources": list(sources)}
    row.update(extra)
    return row


class TestBankSourceUrls:
    """2.3 — the bank's own view of what it grounds against."""

    def test_collects_sources_across_rows(self):
        bank = [_row(f"{KB}/kb/a"), _row(f"{KB}/kb/b", f"{KB}/kb/c")]

        assert bank_source_urls(bank) == [f"{KB}/kb/a", f"{KB}/kb/b", f"{KB}/kb/c"]

    def test_source_less_refusal_rows_contribute_nothing(self):
        bank = [_row(anchor_type="should_refuse"), _row(f"{KB}/kb/a")]

        assert bank_source_urls(bank) == [f"{KB}/kb/a"]

    def test_tolerates_rows_with_a_missing_or_malformed_sources_field(self):
        bank = [{"user_input": "q?"}, {"sources": None}, {"sources": "not-a-list"}]

        assert bank_source_urls(bank) == []

    def test_duplicates_across_rows_collapse_in_order(self):
        bank = [_row(f"{KB}/kb/a"), _row(f"{KB}/kb/a/"), _row(f"{KB}/kb/b")]

        assert bank_source_urls(bank) == [f"{KB}/kb/a", f"{KB}/kb/b"]


class TestFindCoverageGaps:
    """2.3 — corpus pages no bank row grounds against."""

    def test_an_uncovered_page_is_a_gap(self):
        report = find_coverage_gaps(
            [_doc("/kb/a"), _doc("/kb/b")], [_row(f"{KB}/kb/a")]
        )

        assert [doc.url for doc in report.gaps] == [f"{KB}/kb/b"]
        assert [doc.url for doc in report.covered] == [f"{KB}/kb/a"]

    def test_a_fully_covered_corpus_produces_no_gaps(self):
        corpus = [_doc("/kb/a"), _doc("/kb/b")]
        bank = [_row(f"{KB}/kb/a"), _row(f"{KB}/kb/b")]

        assert find_coverage_gaps(corpus, bank).gaps == ()

    def test_coverage_is_re_derived_from_the_current_bank_every_run(self):
        # 2.3/3.4: drafting candidates for a page does NOT make it covered — only
        # an actual bank row citing it does. With the row removed it is a gap again.
        corpus = [_doc("/kb/a")]

        assert find_coverage_gaps(corpus, [_row(f"{KB}/kb/a")]).gaps == ()
        assert [d.url for d in find_coverage_gaps(corpus, []).gaps] == [f"{KB}/kb/a"]

    def test_slash_and_case_variants_still_count_as_covered(self):
        report = find_coverage_gaps([_doc("/kb/a")], [_row(f"{KB.upper()}/kb/a/#top")])

        assert report.gaps == ()

    def test_a_slug_near_miss_is_reconciliation_not_a_gap(self):
        report = find_coverage_gaps([_doc("/docs/a")], [_row(f"{KB}/kb/a")])

        assert report.gaps == ()
        assert [near.url for near in report.needs_reconciliation] == [f"{KB}/docs/a"]

    def test_a_bank_source_absent_from_the_corpus_is_not_a_coverage_gap(self):
        # That is the orphan question, asked against the live inventory instead.
        report = find_coverage_gaps(
            [_doc("/kb/a")], [_row(f"{KB}/kb/a", f"{KB}/kb/zzz")]
        )

        assert report.gaps == ()

    def test_many_rows_citing_one_page_cover_it_once(self):
        report = find_coverage_gaps(
            [_doc("/kb/a")], [_row(f"{KB}/kb/a"), _row(f"{KB}/kb/a")]
        )

        assert report.gaps == ()
        assert len(report.covered) == 1


class TestGroupAndFilter:
    """2.5 — keep a high-volume source from flooding the report."""

    def test_group_by_parent_buckets_docs_in_first_seen_order(self):
        docs = [
            _doc("/kb/a"),
            _doc("/fasrc/archi/blob/dev/x.py", "git", "https://github.com"),
            _doc("/kb/b"),
        ]

        grouped = group_by_parent(docs)

        assert list(grouped) == [KB, "https://github.com/fasrc/archi"]
        assert [d.url for d in grouped[KB]] == [f"{KB}/kb/a", f"{KB}/kb/b"]

    def test_filter_by_source_type(self):
        docs = [
            _doc("/kb/a"),
            _doc("/fasrc/archi/blob/dev/x.py", "git", "https://github.com"),
        ]

        assert [d.url for d in filter_docs(docs, source_type="git")] == [
            "https://github.com/fasrc/archi/blob/dev/x.py"
        ]

    def test_filter_by_parent(self):
        docs = [
            _doc("/kb/a"),
            _doc("/fasrc/archi/blob/dev/x.py", "git", "https://github.com"),
        ]

        assert [d.url for d in filter_docs(docs, parent=KB)] == [f"{KB}/kb/a"]

    def test_filter_by_path_glob_against_the_full_url(self):
        docs = [_doc("/kb/a"), _doc("/docs/b"), _doc("/kb/c")]

        assert [d.url for d in filter_docs(docs, path_glob=f"{KB}/kb/*")] == [
            f"{KB}/kb/a",
            f"{KB}/kb/c",
        ]

    def test_filters_combine_conjunctively(self):
        docs = [
            _doc("/kb/a"),
            _doc("/kb/b", "git"),
            _doc("/fasrc/archi/blob/dev/x.py", "git", "https://github.com"),
        ]

        assert [
            d.url for d in filter_docs(docs, source_type="git", path_glob=f"{KB}/*")
        ] == [f"{KB}/kb/b"]

    def test_no_filters_returns_everything_unchanged(self):
        docs = [_doc("/kb/a"), _doc("/kb/b")]

        assert filter_docs(docs) == tuple(docs)
