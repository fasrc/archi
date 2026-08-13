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

import copy
import os
from pathlib import Path

import pytest

from src.data_manager.collectors.scrapers.sitemap_source import (
    SitemapFetchError,
    SitemapPolicy,
)
from src.utils.goldenset_maintenance import (
    MAX_PROMPT_PAGE_CHARS,
    TRUNCATION_MARKER,
    BaselineRow,
    CorpusDoc,
    Decline,
    DriftExtractionError,
    LiveInventory,
    NearMiss,
    ProposalError,
    _truncate_page_text,
    bank_source_urls,
    build_drift_prompt,
    build_live_inventory,
    declined_urls,
    filter_docs,
    find_coverage_gaps,
    find_drift,
    find_orphans,
    group_by_parent,
    is_fetchable_source,
    normalize_extracted_text,
    page_digest,
    parent_source,
    parse_candidates,
    parse_drift_verdict,
    propose_candidates,
    read_corpus_docs,
    read_declines,
    reconcile,
    reconciliation_key,
    resolve_persisted_path,
    with_decline,
    without_decline,
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

    def test_non_retrievable_documents_are_skipped(self):
        # Only `embedded` documents have chunks the retriever can serve. Coverage
        # asks "which RETRIEVABLE page has no question?", so a pending/embedding/
        # failed row must not become a gap — the resulting golden question would
        # be unanswerable and would score as a benchmark failure.
        docs = read_corpus_docs(
            _rows(
                {"url": f"{KB}/kb/ok", "ingestion_status": "embedded"},
                {"url": f"{KB}/kb/pending", "ingestion_status": "pending"},
                {"url": f"{KB}/kb/embedding", "ingestion_status": "embedding"},
                {"url": f"{KB}/kb/failed", "ingestion_status": "failed"},
            )
        )

        assert [doc.url for doc in docs] == [f"{KB}/kb/ok"]

    def test_a_row_with_no_status_is_kept(self):
        # A dump that omits the column cannot be judged. Dropping those rows would
        # empty the report and read as "fully covered" — a silent false clean, the
        # same failure class the orphan abstention guard exists to prevent.
        # Over-reporting a gap is visible and cheap; under-reporting hides work.
        docs = read_corpus_docs(_rows({"url": f"{KB}/kb/a", "source_type": "web"}))

        assert [doc.url for doc in docs] == [f"{KB}/kb/a"]

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

    def test_a_cross_host_slug_match_is_not_a_near_miss(self):
        # A near-miss claims "these may be the same page under a moved slug".
        # Two different hosts are never the same page, so pairing them would
        # suppress a real gap/orphan behind a bogus candidate.
        result = reconcile([f"{KB}/kb/install"], ["https://vendor.example/install"])

        assert result.near_misses == ()
        assert result.unmatched == (f"{KB}/kb/install",)

    def test_same_host_duplicate_slugs_are_all_listed_as_candidates(self):
        # Distinct same-host pages sharing a leaf slug are genuinely ambiguous.
        # Every candidate is shown so the operator adjudicates; the pairing is
        # never silently narrowed to one.
        result = reconcile(
            [f"{KB}/kb/install"],
            [f"{KB}/docs/a/install", f"{KB}/docs/b/install"],
        )

        assert result.near_misses == (
            NearMiss(
                url=f"{KB}/kb/install",
                candidates=(f"{KB}/docs/a/install", f"{KB}/docs/b/install"),
                key="install",
            ),
        )


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

    def test_tolerates_a_non_dict_row_and_non_string_sources_entries(self):
        # A hand-edited bank can hold anything; a read-only report must survive
        # it rather than raising partway through the census.
        bank = ["oops", None, _row(None, "", 42, f"{KB}/kb/a")]

        assert bank_source_urls(bank) == [f"{KB}/kb/a"]

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

    def test_a_cross_host_bank_source_does_not_mask_a_coverage_gap(self):
        # The bank cites external authorities (18 upstream Slurm-docs rows
        # today). Coverage has no scope guard of its own, so without a host
        # constraint in `reconcile` a foreign URL ending in the same slug would
        # "reconcile" a KB page and hide a real gap behind a bogus pairing.
        report = find_coverage_gaps(
            [_doc("/kb/mpi")], [_row("https://slurm.schedmd.com/mpi")]
        )

        assert [doc.url for doc in report.gaps] == [f"{KB}/kb/mpi"]
        assert report.needs_reconciliation == ()

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


SM = "https://docs.rc.fas.harvard.edu/sitemap.xml"
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def _urlset(*locs):
    body = "".join(f"<url><loc>{loc}</loc></url>" for loc in locs)
    return f'<?xml version="1.0"?><urlset {NS}>{body}</urlset>'


def _index(*locs):
    body = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return f'<?xml version="1.0"?><sitemapindex {NS}>{body}</sitemapindex>'


def _fetcher(docs):
    """Fetch callable over a {url: text} map; anything else fails like the real one."""

    def fetch(url):
        if url not in docs:
            raise SitemapFetchError(f"404 {url}")
        return docs[url]

    return fetch


class TestBuildLiveInventory:
    """2.4a — the live inventory, and knowing when it cannot be trusted."""

    def test_hand_listed_urls_are_the_inventory(self):
        inv = build_live_inventory([f"{KB}/kb/a/", f"{KB}/kb/b"], _fetcher({}))

        assert inv.urls == (f"{KB}/kb/a", f"{KB}/kb/b")
        assert inv.complete is True
        assert inv.failures == ()

    def test_a_sitemap_line_is_expanded(self):
        inv = build_live_inventory(
            [f"sitemap-{SM}"], _fetcher({SM: _urlset(f"{KB}/kb/a", f"{KB}/kb/b")})
        )

        assert inv.urls == (f"{KB}/kb/a", f"{KB}/kb/b")
        assert inv.complete is True

    def test_hand_listed_and_expanded_urls_merge_and_dedupe(self):
        inv = build_live_inventory(
            [f"{KB}/kb/a/", f"sitemap-{SM}"],
            _fetcher({SM: _urlset(f"{KB}/kb/a", f"{KB}/kb/b")}),
        )

        assert inv.urls == (f"{KB}/kb/a", f"{KB}/kb/b")
        assert inv.complete is True

    def test_a_depth_suffix_is_stripped_like_the_ingest_does(self):
        # `sources.list` supports `URL,depth`, and the ingest drops the suffix in
        # `ScraperManager._extract_urls_from_file` BEFORE prefix routing. The live
        # oracle must parse identically: otherwise the inventory holds `…/kb/a,2`
        # while the corpus holds `…/kb/a`, and a bank row citing the still-live
        # page reads as removed.
        inv = build_live_inventory([f"{KB}/kb/a,2"], _fetcher({}))

        assert inv.urls == (f"{KB}/kb/a",)
        assert inv.complete is True

    def test_a_sitemap_line_with_a_depth_suffix_fetches_the_bare_url(self):
        inv = build_live_inventory(
            [f"sitemap-{SM},2"], _fetcher({SM: _urlset(f"{KB}/kb/a")})
        )

        assert inv.urls == (f"{KB}/kb/a",)
        assert inv.complete is True

    def test_a_git_source_is_not_enumerable_and_stays_out_of_the_inventory(self):
        # A git source ingests one document PER FILE; the inventory cannot list
        # those without cloning. Worse, the raw line parses as scheme `git-https`
        # with host `github.com`, which would put that host in scope and make
        # every bank row citing the repo a false orphan — on a report whose
        # proposed action is "prune or convert".
        inv = build_live_inventory(
            [f"{KB}/kb/a", "git-https://github.com/org/repo"], _fetcher({})
        )

        assert inv.urls == (f"{KB}/kb/a",)
        assert inv.unsupported == ("git-https://github.com/org/repo",)

    def test_an_sso_prefix_is_stripped_and_the_page_is_inventoried(self):
        # `sso-` only tells the ingest to authenticate; the line is still exactly
        # one page, so it is enumerable.
        inv = build_live_inventory([f"sso-{KB}/kb/private"], _fetcher({}))

        assert inv.urls == (f"{KB}/kb/private",)
        assert inv.unsupported == ()

    def test_elog_and_indico_sources_are_not_enumerable(self):
        inv = build_live_inventory(
            ["elog-https://elog.example/elog/", "indico-https://indico.example/"],
            _fetcher({}),
        )

        assert inv.urls == ()
        assert len(inv.unsupported) == 2

    def test_an_unprefixed_elog_url_is_detected_like_the_ingest_does(self):
        # The ingest auto-detects an unprefixed ELOG index by path; mirroring it
        # keeps an un-enumerable fan-out source out of the inventory.
        inv = build_live_inventory(["https://logs.example/elog/demo"], _fetcher({}))

        assert inv.urls == ()
        assert inv.unsupported == ("https://logs.example/elog/demo",)

    def test_a_failed_sitemap_fetch_marks_the_inventory_incomplete(self):
        # expand_sitemaps fails OPEN here — it returns zero URLs with only a
        # WARNING, which an unguarded caller would read as "the KB is empty".
        inv = build_live_inventory([f"sitemap-{SM}"], _fetcher({}))

        assert inv.complete is False
        assert any(SM in reason for reason in inv.failures)

    def test_an_unparseable_sitemap_marks_the_inventory_incomplete(self):
        inv = build_live_inventory([f"sitemap-{SM}"], _fetcher({SM: "<not-xml"}))

        assert inv.complete is False
        assert any(SM in reason for reason in inv.failures)

    def test_one_failed_child_of_an_index_marks_the_whole_inventory_incomplete(self):
        # The dangerous case: siblings expand fine, so the count looks healthy
        # and stays above the floor — the partial loss is silent without this.
        child_ok, child_bad = f"{KB}/sitemap-1.xml", f"{KB}/sitemap-2.xml"
        inv = build_live_inventory(
            [f"sitemap-{SM}"],
            _fetcher(
                {
                    SM: _index(child_ok, child_bad),
                    child_ok: _urlset(f"{KB}/kb/a", f"{KB}/kb/b"),
                }
            ),
        )

        assert inv.urls == (f"{KB}/kb/a", f"{KB}/kb/b")
        assert inv.complete is False
        assert any(child_bad in reason for reason in inv.failures)

    def test_a_bank_row_on_a_git_source_host_is_out_of_scope_not_an_orphan(self):
        # End to end: the git source must not lend its host to `in_scope_hosts`,
        # or every per-file bank URL becomes a proposed prune.
        inv = build_live_inventory(
            [f"{KB}/kb/a", "git-https://github.com/org/repo"], _fetcher({})
        )
        report = find_orphans([_row("https://github.com/org/repo/blob/main/x.py")], inv)

        assert report.orphans == ()
        assert report.out_of_scope == ("https://github.com/org/repo/blob/main/x.py",)

    def test_a_fan_out_source_sharing_a_host_still_yields_no_orphans(self):
        # Host-level scope is not enough: a hand-listed page puts `github.com` in
        # scope while the git source contributes nothing, so every per-file bank
        # URL under the repo would be judged against an inventory that cannot
        # contain it — and proposed for prune.
        inv = build_live_inventory(
            [
                "https://github.com/org/repo/releases",
                "git-https://github.com/org/repo",
            ],
            _fetcher({}),
        )
        report = find_orphans([_row("https://github.com/org/repo/blob/main/x.py")], inv)

        assert report.orphans == ()
        assert report.out_of_scope == ("https://github.com/org/repo/blob/main/x.py",)

    def test_a_sibling_path_outside_the_fan_out_scope_is_still_judged(self):
        # The fan-out source owns its own subtree, not the whole host. `…/repo2`
        # is a different project, so exempting it would trade a false orphan for a
        # silently missed one.
        inv = build_live_inventory(
            ["https://github.com/org/repo2/page", "git-https://github.com/org/repo"],
            _fetcher({}),
        )
        report = find_orphans([_row("https://github.com/org/repo2/gone")], inv)

        assert [orphan.urls for orphan in report.orphans] == [
            ("https://github.com/org/repo2/gone",)
        ]

    def test_an_expansion_below_its_floor_marks_the_inventory_incomplete(self):
        inv = build_live_inventory(
            [f"sitemap-{SM}"],
            _fetcher({SM: _urlset(f"{KB}/kb/a")}),
            policy=SitemapPolicy(min_pages=5),
        )

        assert inv.complete is False
        assert any("below_floor" in reason for reason in inv.failures)

    def test_an_expansion_over_its_cap_marks_the_inventory_incomplete(self):
        inv = build_live_inventory(
            [f"sitemap-{SM}"],
            _fetcher({SM: _urlset(f"{KB}/kb/a", f"{KB}/kb/b")}),
            policy=SitemapPolicy(max_pages=1),
        )

        assert inv.complete is False
        assert any("over_cap" in reason for reason in inv.failures)

    def test_an_empty_inventory_is_never_treated_as_complete(self):
        # Nothing to compare against must never mean "everything was removed".
        assert build_live_inventory([], _fetcher({})).complete is False

    def test_blank_and_commented_source_lines_are_ignored(self):
        inv = build_live_inventory(
            ["", "   ", "# a comment", f"{KB}/kb/a"], _fetcher({})
        )

        assert inv.urls == (f"{KB}/kb/a",)
        assert inv.complete is True


def _inventory(*urls, complete=True):
    return LiveInventory(urls=tuple(urls), complete=complete, failures=())


class TestFindOrphans:
    """2.4 — rows whose grounding page is gone from the LIVE source inventory."""

    def test_a_row_citing_a_removed_page_is_an_orphan(self):
        bank = [_row(f"{KB}/kb/removed", user_input="why?")]

        report = find_orphans(bank, _inventory(f"{KB}/kb/still-here"))

        assert report.abstained is False
        assert [(o.row_index, o.urls) for o in report.orphans] == [
            (0, (f"{KB}/kb/removed",))
        ]

    def test_a_live_page_is_not_an_orphan(self):
        report = find_orphans([_row(f"{KB}/kb/a")], _inventory(f"{KB}/kb/a"))

        assert report.orphans == ()

    def test_a_should_refuse_row_is_never_an_orphan(self):
        bank = [_row(anchor_type="should_refuse")]

        assert find_orphans(bank, _inventory(f"{KB}/kb/a")).orphans == ()

    def test_only_the_removed_url_of_a_multi_source_row_is_named(self):
        bank = [_row(f"{KB}/kb/a", f"{KB}/kb/removed")]

        report = find_orphans(bank, _inventory(f"{KB}/kb/a"))

        assert [o.urls for o in report.orphans] == [(f"{KB}/kb/removed",)]

    def test_a_url_on_a_host_the_inventory_never_covers_is_out_of_scope(self):
        # 18 of the 105 FASRC bank rows cite slurm.schedmd.com, which the KB
        # sitemap will never contain. Those pages were not removed — the
        # inventory simply cannot speak to them.
        bank = [_row("https://slurm.schedmd.com/sbatch.html")]

        report = find_orphans(bank, _inventory(f"{KB}/kb/a"))

        assert report.orphans == ()
        assert report.out_of_scope == ("https://slurm.schedmd.com/sbatch.html",)

    def test_a_slug_near_miss_is_reconciliation_not_an_orphan(self):
        bank = [_row(f"{KB}/kb/running-jobs")]

        report = find_orphans(bank, _inventory(f"{KB}/docs/running-jobs"))

        assert report.orphans == ()
        assert [n.url for n in report.needs_reconciliation] == [f"{KB}/kb/running-jobs"]

    def test_an_incomplete_inventory_abstains_from_flagging_anything(self):
        bank = [_row(f"{KB}/kb/removed")]
        inventory = LiveInventory(
            urls=(f"{KB}/kb/a",), complete=False, failures=("sitemap.xml: 404",)
        )

        report = find_orphans(bank, inventory)

        assert report.abstained is True
        assert report.orphans == ()
        assert report.reasons == ("sitemap.xml: 404",)

    def test_survives_a_hand_edited_bank_and_a_junk_inventory_url(self):
        # A malformed entry on either side must degrade to "skip that entry",
        # never to a crash or - worse - a spurious orphan.
        bank = ["oops", _row(None, "", "http://[", f"{KB}/kb/removed")]
        inventory = LiveInventory(
            urls=(f"{KB}/kb/a", "http://["), complete=True, failures=()
        )

        report = find_orphans(bank, inventory)

        assert [o.urls for o in report.orphans] == [(f"{KB}/kb/removed",)]

    def test_detection_never_mutates_the_bank(self):
        bank = [_row(f"{KB}/kb/removed"), _row(anchor_type="should_refuse")]
        before = copy.deepcopy(bank)

        find_orphans(bank, _inventory(f"{KB}/kb/a"))

        assert bank == before


# --------------------------------------------------------------------------- #
# Group 3 — decision ledger + greenlit-only candidate proposal
# --------------------------------------------------------------------------- #
class TestDeclineLedger:
    """3.4 — the ledger records DECLINES only; covered-ness comes from the bank."""

    def test_reads_entries_into_declines(self):
        declines = read_declines(
            [{"url": f"{KB}/kb/a", "reason": "minor page", "at": "2026-07-22"}]
        )

        assert declines == (
            Decline(url=f"{KB}/kb/a", reason="minor page", at="2026-07-22"),
        )

    def test_a_malformed_entry_fails_the_run_instead_of_vanishing(self):
        # A dropped decline is a dropped operator decision. It fails in the
        # visible direction (the page resurfaces as a gap) but it fails
        # SILENTLY, on a green run — and a decline cannot be reconstructed from
        # anything. A corrupt *file* is already fatal; a corrupt *entry* is the
        # same failure at a finer grain and gets the same treatment.
        with pytest.raises(ValueError) as caught:
            read_declines([{"url": f"{KB}/kb/a"}, "not-an-object"])

        assert "1" in str(caught.value)

    def test_an_entry_without_a_usable_url_fails_the_run(self):
        with pytest.raises(ValueError):
            read_declines([{"no_url": 1}])
        with pytest.raises(ValueError):
            read_declines([{"url": "   "}])

    def test_an_entry_whose_url_will_not_parse_fails_the_run(self):
        # Silently dropping this one was the subtler leak: the entry looks fine
        # until canonicalization, which used to warn and move on.
        with pytest.raises(ValueError):
            read_declines([{"url": "http://["}])

    def test_a_non_list_ledger_fails_the_run(self):
        with pytest.raises(ValueError):
            read_declines({"url": f"{KB}/kb/a"})
        with pytest.raises(ValueError):
            read_declines(None)

    def test_an_empty_ledger_is_fine(self):
        assert read_declines([]) == ()

    def test_declined_urls_are_canonicalized_like_every_other_url(self):
        # A ledger hand-edited with a trailing slash must still suppress the
        # canonical corpus URL — otherwise a decline silently stops working.
        declines = read_declines([{"url": f"{KB}/kb/a/"}])

        assert declined_urls(declines) == {f"{KB}/kb/a"}

    def test_appending_a_decline_leaves_the_original_entries_untouched(self):
        entries = [{"url": f"{KB}/kb/a"}]

        appended = with_decline(entries, f"{KB}/kb/b", reason="thin", at="2026-07-22")

        assert entries == [{"url": f"{KB}/kb/a"}]
        assert appended == [
            {"url": f"{KB}/kb/a"},
            {"url": f"{KB}/kb/b", "reason": "thin", "at": "2026-07-22"},
        ]

    def test_declining_the_same_page_twice_does_not_duplicate_it(self):
        entries = with_decline([], f"{KB}/kb/a", reason="first")

        again = with_decline(entries, f"{KB}/kb/a/", reason="second")

        assert again == entries

    def test_declining_an_unusable_url_is_refused(self):
        # Writing a URL the tool cannot canonicalize would create a ledger entry
        # that can never match anything — a decline that silently does nothing.
        with pytest.raises(ValueError):
            with_decline([], "http://[")

    def test_removing_a_decline_drops_only_that_entry(self):
        entries = [{"url": f"{KB}/kb/a"}, {"url": f"{KB}/kb/b", "reason": "keep"}]

        assert without_decline(entries, f"{KB}/kb/a/") == [
            {"url": f"{KB}/kb/b", "reason": "keep"}
        ]
        assert len(entries) == 2

    def test_removing_a_decline_that_is_not_there_is_a_no_op(self):
        entries = [{"url": f"{KB}/kb/a"}]

        assert without_decline(entries, f"{KB}/kb/b") == entries

    def test_undeclining_an_unusable_url_is_refused(self):
        with pytest.raises(ValueError):
            without_decline([], "http://[")

    def test_a_declined_page_is_suppressed_from_the_gap_list(self):
        docs = [_doc("/kb/a"), _doc("/kb/b")]

        report = find_coverage_gaps(docs, [], declined={f"{KB}/kb/a"})

        assert [d.url for d in report.gaps] == [f"{KB}/kb/b"]

    def test_a_suppressed_page_is_reported_not_silently_dropped(self):
        # A report that hides pages without saying so reads as clean when it is
        # not — the suppressed set is part of the output, not a silent filter.
        docs = [_doc("/kb/a")]

        report = find_coverage_gaps(docs, [], declined={f"{KB}/kb/a"})

        assert [d.url for d in report.suppressed] == [f"{KB}/kb/a"]
        assert report.covered == ()

    def test_a_declined_page_that_a_bank_row_covers_is_covered_not_suppressed(self):
        docs = [_doc("/kb/a")]

        report = find_coverage_gaps(docs, [_row(f"{KB}/kb/a")], declined={f"{KB}/kb/a"})

        assert [d.url for d in report.covered] == [f"{KB}/kb/a"]
        assert report.suppressed == ()

    def test_a_greenlit_but_unapplied_page_is_still_a_gap(self):
        # Proposing candidates must NOT mark a page covered: covered-ness is
        # re-derived from the bank, so a page whose candidates were drafted and
        # then abandoned stays visible until a row actually lands.
        docs = [_doc("/kb/a")]
        propose_candidates(f"{KB}/kb/a", "body", _llm_returning([_candidate()]))

        report = find_coverage_gaps(docs, [], declined=set())

        assert [d.url for d in report.gaps] == [f"{KB}/kb/a"]


def _candidate(**extra):
    candidate = {
        "user_input": "How do I request a GPU?",
        "reference": "Add #SBATCH --gpus=1.",
        "anchor_type": "easy_retrieve",
    }
    candidate.update(extra)
    return candidate


def _llm_returning(payload):
    """An injected LLM that answers with `payload` serialized as JSON."""
    import json as _json

    text = payload if isinstance(payload, str) else _json.dumps(payload)
    return lambda prompt: text


class TestProposeCandidates:
    """3.1-3.3 — grounded drafts for a greenlit page, never locked, never applied."""

    def test_drafts_a_candidate_grounded_in_the_greenlit_page(self):
        proposal = propose_candidates(
            f"{KB}/kb/a", "GPU body text", _llm_returning([_candidate()])
        )

        assert len(proposal.candidates) == 1
        row = proposal.candidates[0].as_row()
        assert row["status"] == "draft"
        assert row["sources"] == [f"{KB}/kb/a"]
        assert row["user_input"] == "How do I request a GPU?"
        assert row["reference"] == "Add #SBATCH --gpus=1."
        assert row["anchor_type"] == "easy_retrieve"

    def test_a_candidate_can_never_be_constructed_locked(self):
        # `status` is not a field: there is no way to express a locked candidate,
        # so a compromised or confused model cannot smuggle one through.
        proposal = propose_candidates(
            f"{KB}/kb/a", "body", _llm_returning([_candidate(status="locked")])
        )

        assert proposal.candidates[0].as_row()["status"] == "draft"

    def test_model_supplied_sources_are_replaced_by_the_greenlit_url(self):
        # Grounding is the tool's guarantee, not the model's: whatever URL the
        # model cites, the row it produces cites the page the operator greenlit.
        proposal = propose_candidates(
            f"{KB}/kb/a",
            "body",
            _llm_returning([_candidate(sources=["https://evil.example/x"])]),
        )

        assert proposal.candidates[0].as_row()["sources"] == [f"{KB}/kb/a"]

    def test_the_url_is_canonicalized_before_it_becomes_the_source(self):
        proposal = propose_candidates(
            f"{KB}/kb/a/", "body", _llm_returning([_candidate()])
        )

        assert proposal.candidates[0].as_row()["sources"] == [f"{KB}/kb/a"]

    def test_an_unknown_anchor_type_is_rejected_with_a_reason(self):
        proposal = propose_candidates(
            f"{KB}/kb/a", "body", _llm_returning([_candidate(anchor_type="trivia")])
        )

        assert proposal.candidates == ()
        assert len(proposal.rejected) == 1
        assert "anchor_type" in proposal.rejected[0].reason

    def test_a_should_refuse_candidate_is_rejected_as_ungroundable(self):
        # `should_refuse` rows carry NO sources by design; one "grounded in" a
        # page is a contradiction, so it is rejected rather than relabeled.
        proposal = propose_candidates(
            f"{KB}/kb/a",
            "body",
            _llm_returning([_candidate(anchor_type="should_refuse")]),
        )

        assert proposal.candidates == ()
        assert "should_refuse" in proposal.rejected[0].reason

    def test_a_blank_question_or_answer_is_rejected(self):
        proposal = propose_candidates(
            f"{KB}/kb/a",
            "body",
            _llm_returning(
                [_candidate(user_input="  "), _candidate(reference=""), _candidate()]
            ),
        )

        assert len(proposal.candidates) == 1
        assert len(proposal.rejected) == 2

    def test_a_non_object_candidate_is_rejected_not_crashed_on(self):
        proposal = propose_candidates(
            f"{KB}/kb/a", "body", _llm_returning(["just a string", _candidate()])
        )

        assert len(proposal.candidates) == 1
        assert len(proposal.rejected) == 1

    def test_a_fenced_json_reply_is_parsed(self):
        import json as _json

        fenced = "```json\n" + _json.dumps([_candidate()]) + "\n```"

        proposal = propose_candidates(f"{KB}/kb/a", "body", _llm_returning(fenced))

        assert len(proposal.candidates) == 1

    def test_a_candidates_object_wrapper_is_accepted(self):
        proposal = propose_candidates(
            f"{KB}/kb/a", "body", _llm_returning({"candidates": [_candidate()]})
        )

        assert len(proposal.candidates) == 1

    def test_unparseable_model_output_raises_rather_than_returning_nothing(self):
        # Zero candidates from a broken reply must not look like "this page has
        # nothing worth asking" — that is an operational failure of the run.
        with pytest.raises(ProposalError):
            propose_candidates(f"{KB}/kb/a", "body", _llm_returning("not json at all"))

    def test_a_json_scalar_reply_raises(self):
        with pytest.raises(ProposalError):
            propose_candidates(f"{KB}/kb/a", "body", _llm_returning("42"))

    def test_the_prompt_carries_the_page_text_and_the_url(self):
        seen = {}

        def ask(prompt):
            seen["prompt"] = prompt
            import json as _json

            return _json.dumps([_candidate()])

        propose_candidates(f"{KB}/kb/a", "UNIQUE BODY MARKER", ask, count=2)

        assert "UNIQUE BODY MARKER" in seen["prompt"]
        assert f"{KB}/kb/a" in seen["prompt"]
        assert "2" in seen["prompt"]

    def test_the_page_text_is_bounded_so_a_huge_page_cannot_blow_the_context(self):
        seen = {}

        def ask(prompt):
            seen["prompt"] = prompt
            import json as _json

            return _json.dumps([_candidate()])

        propose_candidates(f"{KB}/kb/a", "x" * 500_000, ask)

        assert len(seen["prompt"]) < 200_000

    def test_an_empty_page_refuses_to_ask_the_model_at_all(self):
        # Proposing from an empty extraction would produce ungrounded questions.
        called = []

        with pytest.raises(ProposalError):
            propose_candidates(f"{KB}/kb/a", "   ", lambda p: called.append(p) or "[]")

        assert called == []

    def test_an_unusable_page_url_is_refused_before_the_model_is_called(self):
        called = []

        with pytest.raises(ProposalError):
            propose_candidates("http://[", "body", lambda p: called.append(p) or "[]")

        assert called == []

    def test_the_sanitizer_refuses_an_unusable_url_on_its_own(self):
        # `parse_candidates` is a public entry point, so it re-checks rather than
        # trusting that every caller validated the URL first.
        with pytest.raises(ProposalError):
            parse_candidates("[]", "http://[")

    def test_proposing_never_touches_the_bank(self):
        bank = [_row(f"{KB}/kb/other")]
        before = copy.deepcopy(bank)

        propose_candidates(f"{KB}/kb/a", "body", _llm_returning([_candidate()]))

        assert bank == before


class TestPersistedDocumentPath:
    """The retriever serves the PERSISTED file, so grounding must read that."""

    def test_a_relative_file_path_resolves_under_the_data_path(self):
        assert resolve_persisted_path("web/docs/a.md", "/srv/archi/data") == Path(
            "/srv/archi/data/web/docs/a.md"
        )

    def test_an_absolute_path_inside_the_root_is_allowed(self, tmp_path):
        # Parity with `catalog_postgres._resolve_path`, which stores absolute
        # paths for some deployments — those are fine as long as they are in the
        # data root.
        root = tmp_path / "data"
        root.mkdir()

        assert (
            resolve_persisted_path(str(root / "a.md"), str(root))
            == (root / "a.md").resolve()
        )

    def test_an_absolute_path_outside_the_root_is_refused(self):
        # `file_path` comes from the catalog or an operator-supplied JSON dump,
        # and its contents are sent to an external model provider. An escape here
        # is a file-disclosure channel, so containment is enforced before any
        # read — and it also stops a stale `..` path grounding a question in an
        # unrelated file.
        with pytest.raises(ValueError):
            resolve_persisted_path("/etc/passwd", "/srv/archi/data")

    def test_a_relative_path_escaping_the_root_is_refused(self):
        with pytest.raises(ValueError):
            resolve_persisted_path("../../etc/passwd", "/srv/archi/data")

    def test_a_symlink_pointing_out_of_the_root_is_refused(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        outside = tmp_path / "secret.md"
        outside.write_text("secret", encoding="utf-8")
        (root / "link.md").symlink_to(outside)

        with pytest.raises(ValueError):
            resolve_persisted_path("link.md", str(root))

    def test_a_self_referential_symlink_is_refused_by_name(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        loop = root / "loop.md"
        loop.symlink_to(loop)

        with pytest.raises(ValueError, match="loop.md"):
            resolve_persisted_path("loop.md", str(root))

    def test_a_symlink_loop_in_the_data_root_is_refused(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        loop_root = tmp_path / "loop_root"
        loop_root.symlink_to(loop_root)

        with pytest.raises(ValueError, match="loop_root"):
            resolve_persisted_path("a.md", str(loop_root))

    def test_a_loop_in_an_ancestor_component_is_refused(self, tmp_path, monkeypatch):
        # Python 3.13+ changed `Path.resolve()`: instead of raising RuntimeError
        # on a symlink loop it delegates to `os.path.realpath(strict=False)`,
        # which gives up at the looping component and returns the rest of the
        # path unexpanded. For `loop/doc.md` the result is `<root>/loop/doc.md`,
        # whose FINAL component is an ordinary (nonexistent) name — so a guard
        # that only inspects the final component sees no symlink and hands back
        # a path whose target is unknown. Verified against 3.13.13 and 3.14.5.
        #
        # The gate runs 3.11, where resolve() still raises and this input is
        # refused for the wrong reason, so the 3.13+ contract is simulated here
        # by pointing resolve() at realpath — exactly what 3.13 does — making
        # the branch reachable on every interpreter.
        root = tmp_path / "data"
        root.mkdir()
        loop = root / "loop"
        loop.symlink_to(loop)
        monkeypatch.setattr(Path, "resolve", lambda self: Path(os.path.realpath(self)))

        with pytest.raises(ValueError, match="loop"):
            resolve_persisted_path("loop/doc.md", str(root))

    def test_the_refusal_reason_is_identical_on_every_interpreter(
        self, tmp_path, monkeypatch
    ):
        # `_resolve_totally` promises one message and type on every
        # interpreter, but the two detection routes reached it separately: the
        # pre-3.13 route interpolated the RuntimeError's own text ("Symlink loop
        # from '<path>'") while the 3.13+ route emitted the literal "symlink
        # loop". Same input, different message depending on the interpreter.
        #
        # BOTH routes are stubbed explicitly rather than letting the host
        # interpreter pick one: on 3.13+ the unpatched call already takes the
        # realpath route, so comparing it against a realpath stub would compare
        # a route with itself and pass no matter how the pre-3.13 route behaves.
        # An unpatched real-loop refusal is covered by
        # test_a_self_referential_symlink_is_refused_by_name.
        root = tmp_path / "data"
        root.mkdir()
        loop = root / "loop"
        loop.symlink_to(loop)

        def raise_only_on_the_loop(self):
            # The data root resolves normally on both routes; only the looping
            # document path takes the interpreter-specific branch, so the two
            # messages are comparable (same `description`, same path).
            if "loop" in self.parts:
                raise RuntimeError(f"Symlink loop from {str(self)!r}")
            return Path(os.path.realpath(self))

        monkeypatch.setattr(Path, "resolve", raise_only_on_the_loop)
        with pytest.raises(ValueError) as pre_313_route:
            resolve_persisted_path("loop/doc.md", str(root))

        monkeypatch.setattr(Path, "resolve", lambda self: Path(os.path.realpath(self)))
        with pytest.raises(ValueError) as post_313_route:
            resolve_persisted_path("loop/doc.md", str(root))

        assert str(pre_313_route.value) == str(post_313_route.value)
        assert str(pre_313_route.value).endswith("cannot be resolved: symlink loop")

    def test_an_unprobeable_component_is_refused_not_raised_as_oserror(self, tmp_path):
        # Totality is the whole promise here, and it is not only about loops.
        # `Path.is_symlink()` swallows just ENOENT/ENOTDIR/EBADF/ELOOP, so an
        # overlong component raises ENAMETOOLONG — which is NOT a ValueError,
        # so the caller's `except ValueError` in
        # scripts/benchmarking/goldenset_maintenance.py would not convert it to
        # a per-row OperationalError and one bad row would abort the entire
        # maintenance run. A component that cannot be probed cannot be
        # certified loop-free, so it is refused by name like any other
        # unresolvable path.
        root = tmp_path / "data"
        root.mkdir()

        with pytest.raises(ValueError, match="cannot be resolved"):
            resolve_persisted_path("x" * 5000, str(root))

    def test_a_loop_erased_by_parent_traversal_is_refused(self, tmp_path):
        # A post-condition on the RESOLVED output cannot see a loop that `..`
        # erased. `loop/../safe.md` under `loop -> loop` is untraversable —
        # opening that pathname fails ELOOP — but `Path.resolve()` collapses the
        # `..` lexically against the unresolved loop and returns
        # `<root>/safe.md`, measured identically on 3.11.15, 3.12.13, 3.13.13
        # and 3.14.5. No component of THAT result is a symlink, so the guard
        # certified it fully resolved and handed back a readable neighbor of the
        # file the row actually named. Traversability is the kernel's verdict to
        # give, not something inferable from the collapsed result.
        root = tmp_path / "data"
        root.mkdir()
        (root / "safe.md").write_text("grounding text", encoding="utf-8")
        loop = root / "loop"
        loop.symlink_to(loop)

        with pytest.raises(ValueError, match="cannot be resolved: symlink loop"):
            resolve_persisted_path("loop/../safe.md", str(root))

    def test_a_trailing_separator_on_a_regular_file_is_refused(self, tmp_path):
        # `Path('safe.md/')` normalizes to `PosixPath('safe.md')` in the
        # constructor, so a probe fed the constructed path never sees the
        # spelling the row stored — and `open('<root>/safe.md/')` fails
        # NotADirectoryError (ENOTDIR) even though `safe.md` is a real,
        # readable file. The guard must refuse by the kernel's verdict on the
        # RAW spelling, not resolve a normalized rendering of it.
        root = tmp_path / "data"
        root.mkdir()
        (root / "safe.md").write_text("grounding text", encoding="utf-8")

        with pytest.raises(
            ValueError, match=r"safe\.md/'.*cannot be resolved: Not a directory"
        ):
            resolve_persisted_path("safe.md/", str(root))

    def test_a_trailing_dot_component_on_a_regular_file_is_refused(self, tmp_path):
        # Same erasure as the trailing separator above, reached through a
        # trailing `.` component instead: `Path('safe.md/.')` also normalizes
        # to `PosixPath('safe.md')`, but `open('<root>/safe.md/.')` fails the
        # same ENOTDIR.
        root = tmp_path / "data"
        root.mkdir()
        (root / "safe.md").write_text("grounding text", encoding="utf-8")

        with pytest.raises(
            ValueError, match=r"safe\.md/\.'.*cannot be resolved: Not a directory"
        ):
            resolve_persisted_path("safe.md/.", str(root))

    def test_a_missing_component_erased_by_parent_traversal_is_refused(self, tmp_path):
        # The same substitution as test_a_loop_erased_by_parent_traversal, but
        # reached through the ENOENT that the guard deliberately tolerates. A
        # path that does not exist yet IS resolvable, so a stale row must pass
        # the guard and fail at the read — but that tolerance is only sound when
        # nothing can have been erased. `missing/../safe.md` collapses to
        # `<root>/safe.md`, discarding the very component the kernel could not
        # traverse: opening the stored pathname fails ENOENT on 3.11.15 and
        # 3.14.5, yet the guard returned the readable neighbour.
        #
        # `..` is what makes the difference, so it is what gates the tolerance —
        # not the errno, which is why this closes the class rather than one more
        # instance of it.
        root = tmp_path / "data"
        root.mkdir()
        (root / "safe.md").write_text("grounding text", encoding="utf-8")

        with pytest.raises(ValueError, match=r"missing component erased by '\.\.'"):
            resolve_persisted_path("missing/../safe.md", str(root))

    def test_a_deleted_document_still_reaches_the_read_to_fail_there(self, tmp_path):
        # The other side of that tolerance, pinned so the fix above cannot be
        # tightened into refusing every nonexistent path. A stale row pointing
        # at a deleted file has no `..`, so nothing can have been erased: it
        # resolves, containment passes, and the diagnostic stays where it has
        # always been — at the read.
        root = tmp_path / "data"
        (root / "web").mkdir(parents=True)

        assert resolve_persisted_path("web/deleted.md", str(root)) == (
            root / "web" / "deleted.md"
        )

    def test_a_malformed_path_is_refused_by_its_own_name(self, tmp_path):
        # An embedded NUL makes `Path.resolve()` raise ValueError itself, so the
        # refusal never reached this guard's one raise site. The TYPE was
        # already what the caller converts to a per-row OperationalError, but
        # the message ("embedded null byte" on 3.11, "lstat: embedded null
        # character in path" on 3.12+) names neither the offending `file_path`
        # nor that it was a persisted document — and it differs by interpreter.
        # An operator reading the run log could not locate the bad row.
        root = tmp_path / "data"
        root.mkdir()

        with pytest.raises(ValueError, match="persisted document") as refusal:
            resolve_persisted_path("a\x00b", str(root))

        # The path is interpolated with !r, so the NUL reaches the operator's
        # log as an escape rather than as a raw control byte — still locatable,
        # which is the point, without corrupting the line it is printed on.
        assert r"a\x00b" in str(refusal.value)
        assert str(refusal.value).endswith("cannot be resolved: malformed path")

    def test_an_unprobeable_component_is_refused_even_when_probes_stay_silent(
        self, tmp_path, monkeypatch
    ):
        # Python 3.14 defeats BOTH halves of the round-2 post-condition:
        # `Path.resolve()` gives up and returns the unresolved absolute spelling
        # instead of raising, and `Path.is_symlink()` returns False instead of
        # letting the OSError through. Measured on 3.14.5, the very input of
        # test_an_unprobeable_component_is_refused_not_raised_as_oserror RETURNS
        # a 5022-character path rather than refusing — the guard accepts a path
        # whose components it could not inspect. Both 3.14 behaviors are
        # simulated so the branch is reachable on the gate's 3.11, and the
        # overlong name keeps the case independent of the runner's uid (a
        # mode-000 parent proves nothing when the suite runs as root).
        root = tmp_path / "data"
        root.mkdir()
        monkeypatch.setattr(Path, "resolve", lambda self: Path(os.path.abspath(self)))
        monkeypatch.setattr(Path, "is_symlink", lambda self: False)

        with pytest.raises(ValueError, match="cannot be resolved"):
            resolve_persisted_path("x" * 5000, str(root))

    def test_a_symlink_swapped_in_after_resolution_is_refused(
        self, tmp_path, monkeypatch
    ):
        # `resolve()` and the traversability probe are two syscalls, so the tree
        # can change in between. The dangerous ordering: resolve() gives up on a
        # loop, then that loop is replaced by a symlink pointing OUT of the data
        # root before the probe runs. The probe now succeeds, so nothing refuses
        # on traversability, and the path handed back is spelled inside the root
        # — while the read would follow the new link outside it. Containment
        # cannot catch it either, because it compares the path as spelled.
        #
        # The race is simulated deterministically by pinning resolve() to the
        # output it produced BEFORE the swap, which is exactly what the losing
        # interleaving hands the guard. This is what the post-condition on the
        # resolved path is for, now that the probe handles every ordinary input.
        root = tmp_path / "data"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "doc.md").write_text("secret", encoding="utf-8")
        (root / "link").symlink_to(outside)
        monkeypatch.setattr(Path, "resolve", lambda self: Path(os.path.abspath(self)))

        with pytest.raises(
            ValueError, match="cannot be resolved: resolution left a symlink"
        ):
            resolve_persisted_path("link/doc.md", str(root))

    def test_a_sibling_root_prefix_is_not_treated_as_contained(self, tmp_path):
        # `/srv/data-old/x` starts with `/srv/data` as a string but is a
        # different directory; containment is by path component, not prefix.
        with pytest.raises(ValueError):
            resolve_persisted_path("/srv/data-old/x.md", "/srv/data")

    def test_a_row_without_a_file_path_has_no_persisted_document(self):
        assert resolve_persisted_path("", "/srv/archi/data") is None

    def test_the_corpus_read_carries_the_persisted_file_path(self):
        docs = read_corpus_docs(
            _rows({"url": f"{KB}/kb/a", "source_type": "web", "file_path": "web/a.md"})
        )

        assert docs[0].file_path == "web/a.md"

    def test_a_row_without_a_file_path_still_reads(self):
        # A JSON dump that omits the column must not crash the gap report; only
        # `--propose` needs the path, and it refuses when it is absent.
        docs = read_corpus_docs(_rows({"url": f"{KB}/kb/a", "source_type": "web"}))

        assert docs[0].file_path == ""


# --------------------------------------------------------------------------- #
# Fact drift (group 4) — hash tripwire, then LLM diff
# --------------------------------------------------------------------------- #
# Drift keys on a **live re-fetch**, not the persisted corpus (design D6): the
# corpus lags in-place edits, so judging drift there would silently pass a
# reference that is already stale against the authoritative page. The sign-off
# condition on that decision is what these tests pin — the live signal is
# measured through the ingest's own extraction, so markup churn cannot
# masquerade as a fact change.

KB_HTML = "<html><body><p>Add #SBATCH --gpus=1 for one GPU.</p></body></html>"
KB_HTML_RESTYLED = (
    "<html><body><div class='card'>\n  <p>Add #SBATCH --gpus=1 for one GPU.</p>\n"
    "</div></body></html>"
)
KB_HTML_CHANGED = "<html><body><p>Add #SBATCH --gpus=2 for one GPU.</p></body></html>"


def _fetcher_for(pages, calls=None, errors=None):
    """A fake page fetcher, recording calls so re-fetch counts can be asserted."""

    def fetch(url):
        if calls is not None:
            calls.append(url)
        if errors and url in errors:
            raise SitemapFetchError(errors[url])
        return pages[url]

    return fetch


KB_HOST = "docs.rc.fas.harvard.edu"


def _drift(bank, fetch, **kwargs):
    """`find_drift` with an allowlist — an empty one authorizes nothing."""
    kwargs.setdefault("allowed_hosts", [KB_HOST, "slurm.schedmd.com"])
    return find_drift(bank, fetch, **kwargs)


def _locked(*sources, hashes=None, **extra):
    """A locked bank row citing `sources`, optionally with stored baselines."""
    row = {
        "user_input": "How many GPUs?",
        "reference": "Add #SBATCH --gpus=1.",
        "sources": list(sources),
        "status": "locked",
    }
    if hashes is not None:
        row["source_hashes"] = hashes
    row.update(extra)
    return row


class TestExtractedTextDigest:
    """4.1 — hash the normalized extracted text, never the raw markup."""

    def test_digest_is_algorithm_labeled(self):
        assert page_digest(KB_HTML).startswith("sha256:")

    def test_markup_churn_does_not_move_the_digest(self):
        # Same sentence, different wrapper markup and indentation: a theme change
        # must not read as a fact change (design D6 sign-off condition).
        assert page_digest(KB_HTML) == page_digest(KB_HTML_RESTYLED)

    def test_a_changed_fact_moves_the_digest(self):
        assert page_digest(KB_HTML) != page_digest(KB_HTML_CHANGED)

    def test_normalization_absorbs_whitespace_only_edits(self):
        assert normalize_extracted_text(
            "a  b \r\n\n\n c\n"
        ) == normalize_extracted_text("a b\n\nc")

    def test_normalization_keeps_a_real_word_change(self):
        assert normalize_extracted_text("a b") != normalize_extracted_text("a c")

    def test_blank_extraction_refuses_rather_than_hashing_nothing(self):
        # Every failed extraction would otherwise hash to the SAME value, so a
        # page that stops converting would read as "unchanged" forever.
        with pytest.raises(DriftExtractionError):
            page_digest("<!-- nothing here -->")


class TestFindDrift:
    """4.1 / 4.2 — which rows are checked, and what a mismatch reports."""

    def test_matching_hashes_are_not_flagged(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert report.drifted == ()
        assert report.checked_rows == 1

    def test_a_changed_source_is_flagged_and_named(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({url: KB_HTML_CHANGED}))

        assert [row.row_index for row in report.drifted] == [0]
        assert [c.url for c in report.drifted[0].changed] == [url]

    def test_any_changed_source_flags_a_multi_source_row(self):
        stable, moved = f"{KB}/kb/a", f"{KB}/kb/b"
        bank = [
            _locked(
                stable,
                moved,
                hashes={stable: page_digest(KB_HTML), moved: page_digest(KB_HTML)},
            )
        ]

        report = _drift(bank, _fetcher_for({stable: KB_HTML, moved: KB_HTML_CHANGED}))

        assert [c.url for c in report.drifted[0].changed] == [moved]
        states = {c.url: c.state for c in report.drifted[0].checks}
        assert states[stable] == "unchanged"

    def test_a_draft_row_is_never_checked_even_with_a_moved_page(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft", source_hashes={url: page_digest(KB_HTML)})]
        calls = []

        report = _drift(bank, _fetcher_for({url: KB_HTML_CHANGED}, calls=calls))

        assert report.drifted == ()
        assert report.checked_rows == 0
        assert calls == []  # not even fetched: an unconfirmed row has no baseline

    def test_a_row_without_status_is_a_draft_and_is_skipped(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, source_hashes={url: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({url: KB_HTML_CHANGED}))

        assert report.drifted == ()

    def test_a_locked_source_less_row_is_skipped(self):
        # A `should_refuse` row carries no sources by design — nothing to drift
        # against, and locking it must not require a grounding hash.
        bank = [_locked(anchor_type="should_refuse")]

        report = _drift(bank, _fetcher_for({}))

        assert report.drifted == ()
        assert report.checked_rows == 0

    def test_a_missing_baseline_is_reported_not_called_drift(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url)]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert report.drifted == ()
        assert [c.url for c in report.unbaselined] == [url]

    def test_an_unknown_hash_algorithm_is_incomparable_not_clean(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: "crc32:deadbeef"})]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert report.drifted == ()
        assert [c.url for c in report.incomparable] == [url]

    def test_an_unlabeled_hash_is_incomparable(self):
        # A bare hex digest does not say how it was computed, and guessing is how
        # a whole bank silently compares against the wrong rule.
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: "a" * 64})]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert [c.url for c in report.incomparable] == [url]

    def test_a_fetch_failure_is_unreachable_never_unchanged(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({}, errors={url: "connection reset"}))

        assert report.drifted == ()
        assert [c.url for c in report.unreachable] == [url]
        assert "connection reset" in report.unreachable[0].detail

    def test_a_baseline_for_a_url_the_row_no_longer_cites_is_reported(self):
        current, dropped = f"{KB}/kb/a", f"{KB}/kb/gone"
        bank = [
            _locked(
                current,
                hashes={current: page_digest(KB_HTML), dropped: page_digest(KB_HTML)},
            )
        ]

        report = _drift(bank, _fetcher_for({current: KB_HTML}))

        assert report.rows[0].stale_baselines == (dropped,)

    def test_emptying_sources_entirely_still_reports_the_stale_baselines(self):
        # The maximal case of the bucket above: `sources` was cleared but the
        # map was left behind, so every recorded confirmation now refers to a
        # page the row no longer cites. Skipping the row as "source-less" hides
        # exactly the hand edit this bucket exists to catch.
        dropped = f"{KB}/kb/gone"
        bank = [_locked(hashes={dropped: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({}))

        assert [row.stale_baselines for row in report.rows] == [(dropped,)]

    def test_a_row_with_only_stale_baselines_counts_as_skipped_not_checked(self):
        # Nothing was fetched or compared, so the census must not imply it was.
        dropped = f"{KB}/kb/gone"
        bank = [_locked(hashes={dropped: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({}))

        assert report.checked_rows == 0
        assert report.skipped_rows == 1
        assert report.rows[0].checks == ()

    def test_a_legitimate_source_less_locked_row_produces_no_row(self):
        # A confirmed `should_refuse` anchor: empty `sources`, no baselines. The
        # spec makes this lockable on purpose, so it must stay silent.
        report = _drift([_locked()], _fetcher_for({}))

        assert report.rows == ()
        assert report.skipped_rows == 1

    def test_unusable_sources_do_not_hide_stale_baselines_either(self):
        # Same hole one branch further down: the sources list is non-empty but
        # every entry is junk, so no check is produced and the row would fall
        # through before the baselines were read.
        dropped = f"{KB}/kb/gone"
        bank = [_locked("", None, hashes={dropped: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({}))

        assert [row.stale_baselines for row in report.rows] == [(dropped,)]

    def test_a_draft_row_with_baselines_is_still_not_reported(self):
        # Drift is locked-only. A draft row's map is not a confirmation history,
        # so it is not a stale one either.
        dropped = f"{KB}/kb/gone"
        bank = [_locked(hashes={dropped: page_digest(KB_HTML)}, status="draft")]

        report = _drift(bank, _fetcher_for({}))

        assert report.rows == ()

    def test_every_source_url_is_fetched_once_per_run(self):
        url = f"{KB}/kb/gpu"
        digest = page_digest(KB_HTML)
        bank = [_locked(url, hashes={url: digest}) for _ in range(3)]
        calls = []

        _drift(bank, _fetcher_for({url: KB_HTML}, calls=calls))

        assert calls == [url]

    def test_a_failed_fetch_is_not_retried_for_every_row(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)}) for _ in range(3)]
        calls = []

        _drift(bank, _fetcher_for({}, calls=calls, errors={url: "timeout"}))

        assert calls == [url]

    def test_all_sources_unreachable_abstains(self):
        # No page was read, so "no drift" would be a false clean over the whole
        # bank — the same trap the orphan pass abstains on.
        a, b = f"{KB}/kb/a", f"{KB}/kb/b"
        bank = [
            _locked(a, hashes={a: page_digest(KB_HTML)}),
            _locked(b, hashes={b: page_digest(KB_HTML)}),
        ]

        report = _drift(bank, _fetcher_for({}, errors={a: "timeout", b: "timeout"}))

        assert report.abstained is True

    def test_one_unreachable_source_does_not_abstain(self):
        # Unlike the orphan inventory, a failure here is LOCAL: it affects only
        # the rows citing that URL, and those are reported individually.
        good, bad = f"{KB}/kb/a", f"{KB}/kb/b"
        bank = [
            _locked(good, hashes={good: page_digest(KB_HTML_CHANGED)}),
            _locked(bad, hashes={bad: page_digest(KB_HTML)}),
        ]

        report = _drift(bank, _fetcher_for({good: KB_HTML}, errors={bad: "timeout"}))

        assert report.abstained is False
        assert [row.row_index for row in report.drifted] == [0]
        assert [c.url for c in report.unreachable] == [bad]

    def test_the_bank_is_never_mutated(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]
        before = copy.deepcopy(bank)

        _drift(bank, _fetcher_for({url: KB_HTML_CHANGED}))

        assert bank == before


class TestDriftVerdict:
    """4.3 — the LLM diff fires only on a mismatch, and only ever advises."""

    def test_the_model_is_asked_only_about_changed_sources(self):
        stable, moved = f"{KB}/kb/a", f"{KB}/kb/b"
        bank = [
            _locked(
                stable,
                moved,
                hashes={stable: page_digest(KB_HTML), moved: page_digest(KB_HTML)},
            )
        ]
        prompts = []

        _drift(
            bank,
            _fetcher_for({stable: KB_HTML, moved: KB_HTML_CHANGED}),
            ask_llm=_recording_llm(
                prompts, '{"verdict": "broken", "explanation": "2 now"}'
            ),
        )

        assert len(prompts) == 1
        assert moved in prompts[0]
        assert stable not in prompts[0]

    def test_the_prompt_carries_the_question_reference_and_fresh_page(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]
        prompts = []

        _drift(
            bank,
            _fetcher_for({url: KB_HTML_CHANGED}),
            ask_llm=_recording_llm(prompts, '{"verdict": "broken"}'),
        )

        assert "How many GPUs?" in prompts[0]
        assert "Add #SBATCH --gpus=1." in prompts[0]
        assert "--gpus=2" in prompts[0]

    def test_no_model_leaves_the_finding_without_a_verdict(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({url: KB_HTML_CHANGED}))

        assert report.drifted[0].changed[0].verdict is None

    def test_a_holds_verdict_does_not_clear_the_finding(self):
        # The hash mismatch is the fact; the model only triages it. Letting a
        # "holds" reply drop the row would put a hallucination in charge of
        # whether a real change gets reviewed.
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]

        report = _drift(
            bank,
            _fetcher_for({url: KB_HTML_CHANGED}),
            ask_llm=_recording_llm([], '{"verdict": "holds", "explanation": "same"}'),
        )

        assert [row.row_index for row in report.drifted] == [0]
        assert report.drifted[0].changed[0].verdict.verdict == "holds"

    def test_an_unknown_verdict_word_becomes_unclear(self):
        assert parse_drift_verdict('{"verdict": "maybe"}').verdict == "unclear"

    def test_an_unparseable_reply_becomes_unclear_and_says_so(self):
        verdict = parse_drift_verdict("I think it's fine, honestly")

        assert verdict.verdict == "unclear"
        assert "not JSON" in verdict.explanation

    def test_a_model_failure_leaves_the_finding_standing(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]

        def exploding(_prompt):
            raise RuntimeError("provider 503")

        report = _drift(bank, _fetcher_for({url: KB_HTML_CHANGED}), ask_llm=exploding)

        assert [row.row_index for row in report.drifted] == [0]
        assert report.drifted[0].changed[0].verdict.verdict == "unclear"
        assert "provider 503" in report.drifted[0].changed[0].verdict.explanation


def _recording_llm(prompts, reply):
    def ask(prompt):
        prompts.append(prompt)
        return reply

    return ask


class TestDriftAgainstAHandEditedBank:
    """The bank is hand-authored, so every field can arrive malformed."""

    def test_a_non_string_stored_hash_reads_as_no_baseline(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: 12345})]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert [c.url for c in report.unbaselined] == [url]

    def test_unusable_source_entries_are_ignored(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked("", None, url, hashes={url: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert [c.url for c in report.rows[0].checks] == [url]

    def test_a_row_whose_sources_are_all_unusable_is_skipped(self):
        bank = [_locked("", None)]

        report = _drift(bank, _fetcher_for({}))

        assert report.checked_rows == 0
        assert report.skipped_rows == 1

    def test_a_repeated_source_is_checked_once(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, f"{url}/", hashes={url: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert [c.url for c in report.rows[0].checks] == [url]

    def test_a_truncated_digest_is_incomparable_not_drift(self):
        # A half-pasted `sha256:` value keeps the label but loses the digest. It
        # can never equal a fresh hash, so treating it as comparable reports a
        # page that did not move — and, with --model, buys an LLM call to
        # explain a change that never happened.
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: "sha256:9f86d0818"})]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert report.drifted == ()
        assert [c.url for c in report.incomparable] == [url]

    def test_a_non_hex_digest_of_the_right_length_is_incomparable(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: "sha256:" + "z" * 64})]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert [c.url for c in report.incomparable] == [url]

    def test_a_malformed_digest_is_never_shown_to_the_model(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: "sha256:9f86d0818"})]
        prompts = []

        _drift(
            bank,
            _fetcher_for({url: KB_HTML}),
            ask_llm=_recording_llm(prompts, '{"verdict": "broken"}'),
        )

        assert prompts == []

    def test_a_well_formed_digest_still_compares(self):
        # The guard must reject only malformed values, not tighten what counts
        # as a real baseline.
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert report.incomparable == ()
        assert report.drifted == ()

    def test_a_json_reply_that_is_not_an_object_is_unclear(self):
        assert parse_drift_verdict('["holds"]').verdict == "unclear"

    def test_a_long_page_is_truncated_in_the_prompt(self):
        prompt = build_drift_prompt("q?", "a", f"{KB}/kb/gpu", "x" * 40_000)

        assert "page truncated" in prompt
        assert len(prompt) < 40_000


