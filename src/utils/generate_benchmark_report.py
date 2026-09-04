#!/usr/bin/env python3
"""
Compare Expected vs Actual Outputs from archi Benchmarking

This script helps evaluate benchmarking results by showing:
- The question asked
- archi's actual answer
- The expected (reference) answer
- Retrieved contexts
- RAGAS scores (if available)

Usage:
    python generate_benchmark_report.py <results.json>
    python generate_benchmark_report.py <results.json> --markdown_output out.md
    python generate_benchmark_report.py <results.json> --html_output out.html
    python generate_benchmark_report.py <results.json> --question 1

With no format flag, a markdown report is written next to the input JSON as
its ``<stem>_report.md`` sibling. ``--html_output`` opts into the HTML report.
"""

import argparse
import html
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path


def get_single_question_results(config_data):
    """Return the single question results regardless of key format."""
    return (
        config_data.get("single question results")
        or config_data.get("single_question_results")
        or {}
    )


def get_total_results(config_data):
    """Return the total results regardless of key format."""
    return config_data.get("total results") or config_data.get("total_results") or {}


def load_benchmark_results(filepath):
    """Load and parse benchmark results JSON"""
    with open(filepath, "r") as f:
        data = json.load(f)

    return data["benchmarking_results"], data["metadata"]


#: Distinguishes "this artifact was written before ingest timing existed" from
#: "no ingest was observed" (``None``). Both would read as a missing number
#: through a plain ``.get``, but they are different facts about the run and the
#: reports say so differently.
_INGEST_NOT_RECORDED = object()


def _format_seconds(seconds):
    """Seconds for arithmetic, h/m/s so a person can read it.

    An ingest is reported in seconds because that is what gets compared across
    campaign arms -- but "7351" is not a duration anyone can feel, and
    "2h 2m 31s" is.
    """
    total = int(round(seconds))
    if total < 60:
        return f"{total} s"
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{total} s ({hours}h {minutes}m {secs}s)"
    return f"{total} s ({minutes}m {secs}s)"


def parse_benchmark_results(results, metadata):
    """Parse benchmark results JSON"""

    result = results[0]

    questions = result.get("single_question_results", {})
    total_results = result.get("total_results", {})
    config_name = result.get("configuration_file", "Unknown configuration")
    config_data = result.get("configuration", {})
    timestamp = metadata.get("time", "Unknown time")

    # `config_data` stays the SELECTED file: the benchmark harness reads its own
    # settings from there (services.benchmarking.modes decides which sections
    # render below), so presenting the running configuration in its place would
    # trade one wrong label for another. Only the agent reads Postgres, so the
    # provenance of what the agent actually ran is reported alongside instead.
    provenance = {
        "running_configuration": result.get("running_configuration"),
        # None, not []. An artifact written before configuration provenance has no
        # such key, and [] would be read below as "compared, and they agreed" --
        # a positive claim about a comparison that never ran, on exactly the
        # historical runs whose mislabelling prompted this work.
        "configuration_divergence": result.get("configuration_divergence"),
        "corpus_fingerprint_before": result.get("corpus_fingerprint_before"),
        "corpus_fingerprint": result.get("corpus_fingerprint"),
        "corpus_unchanged_at_endpoints": result.get("corpus_unchanged_at_endpoints"),
        # Identity, alongside the divergence findings above. Divergence says
        # whether this report can be trusted; the digests say whether this run is
        # the same code and settings as another one, which is the question a
        # campaign actually asks. `config_version` is per arm and comes off the
        # record; `code_version` is per invocation and comes off the metadata.
        "config_version": result.get("config_version"),
        "code_version": metadata.get("code_version"),
        # Three readings, and the renderers keep them apart: the sentinel means
        # the artifact predates the field, None means no ingest was observed
        # (the run reused an existing corpus), a float means seconds. A plain
        # .get() would collapse the first two into one wrong claim.
        "ingest_wall_seconds": result.get("ingest_wall_seconds", _INGEST_NOT_RECORDED),
    }

    return config_data, config_name, timestamp, questions, total_results, provenance


