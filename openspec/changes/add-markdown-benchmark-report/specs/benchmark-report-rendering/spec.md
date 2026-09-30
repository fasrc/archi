# benchmark-report-rendering — delta

## ADDED Requirements

### Requirement: Markdown report renders the full benchmark result
The system SHALL render a benchmark result as a GitHub-flavored markdown report that contains the same content as the HTML report: a header (configuration name, timestamp, question count), the run-provenance section (configuration divergence, corpus stability, code and config versions), the retrieval-accuracy section when SOURCES mode is enabled, the aggregate RAGAS metrics when RAGAS mode is enabled, and one section per question (question text, retrieval check, actual answer, expected answer, expected sources, retrieved documents, agent messages, per-question RAGAS scores).

#### Scenario: A RAGAS-mode result renders to markdown
- **WHEN** `format_markdown_output` is called with a parsed result whose modes include RAGAS
- **THEN** the output contains the header fields, the aggregate metric values, and each question's answer, expected answer, and per-question RAGAS scores

#### Scenario: Literal placeholders survive rendering
- **WHEN** an answer or a retrieved context contains text such as `<jobid>` or backticks
- **THEN** the markdown places that text inside a code fence long enough that the content renders literally and cannot terminate the fence

#### Scenario: Non-fenced data fields cannot restructure the report
- **WHEN** a configuration name, question text, or source name contains markdown syntax or raw HTML
- **THEN** the rendered report escapes it so the payload displays as text and cannot change the report's structure, inject links or images, or hide content

#### Scenario: Scores carry the threshold badge
- **WHEN** a metric value is rendered
- **THEN** it carries 🔴 below 0.5, 🟡 from 0.5 up to but not including 0.7, and 🟢 at 0.7 and above — the same thresholds the HTML report encodes as colors

#### Scenario: A pre-provenance artifact still renders
- **WHEN** `format_markdown_output` is called with `provenance=None` or with provenance fields missing
- **THEN** it returns a report without a crash, and absent provenance is reported as not recorded rather than as agreement

### Requirement: Markdown is the default report format
The system SHALL write the human-readable report as markdown by default: a benchmark run writes `<name>-<timestamp>_report.md` at the end of the run, and the report CLI with no format flag writes `<stem>_report.md` next to the input JSON.

#### Scenario: End of run writes markdown
- **WHEN** a benchmark run completes and the result handler dumps the report
- **THEN** the file written is `_report.md` and its content comes from `format_markdown_output`

#### Scenario: The report is the JSON's sibling
- **WHEN** a run writes its JSON artifact and its markdown report, even across a second-boundary clock rollover between the two writes
- **THEN** both filenames share one captured timestamp, so the report is `<json-stem>_report.md` and the bulk re-render path can find it

#### Scenario: CLI default is markdown
- **WHEN** the report CLI runs with only a results JSON path
- **THEN** it writes `<stem>_report.md` and no HTML file

### Requirement: HTML rendering stays available as an opt-in
The system SHALL keep the HTML report available: the CLI `--html_output <path>` flag writes the HTML report to that path, and `--markdown_output <path>` names the markdown path; when both flags are given, both files are written.

#### Scenario: HTML on request
- **WHEN** the CLI runs with `--html_output report.html` and no markdown flag
- **THEN** it writes only `report.html`, rendered by `format_html_output`

#### Scenario: Both formats on request
- **WHEN** the CLI runs with both `--html_output` and `--markdown_output`
- **THEN** it writes both files

### Requirement: Markdown reports have a bulk render-and-recover path
The system SHALL provide a bulk maintenance path for markdown reports: the backfill script's `--regenerate-md` flag renders each valid benchmark artifact's `_report.md` sibling from its JSON — re-rendering an existing sibling and creating a missing one — while files the script does not recognize as benchmark artifacts are skipped.

#### Scenario: Existing markdown reports are re-rendered
- **WHEN** the backfill script runs with `--regenerate-md` over a directory where an artifact has a `_report.md` sibling
- **THEN** that sibling is re-rendered from the artifact's JSON via the markdown formatter

#### Scenario: A missing report is recreated from the JSON
- **WHEN** the backfill script runs with `--regenerate-md` and a valid artifact has no `_report.md` sibling
- **THEN** the sibling is created from the artifact's JSON, so a failed or interrupted report write is recoverable

#### Scenario: Non-artifacts are skipped
- **WHEN** the backfill script runs with `--regenerate-md` and a JSON file is not a valid benchmark artifact — including a foreign JSON that carries a `metadata` key but no parseable `benchmarking_results`
- **THEN** the file is skipped cleanly: no markdown file is created and no error is raised

### Requirement: A report-write failure never loses the run's JSON
The system SHALL write the run's JSON artifact before the markdown report, and a failure while rendering or writing the report SHALL be caught and logged without destroying or blocking the JSON write.

#### Scenario: The report write fails after the JSON write
- **WHEN** `dump_artifacts` has written the JSON and the markdown render or write raises
- **THEN** the JSON remains on disk, the failure is logged, and the run does not crash on the report step

### Requirement: Both formatters share one context-text extraction
The system SHALL extract the retrieved-context text (the `page_content=` string parsing) in one shared helper used by both the HTML and the markdown formatter, so a parsing fix lands in both report formats at once.

#### Scenario: Quoted content does not degrade silently
- **WHEN** a context string contains embedded quotes or does not match the `page_content=` shape
- **THEN** the helper returns usable text (falling back to the raw string) and both formatters render that same text