class TestDriftFetchPolicy:
    """A `sources` URL is data, and drift turns it into an outbound request.

    The ingest's trust filter (`is_url_allowed`) lives in `expand_sitemaps`, NOT
    in `fetch_sitemap_text` — so reusing the ingest's *fetcher* inherits its
    redirect and size limits but none of its target policy. Drift has to apply
    the filter itself or it becomes a way to reach internal services from
    whatever host the tool runs on, and (with `--model`) to forward what they
    return to an external provider.
    """

    def test_a_loopback_source_is_refused_and_never_fetched(self):
        url = "http://127.0.0.1:8000/admin"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]
        calls = []

        report = _drift(bank, _fetcher_for({url: KB_HTML}, calls=calls))

        assert calls == []
        assert [c.url for c in report.refused] == [url]
        assert report.drifted == ()

    def test_a_link_local_metadata_endpoint_is_refused(self):
        url = "http://169.254.169.254/latest/meta-data/iam/security-credentials"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]
        calls = []

        _drift(bank, _fetcher_for({url: KB_HTML}, calls=calls))

        assert calls == []

    def test_a_private_range_source_is_refused(self):
        url = "http://10.0.0.5/internal"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]
        calls = []

        _drift(bank, _fetcher_for({url: KB_HTML}, calls=calls))

        assert calls == []

    def test_an_obfuscated_numeric_host_is_refused(self):
        # 2130706433 == 127.0.0.1; resolvers accept it, ipaddress will not
        # canonicalize it, so the ingest's filter rejects the whole shape.
        url = "http://2130706433/admin"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]
        calls = []

        _drift(bank, _fetcher_for({url: KB_HTML}, calls=calls))

        assert calls == []

    def test_a_non_http_scheme_is_refused(self):
        url = "file:///etc/passwd"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]
        calls = []

        _drift(bank, _fetcher_for({url: KB_HTML}, calls=calls))

        assert calls == []

    def test_a_refused_source_is_never_shown_to_the_model(self):
        url = "http://127.0.0.1:8000/admin"
        bank = [_locked(url, hashes={url: "sha256:" + "0" * 64})]
        prompts = []

        _drift(
            bank,
            _fetcher_for({url: KB_HTML}),
            ask_llm=_recording_llm(prompts, '{"verdict": "broken"}'),
        )

        assert prompts == []

    def test_an_allowlisted_host_is_fetched(self):
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]
        calls = []

        _drift(bank, _fetcher_for({url: KB_HTML}, calls=calls))

        assert calls == [url]

    def test_no_allowlist_authorizes_nothing(self):
        # Fail closed. Hostname policy cannot survive DNS rebinding, so the set
        # of hosts drift will dial has to be one an operator actually vouched
        # for — not "whatever the bank happens to name".
        url = f"{KB}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]
        calls = []

        report = find_drift(bank, _fetcher_for({url: KB_HTML}, calls=calls))

        assert calls == []
        assert [c.url for c in report.refused] == [url]

    def test_an_allowlist_restricts_which_hosts_drift_will_contact(self):
        listed, other = f"{KB}/kb/gpu", "https://slurm.schedmd.com/mpi"
        bank = [
            _locked(listed, hashes={listed: page_digest(KB_HTML)}),
            _locked(other, hashes={other: page_digest(KB_HTML)}),
        ]
        calls = []

        report = _drift(
            bank,
            _fetcher_for({listed: KB_HTML, other: KB_HTML}, calls=calls),
            allowed_hosts=["docs.rc.fas.harvard.edu"],
        )

        assert calls == [listed]
        assert [c.url for c in report.refused] == [other]

    def test_a_plaintext_source_is_refused_even_on_an_allowlisted_host(self):
        # The ingest's filter permits http, and TLS verification buys nothing on
        # a plaintext hop: anyone on the network path can substitute the page,
        # manufacture a drift finding, steer the advisory verdict, and — with
        # --model — choose the text sent to the provider. That is the exact risk
        # the fetcher verifies TLS to avoid, so drift must not dial the scheme
        # that defeats it.
        url = f"http://{KB_HOST}/kb/gpu"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]
        calls = []

        report = _drift(bank, _fetcher_for({url: KB_HTML}, calls=calls))

        assert calls == []
        assert [c.url for c in report.refused] == [url]

    def test_the_plaintext_refusal_names_the_scheme(self):
        # A refused URL is only useful if the report says which rule refused it.
        url = f"http://{KB_HOST}/kb/gpu"

        report = _drift([_locked(url)], _fetcher_for({url: KB_HTML}))

        assert "https" in report.refused[0].detail