def format_provenance_html(provenance):
    """Render whether the report can be trusted to describe the run.

    The selected configuration and the one the agent actually used can differ:
    the agent reads Postgres while the harness writes and reads a YAML file. A
    report that showed only the file reported a run executed at
    ``context_window: 8192`` as ``32768``. This block is what makes that visible
    to the person reading the report rather than only to a container log.
    """
    if not provenance:
        return ""

    divergence = provenance.get("configuration_divergence")
    if divergence:
        config_line = (
            "<p class='provenance-alert'>The run did <strong>not</strong> use the "
            "selected configuration. Settings that differ between the selected "
            "file and what the agent read:</p><ul>"
            + "".join(f"<li><code>{item}</code></li>" for item in divergence)
            + "</ul>"
        )
    elif divergence is None:
        # Absence is not agreement. An empty list means the two were compared and
        # agreed; a missing key means no comparison was made at all.
        config_line = (
            "<p class='provenance-alert'>Whether the run used the selected "
            "configuration was <strong>not recorded</strong>: this artifact "
            "predates configuration provenance, so no comparison was made.</p>"
        )
    else:
        config_line = (
            "<p class='provenance-ok'>The configuration the agent read "
            "<strong>matches</strong> the selected file.</p>"
        )

    stable = provenance.get("corpus_unchanged_at_endpoints")
    before = provenance.get("corpus_fingerprint_before")
    after = provenance.get("corpus_fingerprint")
    if stable is True:
        # Deliberately weaker than "unchanged for the whole run". Two samples
        # prove only that the endpoints matched: a corpus that changed and
        # changed back while the questions ran would produce this same result.
        corpus_line = (
            "<p class='provenance-ok'>The corpus was the same at the start and "
            f"the end of the run (<code>{after}</code>). This does not rule out "
            "a change that was reverted in between.</p>"
        )
    elif stable is False:
        corpus_line = (
            "<p class='provenance-alert'>The corpus <strong>changed</strong> "
            "while the run was in progress, so its questions were not all "
            f"scored against the same documents (<code>{before}</code> &rarr; "
            f"<code>{after}</code>).</p>"
        )
    else:
        corpus_line = (
            "<p class='provenance-alert'>Corpus stability is "
            "<strong>unknown</strong>: it was not observed both before and "
            f"after the run (<code>{before}</code> &rarr; <code>{after}</code>)."
            "</p>"
        )

    ingest = provenance.get("ingest_wall_seconds", _INGEST_NOT_RECORDED)
    if ingest is _INGEST_NOT_RECORDED:
        ingest_line = (
            "<p class='provenance-alert'>Time to ingest is <strong>not "
            "recorded</strong>: this artifact predates the field.</p>"
        )
    elif ingest is None:
        ingest_line = (
            "<p class='provenance-ok'>Time to ingest: <strong>not "
            "measured</strong> &mdash; no ingest was observed while this run "
            "waited, which normally means it reused an existing corpus.</p>"
        )
    else:
        ingest_line = (
            "<p class='provenance-ok'>Time to ingest: "
            f"<strong>{_format_seconds(ingest)}</strong>, harness-observed "
            "(first poll reporting progress to the one reporting completion, "
            "so queue time is excluded but data-manager work either side of "
            "the ingest is not). Measured once before the sweep, so every arm "
            "of this run carries the same figure; if the corpus line above "
            "says the corpus changed, this is not the ingest that built "
            "it.</p>"
        )

    return (
        "<div class='provenance'><h2>Run provenance</h2>"
        + config_line
        + corpus_line
        + ingest_line
        + format_version_html(provenance)
        + "</div>"
    )


_NOT_RECORDED = "<em>not recorded &mdash; this artifact predates version stamping</em>"


def format_version_html(provenance):
    """Render the code and configuration identity of the run.

    Divergence and corpus stability, above, say whether this report describes its
    own run. These digests answer the question a campaign asks across runs: was
    this the same code, and the same settings, as that other arm? Equal digests
    mean equal inputs.

    Neither is derivable from ``git_info.last_commit``: ``archi create`` writes it
    once and freezes it, so every run between 2026-08-11 and 2026-08-17 reports
    ``0a157cdce0`` with an empty diff. The commit is shown, labelled, so a reader
    does not mistake it for the code this run executed.

    An artifact written before stamping says so rather than being filled in with
    a plausible guess.
    """
    if not provenance:
        return ""

    code = provenance.get("code_version") or {}
    config = provenance.get("config_version") or {}
    if not code and not config:
        return ""

    rows = []

    code_digest = code.get("digest")
    rows.append(
        "<li>Code version: "
        + (
            f"<code>{html.escape(str(code_digest))}</code>"
            if code_digest
            else _NOT_RECORDED
        )
        + "</li>"
    )
    commit = code.get("deploy_git_commit")
    if commit:
        dirty = " (dirty tree)" if code.get("deploy_git_dirty") else ""
        rows.append(
            f"<li>Deploy-time commit: <code>{html.escape(str(commit))}</code>{dirty} "
            "&mdash; frozen by <code>archi create</code>; it identifies the "
            "deploy, not the image this run used</li>"
        )

    config_digest = config.get("digest")
    rows.append(
        "<li>Config version: "
        + (
            f"<code>{html.escape(str(config_digest))}</code>"
            if config_digest
            else _NOT_RECORDED
        )
        + "</li>"
    )
    if config.get("source"):
        rows.append(f"<li>Config basis: {html.escape(str(config['source']))}</li>")

    key_settings = config.get("key_settings") or {}
    if key_settings:
        settings_rows = "".join(
            "<tr><td><code>{}</code></td><td><code>{}</code></td></tr>".format(
                html.escape(path),
                html.escape(
                    json.dumps(key_settings[path], sort_keys=True, default=repr)
                    if isinstance(key_settings[path], (dict, list))
                    else str(key_settings[path])
                ),
            )
            for path in sorted(key_settings)
        )
        settings_table = (
            "<p>Settings that define this arm:</p>"
            "<table class='provenance-settings'>"
            "<tr><th>Setting</th><th>Value</th></tr>" + settings_rows + "</table>"
        )
    else:
        settings_table = ""

    return "<ul>" + "".join(rows) + "</ul>" + settings_table


def format_total_duration(raw_duration):
    """Convert a raw duration value from LangChain messages into a readable string.

    LangChain providers differ in units; Ollama, for example, reports nanoseconds.
    Use simple magnitude-based heuristics and keep the raw value available for reference.
    """
    try:
        value = float(raw_duration)
    except (TypeError, ValueError):
        return None, None

    if value <= 0:
        return None, None

    if value >= 1_000_000_000:
        seconds = value / 1_000_000_000  # assume nanoseconds
        assumed_unit = "nanoseconds"
    elif value >= 1_000_000:
        seconds = value / 1_000_000  # assume microseconds
        assumed_unit = "microseconds"
    elif value >= 1_000:
        seconds = value / 1_000  # assume milliseconds
        assumed_unit = "milliseconds"
    else:
        seconds = value
        assumed_unit = "seconds"

    if seconds >= 1:
        friendly = f"{seconds:.2f}s"
    elif seconds >= 0.001:
        friendly = f"{seconds * 1000:.0f}ms"
    else:
        friendly = f"{seconds * 1_000_000:.0f}µs"

    return friendly, assumed_unit


