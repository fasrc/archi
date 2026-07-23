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

from src.utils.goldenset_maintenance import CorpusDoc, parent_source, read_corpus_docs


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