class TestDriftRetainsBoundedText:
    """The tripwire runs over a whole bank; retained text has to be bounded."""

    def test_a_huge_page_is_hashed_whole_but_retained_truncated(self):
        url = f"{KB}/kb/big"
        html = "<html><body><p>" + ("word " * 40_000) + "</p></body></html>"
        bank = [_locked(url, hashes={url: "sha256:" + "0" * 64})]

        report = _drift(bank, _fetcher_for({url: html}))
        check = report.drifted[0].changed[0]

        # The digest must cover the WHOLE page — truncating before hashing would
        # make every edit past the cut invisible.
        assert check.fresh == page_digest(html)
        assert len(check.fresh_text) <= MAX_PROMPT_PAGE_CHARS + len(TRUNCATION_MARKER)

    def test_a_malformed_url_is_refused_rather_than_raising(self):
        # `urlparse(...).hostname` raises on an unclosed IPv6 bracket. A read-only
        # pass must refuse it, not die on one bad row in a hand-edited bank.
        assert is_fetchable_source("http://[::1/admin") is False


class TestDriftAbstainsWhenNothingWasRead:
    """Abstention keys on "no page was read", not on how the reading failed.

    A refusal never reaches the fetch cache, so a cache-shaped rule reported a
    fully-refused run — a mistyped `--allowed-hosts`, say — as a clean zero-drift
    pass. The honest question is whether any source was actually read.
    """

    def test_every_source_refused_abstains(self):
        url = "http://127.0.0.1:9000/admin"
        bank = [_locked(url, hashes={url: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert report.abstained is True

    def test_a_mix_of_refused_and_unreachable_with_no_reads_abstains(self):
        refused, dead = "http://10.0.0.5/x", f"{KB}/kb/gone"
        bank = [
            _locked(refused, hashes={refused: page_digest(KB_HTML)}),
            _locked(dead, hashes={dead: page_digest(KB_HTML)}),
        ]

        report = _drift(bank, _fetcher_for({}, errors={dead: "timeout"}))

        assert report.abstained is True
        assert len(report.reasons) == 2

    def test_one_successful_read_is_enough_not_to_abstain(self):
        good, refused = f"{KB}/kb/a", "http://10.0.0.5/x"
        bank = [
            _locked(good, hashes={good: page_digest(KB_HTML)}),
            _locked(refused, hashes={refused: page_digest(KB_HTML)}),
        ]

        report = _drift(bank, _fetcher_for({good: KB_HTML}))

        assert report.abstained is False

    def test_a_bank_with_nothing_to_check_does_not_abstain(self):
        # No locked rows is "nothing to do", not "the run failed".
        report = _drift([_row(f"{KB}/kb/a")], _fetcher_for({}))

        assert report.abstained is False


class TestDriftEvidenceIsHonestAboutTruncation:
    """Round-1's memory fix silently disabled the prompt's truncation marker.

    `_fetch_extract` began capping the retained text at exactly
    `MAX_PROMPT_PAGE_CHARS`, so `build_drift_prompt`'s own `len(text) > MAX`
    check stopped firing and the model was handed a prefix presented as the
    whole page. If the contradicting sentence sits past the cut, the verdict can
    read "holds" off incomplete evidence — a false reassurance on exactly the
    long pages hardest to check by eye.
    """

    def test_retained_text_says_it_was_cut(self):
        url = f"{KB}/kb/big"
        html = "<html><body><p>" + ("word " * 40_000) + "</p></body></html>"
        bank = [_locked(url, hashes={url: "sha256:" + "0" * 64})]

        report = _drift(bank, _fetcher_for({url: html}))

        assert report.drifted[0].changed[0].fresh_text.endswith(TRUNCATION_MARKER)

    def test_the_prompt_carries_the_marker_for_an_already_cut_page(self):
        url = f"{KB}/kb/big"
        html = "<html><body><p>" + ("word " * 40_000) + "</p></body></html>"
        bank = [_locked(url, hashes={url: "sha256:" + "0" * 64})]
        prompts = []

        _drift(
            bank,
            _fetcher_for({url: html}),
            ask_llm=_recording_llm(prompts, '{"verdict": "unclear"}'),
        )

        assert TRUNCATION_MARKER.strip() in prompts[0]

    def test_truncation_is_idempotent_not_re_cut(self):
        # The prompt builder must not chop the marker back off text that arrived
        # already truncated.
        once = _truncate_page_text("x" * (MAX_PROMPT_PAGE_CHARS * 2))

        assert _truncate_page_text(once) == once

    def test_a_short_page_is_left_alone(self):
        assert _truncate_page_text("short") == "short"


class TestMalformedLockedSource:
    """An unparseable URL on a locked row must be named, not quietly dropped."""

    def test_it_is_reported_as_refused_rather_than_skipped(self):
        bank = [_locked("http://[", status="locked")]

        report = _drift(bank, _fetcher_for({}))

        # Previously this row fell through to `skipped`, where the CLI labels it
        # "draft or source-less" — both false. A locked row with a broken source
        # is unjudgeable, and unjudgeable is a thing this report names.
        assert report.skipped_rows == 0
        assert [c.url for c in report.refused] == ["http://["]

    def test_a_good_source_alongside_a_broken_one_is_still_checked(self):
        good = f"{KB}/kb/gpu"
        bank = [_locked(good, "http://[", hashes={good: page_digest(KB_HTML)})]

        report = _drift(bank, _fetcher_for({good: KB_HTML}))

        states = {c.url: c.state for c in report.rows[0].checks}
        assert states[good] == "unchanged"
        assert states["http://["] == "refused"


class TestBaselineDrafts:
    """goldenset-baseline-drafts — opt-in hashing of draft rows before locking."""

    def test_baseline_drafts_fetches_and_hashes_draft_sources(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft")]
        calls = []

        report = _drift(
            bank, _fetcher_for({url: KB_HTML}, calls=calls), baseline_drafts=True
        )

        assert calls == [url]
        assert len(report.baseline_only) == 1
        assert isinstance(report.baseline_only[0], BaselineRow)
        assert report.baseline_only[0].source_hashes == {url: page_digest(KB_HTML)}

    def test_baseline_only_result_is_not_in_report_rows(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft")]

        report = _drift(bank, _fetcher_for({url: KB_HTML}), baseline_drafts=True)

        assert report.rows == ()

    def test_without_baseline_drafts_draft_sources_are_not_fetched(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft")]
        calls = []

        _drift(bank, _fetcher_for({url: KB_HTML}, calls=calls), baseline_drafts=False)

        assert calls == []

    def test_without_baseline_drafts_baseline_only_is_empty(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft")]

        report = _drift(bank, _fetcher_for({url: KB_HTML}))

        assert report.baseline_only == ()

    def test_baselined_draft_does_not_appear_in_drifted(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft")]

        report = _drift(bank, _fetcher_for({url: KB_HTML}), baseline_drafts=True)

        assert report.drifted == ()

    def test_baselined_draft_does_not_appear_in_unbaselined(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft")]

        report = _drift(bank, _fetcher_for({url: KB_HTML}), baseline_drafts=True)

        assert report.unbaselined == ()

    def test_baselined_draft_does_not_trigger_llm_call(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft")]
        llm_calls = []

        _drift(
            bank,
            _fetcher_for({url: KB_HTML}),
            ask_llm=_recording_llm(llm_calls, '{"verdict": "holds"}'),
            baseline_drafts=True,
        )

        assert llm_calls == []

    def test_baselined_draft_does_not_count_toward_checked_rows(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft")]

        report = _drift(bank, _fetcher_for({url: KB_HTML}), baseline_drafts=True)

        assert report.checked_rows == 0

    def test_baselined_draft_does_not_change_abstention_with_locked_row_unreachable(
        self,
    ):
        draft_url = f"{KB}/kb/draft"
        locked_url = f"{KB}/kb/locked"
        bank = [
            _row(draft_url, status="draft"),
            _locked(locked_url, hashes={locked_url: page_digest(KB_HTML)}),
        ]

        report_without = _drift(
            bank,
            _fetcher_for({draft_url: KB_HTML}, errors={locked_url: "timeout"}),
            baseline_drafts=False,
        )
        report_with = _drift(
            bank,
            _fetcher_for({draft_url: KB_HTML}, errors={locked_url: "timeout"}),
            baseline_drafts=True,
        )

        assert report_without.abstained == report_with.abstained

    def test_baseline_row_carries_row_index(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft")]

        report = _drift(bank, _fetcher_for({url: KB_HTML}), baseline_drafts=True)

        assert report.baseline_only[0].row_index == 0

    def test_multi_source_draft_row_hashes_all_sources(self):
        url_a = f"{KB}/kb/a"
        url_b = f"{KB}/kb/b"
        bank = [_row(url_a, url_b, status="draft")]

        report = _drift(
            bank,
            _fetcher_for({url_a: KB_HTML, url_b: KB_HTML_RESTYLED}),
            baseline_drafts=True,
        )

        assert set(report.baseline_only[0].source_hashes) == {url_a, url_b}

    def test_enabling_baseline_drafts_leaves_skipped_rows_unchanged(self):
        url = f"{KB}/kb/gpu"
        bank = [_row(url, status="draft")]

        without = _drift(bank, _fetcher_for({url: KB_HTML}), baseline_drafts=False)
        with_baselines = _drift(
            bank, _fetcher_for({url: KB_HTML}), baseline_drafts=True
        )

        # Asking for hash output must not move a detection metric. The row is
        # excluded from drift checking either way, so it stays skipped — else the
        # CLI's locked-row summary silently stops accounting for baselined drafts
        # and a one-draft bank reports 0 skipped while skipping one.
        assert with_baselines.skipped_rows == without.skipped_rows == 1

    def test_unreachable_draft_source_is_recorded_as_missing(self):
        reachable = f"{KB}/kb/a"
        dead = f"{KB}/kb/b"
        bank = [_row(reachable, dead, status="draft")]

        report = _drift(
            bank,
            _fetcher_for({reachable: KB_HTML}, errors={dead: "timeout"}),
            baseline_drafts=True,
        )

        # A draft has no stored baseline to fall back on, so a dropped source is
        # not recoverable from the row itself — it has to be named, or the block
        # invites a paste that locks the row with a hole in it.
        row = report.baseline_only[0]
        assert row.source_hashes == {reachable: page_digest(KB_HTML)}
        assert row.missing == (dead,)

    def test_refused_draft_source_is_recorded_as_missing(self):
        allowed = f"{KB}/kb/a"
        offlist = "https://example.com/kb/b"
        bank = [_row(allowed, offlist, status="draft")]

        report = _drift(bank, _fetcher_for({allowed: KB_HTML}), baseline_drafts=True)

        row = report.baseline_only[0]
        assert row.source_hashes == {allowed: page_digest(KB_HTML)}
        assert row.missing == (offlist,)

    def test_unparseable_draft_source_is_recorded_as_missing(self):
        good = f"{KB}/kb/a"
        bank = [_row(good, "http://[", status="draft")]

        report = _drift(bank, _fetcher_for({good: KB_HTML}), baseline_drafts=True)

        assert report.baseline_only[0].missing == ("http://[",)

    def test_a_fully_hashed_draft_reports_nothing_missing(self):
        url_a = f"{KB}/kb/a"
        url_b = f"{KB}/kb/b"
        bank = [_row(url_a, url_b, status="draft")]

        report = _drift(
            bank,
            _fetcher_for({url_a: KB_HTML, url_b: KB_HTML_RESTYLED}),
            baseline_drafts=True,
        )

        # Keeps the three assertions above honest: they would also pass against a
        # `missing` that is simply always populated.
        assert report.baseline_only[0].missing == ()