def format_html_output(
    config_data, config_name, timestamp, questions, total_results, provenance=None
):
    """Format results as HTML for easier reading.

    ``provenance`` defaults to None so result files written before provenance was
    recorded still render.
    """

    html_parts = [
        """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Benchmark Results Comparison</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 30px;
                border-radius: 10px;
                margin-bottom: 30px;
            }
            .metrics {
                background: white;
                padding: 20px;
                border-radius: 10px;
                margin-bottom: 20px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .question-card {
                background: white;
                padding: 30px;
                border-radius: 10px;
                margin-bottom: 30px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            }
            .section {
                margin: 20px 0;
            }
            .section-title {
                font-weight: bold;
                font-size: 1.1em;
                margin-bottom: 10px;
                color: #667eea;
            }
            .answer-box {
                background: #f8f9fa;
                padding: 15px;
                border-radius: 5px;
                border-left: 4px solid #667eea;
                margin: 10px 0;
                white-space: pre-wrap;
                font-family: 'Monaco', 'Courier New', monospace;
                font-size: 0.9em;
            }
            .expected-box {
                border-left-color: #28a745;
            }
            .context-box {
                background: #fff3cd;
                padding: 10px;
                border-radius: 5px;
                margin: 5px 0;
                font-size: 0.85em;
            }
            .metrics-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin-top: 15px;
            }
            .metric-item {
                background: #f8f9fa;
                padding: 15px;
                border-radius: 5px;
                text-align: center;
            }
            .metric-value {
                font-size: 2em;
                font-weight: bold;
                color: #667eea;
            }
            .metric-label {
                font-size: 0.9em;
                color: #666;
                margin-top: 5px;
            }
            .score-low { color: #dc3545; }
            .score-medium { color: #ffc107; }
            .score-high { color: #28a745; }
        </style>
    </head>
    <body>
    """
    ]

    # Header
    html_parts.append(
        f"""
    <div class="header">
        <h1>📊 Benchmark Results Comparison</h1>
        <p><strong>Configuration:</strong> {config_name}</p>
        <p><strong>Timestamp:</strong> {timestamp}</p>
        <p><strong>Questions Processed:</strong> {len(questions)}</p>
    </div>
    {format_provenance_html(provenance)}
"""
    )

    # sources (retrieval accuracy) metrics
    if "SOURCES" in config_data.get("services", {}).get("benchmarking", {}).get(
        "modes", []
    ):

        # Retrieval Accuracy
        ret_accuracy = total_results.get("source_accuracy", None)
        # The scores were divided by the SOURCE-SCORABLE question count, which
        # excludes zero-source rows (e.g. the `should_refuse` anchor). Deriving the
        # count from len(questions) would disagree with the score it is derived
        # from. Older result files predate the key and used len(questions).
        ret_total = total_results.get("source_scored_count", len(questions))
        ret_correct = int(ret_total * ret_accuracy)

        if ret_accuracy:
            ret_accuracy *= 100
        ret_partial = total_results.get("relative_source_accuracy", None)
        ret_partial = int(ret_total * ret_partial) - ret_correct

        html_parts.append('<div class="metrics">')
        html_parts.append("<h2>🎯 Retrieval Accuracy</h2>")
        html_parts.append(
            '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; max-width: 900px; margin: 0 auto;">'
        )

        # Fully Correct
        score_class = (
            "score-low"
            if ret_accuracy < 50
            else "score-medium" if ret_accuracy < 80 else "score-high"
        )
        html_parts.append(
            f"""
            <div class="metric-item">
                <div class="metric-value {score_class}">{ret_accuracy:.1f}%</div>
                <div class="metric-label">Fully Correct: {ret_correct}/{ret_total}</div>
            </div>
        """
        )

        # Partially Correct
        if ret_partial > 0:
            html_parts.append(
                f"""
                <div class="metric-item">
                    <div class="metric-value score-medium">{ret_partial}</div>
                    <div class="metric-label">Partially Correct (some expected sources retrieved)</div>
                </div>
            """
            )

        # Incorrect. A residual over the EXPECTED sources, so it counts questions
        # where none of the expected sources were among those retrieved -- NOT
        # questions where retrieval returned nothing. The old label claimed the
        # latter: in benchmarking-ragas-205-20260817_040939 all 106 scored
        # questions retrieved documents and none retrieved zero, yet the report
        # announced "19 Incorrect (no sources found)", inviting the reader to
        # diagnose a retrieval outage that had not happened.
        ret_incorrect = ret_total - ret_correct - ret_partial
        if ret_incorrect > 0:
            html_parts.append(
                f"""
                <div class="metric-item">
                    <div class="metric-value score-low">{ret_incorrect}</div>
                    <div class="metric-label">Incorrect (no expected sources retrieved)</div>
                </div>
            """
            )

        html_parts.append("</div></div>")

    if "RAGAS" in config_data.get("services", {}).get("benchmarking", {}).get(
        "modes", []
    ):

        # Aggregate RAGAS Metrics
        if total_results:
            html_parts.append('<div class="metrics">')
            html_parts.append("<h2>Aggregate RAGAS Metrics</h2>")
            html_parts.append('<div class="metrics-grid">')
            for metric, value in total_results.items():
                if "aggregate" in metric:
                    clean_name = (
                        metric.replace("aggregate_", "").replace("_", " ").title()
                    )
                    score_class = (
                        "score-low"
                        if value < 0.5
                        else "score-medium" if value < 0.7 else "score-high"
                    )
                    html_parts.append(
                        f"""
                    <div class="metric-item">
                        <div class="metric-value {score_class}">{value:.3f}</div>
                        <div class="metric-label">{clean_name}</div>
                    </div>
                    """
                    )
            html_parts.append("</div></div>")

    # Each Question
    for i, (qid, q_data) in enumerate(questions.items(), 1):
        html_parts.append(f'<div class="question-card">')
        html_parts.append(f"<h2>Question {i}: {qid}</h2>")

        # Question
        html_parts.append(f'<div class="section">')
        html_parts.append(f'<div class="section-title">❓ Question</div>')
        html_parts.append(f'<p>{q_data["question"]}</p>')
        html_parts.append(f"</div>")

        # reference sources
        reference_sources_metadata = q_data.get("reference_sources_metadata", [])
        reference_sources_match_fields = q_data.get(
            "reference_sources_match_fields", []
        )
        expected_sources = []
        for ref_source, match_field in zip(
            reference_sources_metadata, reference_sources_match_fields
        ):
            expected_sources.append(ref_source[match_field])
        found_sources = [
            source
            for i, source in enumerate(expected_sources)
            # Degraded/failed rows are never source-scored, so the harness does
            # not stamp `matched` on their reference metadata; treat an absent
            # flag as a miss rather than crashing report generation.
            if reference_sources_metadata[i].get("matched")
        ]

        # retrieved sources
        sources_metadata = q_data.get("sources_metadata", [])
        retrieved_sources = [
            s.get("display_name") or s.get("file_name") or "" for s in sources_metadata
        ]

        # Check if any expected source was retrieved
        expected_sources_set = set(expected_sources)

        retrieval_status = "none"
        if (
            len(found_sources) == len(expected_sources_set)
            and len(expected_sources_set) > 0
        ):
            retrieval_status = "full"
        elif len(found_sources) > 0:
            retrieval_status = "partial"

        if expected_sources:
            if retrieval_status == "full":
                status_class = "score-high"
                status_icon = "✅"
                status_text = "FULLY CORRECT"
            elif retrieval_status == "partial":
                status_class = "score-medium"
                status_icon = "⚠️"
                status_text = f"PARTIALLY CORRECT ({len(found_sources)}/{len(expected_sources_set)} sources found)"
            else:
                status_class = "score-low"
                status_icon = "❌"
                status_text = "INCORRECT"

            # Display expected sources
            expected_display = ", ".join(expected_sources)

            html_parts.append(f'<div class="section">')
            html_parts.append(f'<div class="section-title">🎯 Retrieval Check</div>')
            html_parts.append(
                f'<div style="background: #f8f9fa; padding: 15px; border-radius: 5px;">'
            )
            html_parts.append(
                f"<p><strong>Expected Document(s):</strong> {expected_display}</p>"
            )
            html_parts.append(
                f'<p><strong>Retrieved Documents:</strong> {", ".join(retrieved_sources) if retrieved_sources else "None"}</p>'
            )
            html_parts.append(
                f'<p><strong class="{status_class}">{status_icon} Status: {status_text}</strong></p>'
            )
            html_parts.append(f"</div>")
            html_parts.append(f"</div>")

        # archi's Answer
        html_parts.append(f'<div class="section">')
        html_parts.append(f'<div class="section-title">🤖 archi\'s Answer</div>')
        # Escaped: FASRC documentation is full of command placeholders such as
        # `<jobid>` and `<rcusername>`, which a browser parses as unknown elements
        # and renders as nothing -- silently deleting the argument the reader
        # needs from the command they were told to run.
        html_parts.append(
            f'<div class="answer-box">'
            f'{html.escape(str(q_data.get("answer", "N/A")))}</div>'
        )
        html_parts.append(f"</div>")

        # Expected Answer
        html_parts.append(f'<div class="section">')
        html_parts.append(f'<div class="section-title">✅ Expected Answer</div>')
        html_parts.append(
            f'<div class="answer-box expected-box">'
            f'{html.escape(str(q_data.get("reference_answer", "N/A")))}</div>'
        )
        html_parts.append(f"</div>")

        # Expected Documents/Sources
        if expected_sources:
            html_parts.append(f'<div class="section">')
            html_parts.append(
                f'<div class="section-title">🎯 Expected Source Documents</div>'
            )
            html_parts.append(
                f'<div style="background: #e8f5e9; border-left: 4px solid #4CAF50; padding: 15px; border-radius: 5px;">'
            )
            html_parts.append(f'<ul style="margin: 0; padding-left: 20px;">')
            for source in expected_sources:
                html_parts.append(
                    f'<li style="padding: 5px 0;"><strong>{source}</strong></li>'
                )
            html_parts.append(f"</ul>")
            html_parts.append(f"</div>")
            html_parts.append(f"</div>")

        # Retrieved Contexts/Documents
        contexts = q_data.get("contexts", [])
        if contexts:
            html_parts.append(f'<div class="section">')
            html_parts.append(
                f'<div class="section-title">📚 Retrieved Documents ({len(contexts)})</div>'
            )
            for j, ctx in enumerate(contexts, 1):
                # Extract ticket ID from context
                ticket_id = retrieved_sources[j - 1]
                ticket_badge = (
                    f'<span style="background: #2196F3; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.85em; margin-left: 10px;">{ticket_id}</span>'
                    if ticket_id
                    else ""
                )

                # Parse context if it's a Document representation; shared with
                # the markdown formatter so a parsing fix lands in both.
                ctx_text = extract_context_text(ctx)

                # Truncate if too long for display
                display_text = (
                    ctx_text[:500] + "..." if len(ctx_text) > 500 else ctx_text
                )
                full_text = ctx_text.replace("<", "&lt;").replace(">", "&gt;")

                html_parts.append(
                    f"""
                <div class="context-box" style="background: #f8f9fa; border-left: 3px solid #2196F3; padding: 15px; margin: 10px 0; border-radius: 5px;">
                    <div style="font-weight: bold; margin-bottom: 8px;">Document {j}{ticket_badge}</div>
                    <div style="font-size: 0.9em; white-space: pre-wrap; font-family: 'Courier New', monospace;">{display_text}</div>
                    {f'<details style="margin-top: 10px;"><summary style="cursor: pointer; color: #667eea;">Show full document</summary><pre style="margin-top: 10px; font-size: 0.85em; overflow-x: auto;">{full_text}</pre></details>' if len(ctx_text) > 500 else ''}
                </div>
                """
                )
            html_parts.append(f"</div>")

        # Agent message trace
        messages = q_data.get("messages", [])
        if messages:
            html_parts.append(f'<div class="section">')
            html_parts.append(
                f'<div class="section-title">💬 Agent Messages ({len(messages)})</div>'
            )
            html_parts.append(
                f'<div style="display: flex; flex-direction: column; gap: 12px;">'
            )
            for m_idx, message in enumerate(messages, 1):
                msg_type = message.get("type", "message")
                duration_display, duration_unit = format_total_duration(
                    message.get("total_duration")
                )
                duration_suffix = f" ({duration_display})" if duration_display else ""
                duration_title = ""
                if duration_display:
                    raw_duration = html.escape(str(message.get("total_duration")))
                    unit_hint = (
                        f"assumed {duration_unit}" if duration_unit else "raw value"
                    )
                    duration_title = (
                        f' title="Raw duration: {raw_duration} ({unit_hint})"'
                    )
                if msg_type == "tool_call":
                    title = f'🛠️ Tool Call #{m_idx}: {message.get("tool_name", "Unknown Tool")}{duration_suffix}'
                    args = message.get("tool_args")
                    body = (
                        f"<strong>Args:</strong> {html.escape(str(args))}"
                        if args is not None
                        else "<em>No arguments provided</em>"
                    )
                    border_color = "#17a2b8"
                elif msg_type == "ai_message":
                    title = f"🤖 Assistant Message #{m_idx}{duration_suffix}"
                    content = message.get("content", "")
                    body = html.escape(str(content)).replace("\\n", "<br>")
                    border_color = "#6f42c1"
                else:
                    title = f"📝 Message #{m_idx}{duration_suffix}"
                    fallback = message.get("content", message)
                    body = html.escape(str(fallback)).replace("\\n", "<br>")
                    border_color = "#343a40"
                html_parts.append(
                    f"""
                <div class="answer-box" style="background: #fff; border-left-color: {border_color};">
                    <div style="font-weight: 600; margin-bottom: 6px;"{duration_title}>{title}</div>
                    <div style="font-size: 0.9em; white-space: pre-wrap;">{body}</div>
                </div>
                """
                )
            html_parts.append(f"</div>")
            html_parts.append(f"</div>")

        # RAGAS Metrics
        ragas_metrics = {
            "answer_relevancy": "Answer Relevancy",
            "faithfulness": "Faithfulness",
            "context_precision": "Context Precision",
            "context_recall": "Context Recall",
            "answer_correctness": "Answer Correctness",
        }

        if "RAGAS" in config_data.get("services", {}).get("benchmarking", {}).get(
            "modes", []
        ):
            html_parts.append(f'<div class="section">')
            html_parts.append(f'<div class="section-title">📊 RAGAS Scores</div>')
            html_parts.append(f'<div class="metrics-grid">')
            for metric_key, metric_name in ragas_metrics.items():
                if metric_key in q_data and q_data[metric_key] is not None:
                    value = q_data[metric_key]
                    score_class = (
                        "score-low"
                        if value < 0.5
                        else "score-medium" if value < 0.7 else "score-high"
                    )
                    html_parts.append(
                        f"""
                    <div class="metric-item">
                        <div class="metric-value {score_class}">{value:.3f}</div>
                        <div class="metric-label">{metric_name}</div>
                    </div>
                    """
                    )
            html_parts.append(f"</div></div>")

        html_parts.append(f"</div>")

    html_parts.append("</body></html>")
    return "\n".join(html_parts)


