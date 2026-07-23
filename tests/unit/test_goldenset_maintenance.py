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

from src.data_manager.collectors.scrapers.sitemap_source import (
    SitemapFetchError,
    SitemapPolicy,
)
from src.utils.goldenset_maintenance import (
    CorpusDoc,
    LiveInventory,
    NearMiss,
    bank_source_urls,
    build_live_inventory,
    filter_docs,
    find_coverage_gaps,
    find_orphans,
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
