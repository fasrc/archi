## ADDED Requirements

### Requirement: Deeply-nested HTML must convert rather than silently fall back

The HTML→Markdown conversion SHALL convert deeply-nested HTML whose nesting exceeds
Python's default recursion limit, instead of raising `RecursionError` and keeping the
original raw HTML. The conversion MUST NOT crash the process (no C-stack overflow /
segfault from an over-raised recursion limit), and any process-global recursion limit
changed for the conversion MUST be restored afterward.

#### Scenario: Deeply-nested HTML converts to Markdown

- **WHEN** a scraped HTML resource whose markup nests beyond Python's default recursion
  limit (e.g. ~2000 nested elements) is processed by `HtmlToMarkdownProcessor`
- **THEN** it is converted to Markdown (suffix `md`, `metadata["converted_from"] == "html"`,
  non-empty content)
- **AND** the process does not raise `RecursionError` and does not crash

#### Scenario: Genuine conversion failure still falls back gracefully

- **WHEN** conversion still raises an exception, or produces blank/whitespace-only output
- **THEN** the original resource is returned unchanged
- **AND** ingest is never blocked