# Inline (non-fenced) markdown fields are escaped with this table. The report is
# pasted into GitHub, so a data field must not be able to restructure it: no
# emphasis, code spans, links, tables, or raw HTML. The backslashes render
# invisibly on GitHub, so escaped text still reads as the original.
_MD_INLINE_ESCAPES = str.maketrans(
    {
        "\\": "\\\\",
        "`": "\\`",
        "*": "\\*",
        "_": "\\_",
        "~": "\\~",
        "[": "\\[",
        "]": "\\]",
        "|": "\\|",
        "@": "\\@",
        "<": "&lt;",
        ">": "&gt;",
    }
)


def md_escape(text):
    """Neutralize artifact-sourced text for inline markdown interpolation.

    Whitespace is collapsed onto one line, so only the FIRST character can sit
    at a line start when the field is rendered as its own paragraph — a leading
    block starter there would grow a heading or a list out of data, so it gets
    a backslash too.
    """
    escaped = " ".join(str(text).split()).translate(_MD_INLINE_ESCAPES)
    # GFM autolinks bare URLs (scheme://, any www. — parentheses count as
    # valid preceders — and emails via the @ escape above); an escaped colon
    # or dot cannot participate, so the payload stays plain text.
    escaped = escaped.replace("://", "\\://")
    escaped = re.sub(r"(?i)(www)\.", r"\1\\.", escaped)
    if escaped.startswith(("#", "-", "+")):
        escaped = "\\" + escaped
    else:
        # An ordered-list marker (`1. item` / `1) item`) is a block starter
        # too: escape its delimiter so the digits stay plain text.
        head = escaped.split(" ", 1)[0]
        if head[:-1].isdigit() and head[-1:] in ".)":
            escaped = head[:-1] + "\\" + head[-1] + escaped[len(head) :]
    return escaped


