"""A report must render the run's answers as text, and label its counts truthfully.

Two defects found by Codex review on #274, both in ``format_html_output`` and both
visible in artifacts already committed:

* Answer and reference-answer text was embedded raw. FASRC documentation is full
  of command placeholders — ``<jobid>``, ``<rcusername>``, ``<partition>`` — and a
  browser parses those as unknown elements and shows nothing. An answer reading
  ``salloc -p <partition> ...`` renders as ``salloc -p ...``, silently deleting
  the argument the reader needs. The module already imports ``html`` and escapes
  everywhere else in this function; these two sites were the gap.
* The retrieval summary computed its "incorrect" bucket as a residual --
  ``total - fully_correct - partial`` -- which counts questions where none of the
  *expected* sources were retrieved, then labelled it "no sources found", which
  claims nothing was retrieved at all. In the artifact Codex cited, all 106
  scored questions retrieved documents and none retrieved zero, so the label
  invited the reader to diagnose a retrieval outage that had not happened.
"""

from src.utils.generate_benchmark_report import format_html_output

CONFIG = {"services": {"benchmarking": {"modes": ["RAGAS", "SOURCES"]}}}
#: Escaping is about the per-question answer blocks, so those tests use a config
#: without SOURCES to keep the retrieval summary out of the page entirely.
RAGAS_ONLY = {"services": {"benchmarking": {"modes": ["RAGAS"]}}}


def _render(questions, total_results=None, config=None):
    return format_html_output(
        config or CONFIG,
        "configs/config.yaml",
        "2026-08-18",
        questions,
        total_results or {},
        provenance=None,
    )


def _render_ragas(questions):
    return _render(questions, config=RAGAS_ONLY)


class TestAnswerTextIsRenderedNotParsed:
    def test_a_command_placeholder_survives_into_the_page(self):
        html_out = _render_ragas(
            {
                "q1": {
                    "question": "How do I check my jobs?",
                    "answer": "Run squeue -u <rcusername> to list them.",
                    "reference_answer": "Use squeue -u <rcusername>.",
                }
            }
        )

        assert "&lt;rcusername&gt;" in html_out
        assert "<rcusername>" not in html_out

    def test_the_reference_answer_is_escaped_too(self):
        html_out = _render_ragas(
            {
                "q1": {
                    "question": "q",
                    "answer": "a",
                    "reference_answer": "sbatch -p <partition> job.sh",
                }
            }
        )

        assert "&lt;partition&gt;" in html_out
        assert "<partition>" not in html_out

    def test_an_answer_cannot_inject_markup_into_the_report(self):
        html_out = _render_ragas(
            {
                "q1": {
                    "question": "q",
                    "answer": "<script>alert(1)</script>",
                    "reference_answer": "ref",
                }
            }
        )

        assert "<script>alert(1)</script>" not in html_out
        assert "&lt;script&gt;" in html_out

    def test_ampersands_and_quotes_do_not_corrupt_the_page(self):
        html_out = _render_ragas(
            {
                "q1": {
                    "question": "q",
                    "answer": 'use A & B with --flag="x"',
                    "reference_answer": "ref",
                }
            }
        )

        assert "A &amp; B" in html_out

    def test_a_missing_answer_still_renders_its_placeholder(self):
        html_out = _render_ragas({"q1": {"question": "q"}})

        assert "N/A" in html_out


class TestTheRetrievalLabelsDescribeExpectedSources:
    TOTALS = {
        "source_scored_count": 106,
        "source_accuracy": 87 / 106,
        "relative_source_accuracy": 87 / 106,
    }

    def test_the_incorrect_bucket_does_not_claim_nothing_was_retrieved(self):
        """It is a residual over EXPECTED sources, not a retrieval-outage count."""
        html_out = _render({"q1": {"question": "q", "answer": "a"}}, self.TOTALS)

        assert "no sources found" not in html_out
        assert "19" in html_out

    def test_the_incorrect_bucket_names_expected_sources(self):
        html_out = _render({"q1": {"question": "q", "answer": "a"}}, self.TOTALS)

        assert "expected sources" in html_out

    def test_the_partial_bucket_also_names_expected_sources(self):
        totals = dict(self.TOTALS, source_accuracy=80 / 106)
        html_out = _render({"q1": {"question": "q", "answer": "a"}}, totals)

        assert "some sources found" not in html_out
        assert "expected sources" in html_out

    def test_the_fully_correct_count_is_unchanged(self):
        html_out = _render({"q1": {"question": "q", "answer": "a"}}, self.TOTALS)

        assert "87/106" in html_out
