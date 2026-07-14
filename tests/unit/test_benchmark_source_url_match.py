"""Gold-source URL matching must survive benign URL-form drift.

The bank authors canonical page URLs (`.../kb/running-jobs/`); the sitemap-driven
ingest stores them without the trailing slash (`.../kb/running-jobs`). An exact
string compare therefore matched *nothing* — every gold source scored as a miss
regardless of retrieval quality, and the failure was silent (a broken join is
indistinguishable from bad retrieval in the output).

These tests pin the canonicalization down on both sides while keeping genuinely
different URLs apart, so a slash can never again zero out the score.
"""

from src.bin.service_benchmark import Benchmarker


class _Doc:
    def __init__(self, metadata):
        self.metadata = metadata


def _benchmarker():
    """A Benchmarker without __init__ — get_source_results touches no instance state."""
    return Benchmarker.__new__(Benchmarker)


def _match(reference, retrieved_value, field="url"):
    """Run the real matcher against one reference and one retrieved document."""
    result = {"source_documents": [_Doc({field: retrieved_value})]}
    return _benchmarker().get_source_results(result, [{field: reference}])[0]


def test_authored_trailing_slash_matches_ingested_url_without_one():
    # The live bug: bank says ".../running-jobs/", corpus says ".../running-jobs".
    assert _match(
        "https://docs.rc.fas.harvard.edu/kb/running-jobs/",
        "https://docs.rc.fas.harvard.edu/kb/running-jobs",
    )


def test_ingested_trailing_slash_matches_authored_url_without_one():
    # The mirror case — canonicalization must apply to BOTH sides, not just one.
    assert _match(
        "https://docs.rc.fas.harvard.edu/kb/running-jobs",
        "https://docs.rc.fas.harvard.edu/kb/running-jobs/",
    )


def test_surrounding_whitespace_does_not_break_the_match():
    assert _match(
        "  https://slurm.schedmd.com/sbatch.html  ",
        "https://slurm.schedmd.com/sbatch.html",
    )


def test_exact_match_still_matches():
    assert _match(
        "https://docs.rc.fas.harvard.edu/kb/cluster-storage",
        "https://docs.rc.fas.harvard.edu/kb/cluster-storage",
    )


def test_different_articles_still_do_not_match():
    # Canonicalization must not become over-matching: a boost to recall bought by
    # collapsing distinct pages would be worse than the bug it fixes.
    assert not _match(
        "https://docs.rc.fas.harvard.edu/kb/running-jobs",
        "https://docs.rc.fas.harvard.edu/kb/cluster-storage",
    )


def test_prefix_urls_are_not_conflated():
    # ".../python" must not match ".../python-packages" — trailing-slash handling
    # must strip a slash, never do prefix matching.
    assert not _match(
        "https://docs.rc.fas.harvard.edu/kb/python",
        "https://docs.rc.fas.harvard.edu/kb/python-packages",
    )


def test_non_url_match_fields_are_unaffected():
    assert _match("running_jobs.md", "running_jobs.md", field="file_name")
    assert not _match("running_jobs.md", "cluster_storage.md", field="file_name")


def test_list_valued_metadata_field_is_canonicalized_too():
    # Some retrievers surface a list of urls on one document; the trailing-slash
    # fix must reach inside the list, not just the scalar case.
    result = {
        "source_documents": [
            _Doc({"url": ["https://docs.rc.fas.harvard.edu/kb/spack/", None]})
        ]
    }
    assert _benchmarker().get_source_results(
        result, [{"url": "https://docs.rc.fas.harvard.edu/kb/spack"}]
    ) == [True]


def test_missing_metadata_field_is_a_miss_not_a_crash():
    result = {"source_documents": [_Doc({"file_name": "x.md"})]}
    assert _benchmarker().get_source_results(
        result, [{"url": "https://docs.rc.fas.harvard.edu/kb/running-jobs"}]
    ) == [False]