def fence(text):
    """Wrap artifact text in a code fence it cannot terminate.

    FASRC documentation is full of command placeholders such as ``<jobid>``,
    and answers can carry markdown of their own; a fence one backtick longer
    than the longest backtick run inside the text renders all of it literally.
    """
    text = str(text)
    longest = 0
    run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}text\n{text}\n{ticks}"


def code_span(text):
    """Wrap artifact data in an inline code span it cannot terminate.

    Markdown does not process backslashes inside code spans, so ``md_escape``
    is useless there; instead the delimiter is one backtick longer than the
    longest backtick run inside the data, space-padded per GFM so edge
    backticks render and the padding is stripped by the renderer.
    """
    text = " ".join(str(text).split())
    longest = 0
    run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    ticks = "`" * (longest + 1)
    return f"{ticks} {text} {ticks}"


def _score_badge(value):
    """The 0.5 / 0.7 thresholds the HTML report encodes as colors."""
    if value < 0.5:
        return "🔴"
    if value < 0.7:
        return "🟡"
    return "🟢"


def _score_cell(value):
    """A score cell: badged when finite, plainly unscored when not.

    ``build_ragas_aggregates`` emits ``float("nan")`` when nothing was
    scorable; NaN fails both threshold comparisons, so without this check an
    unscored run would wear the green badge and read as a success.
    """
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "n/a (unscored)"
    return f"{value:.3f} {_score_badge(value)}"


