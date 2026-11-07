# retriever-tool-rendering Specification

## Purpose
What the `search_vectorstore_hybrid` tool shows the model for each retrieved document: the passage the document was ranked for, every result the retriever returns, and a per-document size that keeps the whole result within its 8000-character ceiling.
## Requirements
### Requirement: The tool shows the passage that matched
The retriever tool SHALL render, for each document, the text inside a valid matched span
(`matched_start`/`matched_end` metadata) when present, and the start of `page_content` otherwise. A
span is valid only when both values are integers (not booleans) with
`0 <= matched_start < matched_end <= len(page_content)`. The rendered text MUST be truncated to the
tool's per-document character limit with a trailing `...` marker, as today.

#### Scenario: Match beyond the old window is shown
- **WHEN** a document's matched span starts after character 800 of its `page_content`
- **THEN** the rendered snippet contains the span's text and not the text before the span

#### Scenario: No span renders from the start
- **WHEN** a document carries no matched span
- **THEN** the rendered snippet is the start of `page_content`, truncated to the per-document limit

#### Scenario: Invalid span is ignored
- **WHEN** a document's span is out of range, empty, reversed, or not made of integers
- **THEN** the rendered snippet is the start of `page_content`, as if no span were present

### Requirement: The tool shows every result the retriever returns by default
`create_retriever_tool` SHALL default to rendering up to 8 documents. It SHALL limit each body to
`min(max_chars, share)`, where `max_chars` defaults to 1200 and `share` is the 8000-character
result ceiling (`DEFAULT_RETRIEVER_RESULT_CHARS`, unchanged) minus the rendered headers,
separators and per-document `...` markers, divided by the number of documents rendered and floored
at 200 characters. It SHALL
keep the 8000-character ceiling on the complete serialized result.

#### Scenario: Five results, five full-length snippets
- **WHEN** a retriever wired with the default tool settings returns 5 documents with typical
  metadata and bodies longer than 1200 characters
- **THEN** the tool output contains 5 numbered snippet headers `[1]` … `[5]`, each body is
  1200 characters plus the `...` marker, and the output is at most 8000 characters without the
  truncation marker

#### Scenario: Eight results all fit
- **WHEN** a retriever wired with the default tool settings returns 8 documents with typical
  metadata and long bodies
- **THEN** the tool output contains 8 numbered snippet headers `[1]` … `[8]` and is at most 8000
  characters without the truncation marker

#### Scenario: The result ceiling still holds
- **WHEN** the retrieved documents' metadata and text would serialize to more than 8000 characters
- **THEN** the tool output is clamped to 8000 characters with the truncation marker

