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
from pathlib import Path

import pytest

from src.data_manager.collectors.scrapers.sitemap_source import (
    SitemapFetchError,
    SitemapPolicy,
)
from src.utils.goldenset_maintenance import (
    CorpusDoc,
    Decline,
    LiveInventory,
    NearMiss,
    ProposalError,
    bank_source_urls,
    build_live_inventory,
    declined_urls,
    filter_docs,
    find_coverage_gaps,
    find_orphans,
    group_by_parent,
    parent_source,
    parse_candidates,
    propose_candidates,
    read_corpus_docs,
    read_declines,
    reconcile,
    reconciliation_key,
    resolve_persisted_path,
    with_decline,
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

    def test_survives_a_hand_edited_ledger(self):
        declines = read_declines(
            ["not-an-object", {"no_url": 1}, {"url": ""}, {"url": f"{KB}/kb/a"}]
        )

        assert [d.url for d in declines] == [f"{KB}/kb/a"]

    def test_a_non_list_ledger_reads_as_empty(self):
        assert read_declines({"url": f"{KB}/kb/a"}) == ()
        assert read_declines(None) == ()

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