def extract_context_text(ctx):
    """Extract the page text from a retrieved-context entry.

    LangChain ``Document`` entries arrive as their ``repr`` string; this slices
    the ``page_content`` out of that shape and falls back to the raw string for
    anything else. Shared by the HTML and markdown formatters so a parsing fix
    lands in both report formats at once.
    """
    if isinstance(ctx, str) and ctx.startswith("page_content="):
        try:
            content_start = ctx.find("page_content='") + len("page_content='")
            content_end = ctx.find("' metadata=", content_start)
            if content_end != -1:
                return ctx[content_start:content_end]
            return ctx
        except Exception:
            return ctx
    return str(ctx)


_MD_NOT_RECORDED = "*not recorded — this artifact predates version stamping*"


def format_version_markdown(provenance):
    """Markdown mirror of ``format_version_html``: the run's identity digests."""
    if not provenance:
        return ""

    code = provenance.get("code_version") or {}
    config = provenance.get("config_version") or {}
    if not code and not config:
        return ""

    lines = []

    code_digest = code.get("digest")
    lines.append(
        "- Code version: "
        + (code_span(code_digest) if code_digest else _MD_NOT_RECORDED)
    )
    commit = code.get("deploy_git_commit")
    if commit:
        dirty = " (dirty tree)" if code.get("deploy_git_dirty") else ""
        lines.append(
            f"- Deploy-time commit: {code_span(commit)}{dirty} — frozen by "
            "`archi create`; it identifies the deploy, not the image this run used"
        )

    config_digest = config.get("digest")
    lines.append(
        "- Config version: "
        + (code_span(config_digest) if config_digest else _MD_NOT_RECORDED)
    )
    if config.get("source"):
        lines.append(f"- Config basis: {md_escape(config['source'])}")

    key_settings = config.get("key_settings") or {}
    if key_settings:
        lines += [
            "",
            "Settings that define this arm:",
            "",
            "| Setting | Value |",
            "|---|---|",
        ]
        for path in sorted(key_settings):
            value = key_settings[path]
            rendered = (
                json.dumps(value, sort_keys=True, default=repr)
                if isinstance(value, (dict, list))
                else str(value)
            )
            lines.append(f"| {md_escape(path)} | {md_escape(rendered)} |")

    return "\n".join(lines)


def format_provenance_markdown(provenance):
    """Markdown mirror of ``format_provenance_html``.

    Same content, same caveats: an empty divergence list means the selected and
    running configurations were compared and agreed; a missing key means no
    comparison was made, which is reported as such rather than as agreement.
    """
    if not provenance:
        return ""

    lines = ["## Run provenance", ""]

    divergence = provenance.get("configuration_divergence")
    if divergence:
        lines.append(
            "⚠️ The run did **not** use the selected configuration. Settings that "
            "differ between the selected file and what the agent read:"
        )
        lines.append("")
        lines += [f"- {code_span(item)}" for item in divergence]
    elif divergence is None:
        lines.append(
            "⚠️ Whether the run used the selected configuration was **not "
            "recorded**: this artifact predates configuration provenance, so no "
            "comparison was made."
        )
    else:
        lines.append(
            "✅ The configuration the agent read **matches** the selected file."
        )

    stable = provenance.get("corpus_unchanged_at_endpoints")
    before = provenance.get("corpus_fingerprint_before")
    after = provenance.get("corpus_fingerprint")
    lines.append("")
    if stable is True:
        lines.append(
            "✅ The corpus was the same at the start and the end of the run "
            f"({code_span(after)}). This does not rule out a change that was "
            "reverted in between."
        )
    elif stable is False:
        lines.append(
            "⚠️ The corpus **changed** while the run was in progress, so its "
            "questions were not all scored against the same documents "
            f"({code_span(before)} → {code_span(after)})."
        )
    else:
        lines.append(
            "⚠️ Corpus stability is **unknown**: it was not observed both before "
            f"and after the run ({code_span(before)} → {code_span(after)})."
        )

    ingest = provenance.get("ingest_wall_seconds", _INGEST_NOT_RECORDED)
    lines.append("")
    if ingest is _INGEST_NOT_RECORDED:
        lines.append(
            "⏱️ Time to ingest is **not recorded**: this artifact predates the " "field."
        )
    elif ingest is None:
        lines.append(
            "⏱️ Time to ingest: **not measured** — no ingest was observed while "
            "this run waited, which normally means it reused an existing "
            "corpus."
        )
    else:
        lines.append(
            f"⏱️ Time to ingest: **{_format_seconds(ingest)}**, "
            "harness-observed (first poll reporting progress to the one "
            "reporting completion, so queue time is excluded). Measured once "
            "before the sweep, so every arm of this run carries the same "
            "figure; if the corpus line above says the corpus changed, this is "
            "not the ingest that built it."
        )

    version_md = format_version_markdown(provenance)
    if version_md:
        lines += ["", version_md]

    return "\n".join(lines)


def format_markdown_output(
    config_data, config_name, timestamp, questions, total_results, provenance=None
):
    """Format results as GitHub-flavored markdown.

    Mirrors ``format_html_output`` section for section. ``provenance`` defaults
    to None so result files written before provenance was recorded still render.
    """
    modes = config_data.get("services", {}).get("benchmarking", {}).get("modes", [])

    parts = [
        "# Benchmark Results Comparison",
        "",
        f"**Configuration:** {md_escape(config_name)}  ",
        f"**Timestamp:** {md_escape(timestamp)}  ",
        f"**Questions Processed:** {len(questions)}",
    ]

    provenance_md = format_provenance_markdown(provenance)
    if provenance_md:
        parts += ["", provenance_md]

    if "SOURCES" in modes:
        ret_accuracy = total_results.get("source_accuracy", None)
        # Same denominator caveat as the HTML report: the score was divided by
        # the SOURCE-SCORABLE question count, so derive the count from the same
        # key (older artifacts predate it and used len(questions)).
        ret_total = total_results.get("source_scored_count", len(questions))
        # round(), not int(): the accuracy is stored as a float, and truncating
        # 15/22*22 == 14.999… would report one hit fewer than the run scored.
        ret_correct = round(ret_total * ret_accuracy)
        if ret_accuracy:
            ret_accuracy *= 100
        ret_partial = total_results.get("relative_source_accuracy", None)
        ret_partial = round(ret_total * ret_partial) - ret_correct

        parts += ["", "## 🎯 Retrieval Accuracy", ""]
        parts.append(
            f"- **Fully Correct:** {ret_correct}/{ret_total} ({ret_accuracy:.1f}%)"
        )
        if ret_partial > 0:
            parts.append(
                f"- **Partially Correct** (some expected sources retrieved): "
                f"{ret_partial}"
            )
        # A residual over the EXPECTED sources — questions where none of the
        # expected sources were retrieved, NOT questions with zero retrieval.
        ret_incorrect = ret_total - ret_correct - ret_partial
        if ret_incorrect > 0:
            parts.append(
                f"- **Incorrect** (no expected sources retrieved): {ret_incorrect}"
            )

    if "RAGAS" in modes and total_results:
        parts += [
            "",
            "## 📊 Aggregate RAGAS Metrics",
            "",
            "| Metric | Score |",
            "|---|---|",
        ]
        for metric, value in total_results.items():
            if "aggregate" in metric:
                clean_name = metric.replace("aggregate_", "").replace("_", " ").title()
                parts.append(f"| {md_escape(clean_name)} | {_score_cell(value)} |")

    ragas_metrics = {
        "answer_relevancy": "Answer Relevancy",
        "faithfulness": "Faithfulness",
        "context_precision": "Context Precision",
        "context_recall": "Context Recall",
        "answer_correctness": "Answer Correctness",
    }

    for i, (qid, q_data) in enumerate(questions.items(), 1):
        parts += ["", "---", "", f"## Question {i}: {md_escape(qid)}"]

        parts += ["", "### ❓ Question", "", md_escape(q_data["question"])]

        reference_sources_metadata = q_data.get("reference_sources_metadata", [])
        reference_sources_match_fields = q_data.get(
            "reference_sources_match_fields", []
        )
        expected_sources = []
        for ref_source, match_field in zip(
            reference_sources_metadata, reference_sources_match_fields
        ):
            expected_sources.append(ref_source[match_field])
        found_sources = [
            source
            for idx, source in enumerate(expected_sources)
            # Degraded/failed rows are never source-scored; an absent `matched`
            # flag is a miss, not a crash — same rule as the HTML report.
            if reference_sources_metadata[idx].get("matched")
        ]

        sources_metadata = q_data.get("sources_metadata", [])
        retrieved_sources = [
            s.get("display_name") or s.get("file_name") or "" for s in sources_metadata
        ]

        expected_sources_set = set(expected_sources)
        retrieval_status = "none"
        if (
            len(found_sources) == len(expected_sources_set)
            and len(expected_sources_set) > 0
        ):
            retrieval_status = "full"
        elif len(found_sources) > 0:
            retrieval_status = "partial"

        if expected_sources:
            if retrieval_status == "full":
                status_line = "✅ FULLY CORRECT"
            elif retrieval_status == "partial":
                status_line = (
                    f"⚠️ PARTIALLY CORRECT ({len(found_sources)}/"
                    f"{len(expected_sources_set)} sources found)"
                )
            else:
                status_line = "❌ INCORRECT"

            retrieved_display = (
                md_escape(", ".join(retrieved_sources)) if retrieved_sources else "None"
            )
            parts += [
                "",
                "### 🎯 Retrieval Check",
                "",
                f"**Expected Document(s):** {md_escape(', '.join(expected_sources))}  ",
                f"**Retrieved Documents:** {retrieved_display}  ",
                f"**Status:** {status_line}",
            ]

        parts += ["", "### 🤖 archi's Answer", "", fence(q_data.get("answer", "N/A"))]
        parts += [
            "",
            "### ✅ Expected Answer",
            "",
            fence(q_data.get("reference_answer", "N/A")),
        ]

        if expected_sources:
            parts += ["", "### 🎯 Expected Source Documents", ""]
            parts += [f"- **{md_escape(source)}**" for source in expected_sources]

        contexts = q_data.get("contexts", [])
        if contexts:
            parts += ["", f"### 📚 Retrieved Documents ({len(contexts)})"]
            for j, ctx in enumerate(contexts, 1):
                ticket_id = (
                    retrieved_sources[j - 1] if j - 1 < len(retrieved_sources) else ""
                )
                header = f"**Document {j}**"
                if ticket_id:
                    header += f" — {md_escape(ticket_id)}"
                ctx_text = extract_context_text(ctx)
                if len(ctx_text) > 500:
                    # Same contract as the HTML report's expander: the preview
                    # is followed by the COMPLETE text, so the evidence never
                    # requires opening the JSON artifact.
                    parts += ["", header, "", fence(ctx_text[:500] + "...")]
                    parts += [
                        "",
                        "<details><summary>Show full document</summary>",
                        "",
                        fence(ctx_text),
                        "",
                        "</details>",
                    ]
                else:
                    parts += ["", header, "", fence(ctx_text)]

        messages = q_data.get("messages", [])
        if messages:
            parts += ["", f"### 💬 Agent Messages ({len(messages)})"]
            for m_idx, message in enumerate(messages, 1):
                msg_type = message.get("type", "message")
                duration_display, _ = format_total_duration(
                    message.get("total_duration")
                )
                suffix = f" ({duration_display})" if duration_display else ""
                if msg_type == "tool_call":
                    tool_name = md_escape(message.get("tool_name", "Unknown Tool"))
                    title = f"🛠️ Tool Call #{m_idx}: {tool_name}{suffix}"
                    args = message.get("tool_args")
                    body = (
                        fence(args) if args is not None else "*No arguments provided*"
                    )
                elif msg_type == "ai_message":
                    title = f"🤖 Assistant Message #{m_idx}{suffix}"
                    body = fence(message.get("content", ""))
                else:
                    title = f"📝 Message #{m_idx}{suffix}"
                    body = fence(message.get("content", message))
                parts += ["", f"**{title}**", "", body]

        if "RAGAS" in modes:
            score_rows = [
                f"| {metric_name} | {_score_cell(q_data[metric_key])} |"
                for metric_key, metric_name in ragas_metrics.items()
                if metric_key in q_data and q_data[metric_key] is not None
            ]
            if score_rows:
                parts += [
                    "",
                    "### 📊 RAGAS Scores",
                    "",
                    "| Metric | Score |",
                    "|---|---|",
                ]
                parts += score_rows

    return "\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Compare expected vs actual outputs from archi benchmarking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate a markdown report next to the input (results_report.md)
  python generate_benchmark_report.py results.json

  # Save the markdown report to a specific path
  python generate_benchmark_report.py results.json --markdown_output report.md

  # Opt into the HTML report instead
  python generate_benchmark_report.py results.json --html_output report.html
        """,
    )

    parser.add_argument("results_file", help="Path to benchmark results JSON file")
    parser.add_argument("--html_output", help="Generate HTML output file")
    parser.add_argument("--markdown_output", help="Generate markdown output file")
    parser.add_argument(
        "--question", "-q", type=int, help="Show only specific question number"
    )

    args = parser.parse_args()

    # Validate input file
    results_path = Path(args.results_file)
    if not results_path.exists():
        print(f"Error: File '{args.results_file}' not found", file=sys.stderr)
        sys.exit(1)

    # Markdown is the default report format; HTML is opt-in. With no format
    # flag the report is written as the artifact's `_report.md` sibling, the
    # same shape a run produces, so the backfill path can always find it.
    markdown_path = args.markdown_output
    if not args.html_output and not args.markdown_output:
        markdown_path = results_path.with_name(results_path.stem + "_report.md")

    # Load results
    try:
        results, metadata = load_benchmark_results(args.results_file)
        config_data, config_name, timestamp, questions, total_results, provenance = (
            parse_benchmark_results(results, metadata)
        )
    except Exception as e:
        print(f"Error loading results: {e}", file=sys.stderr)
        sys.exit(1)

    if markdown_path:
        markdown_content = format_markdown_output(
            config_data, config_name, timestamp, questions, total_results, provenance
        )
        with open(markdown_path, "w") as f:
            f.write(markdown_content)
        print(f"✅ Markdown report generated: {markdown_path}")

    if args.html_output:
        html_content = format_html_output(
            config_data, config_name, timestamp, questions, total_results, provenance
        )
        with open(args.html_output, "w") as f:
            f.write(html_content)
        print(f"✅ HTML report generated: {args.html_output}")


if __name__ == "__main__":
    main()
