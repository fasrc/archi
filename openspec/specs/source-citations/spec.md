# source-citations Specification

## Purpose
TBD - created by syncing change add-hyperlink-citations. Update Purpose after archive.
## Requirements
### Requirement: Retrieved context exposes a citable title and URL

The retriever's per-document formatting that the model sees SHALL include, for each snippet,
the document's `url` and a human-readable title (`title`, else `display_name`, else filename),
in addition to the existing index and resource hash. The model cannot cite fields that are
not in its context.

#### Scenario: A retrieved snippet carries url and title in the model context

- **WHEN** the vectorstore retriever formats results for the model and a document's metadata
  has `url` and `title`
- **THEN** the formatted snippet header includes both the title and the url (and retains the
  resource hash for the document-fetch tool)

#### Scenario: Title falls back when absent

- **WHEN** a document has a `url` but no non-empty `title`
- **THEN** the snippet uses `display_name` (else the filename) as the title text
- **AND** the resource hash is never used as citation text

### Requirement: The agent cites sources as inline Markdown hyperlinks

When the FASRC Docs agent references a retrieved source in its answer, it SHALL emit an inline
Markdown link `[title](url)` at the citation point, instead of a bare numeric index. It SHALL
NOT fabricate a URL, and when a source has no URL it SHALL name the source in plain text.

#### Scenario: A referenced source renders as a hyperlink

- **WHEN** the agent answers using a retrieved document that has a title and url
- **THEN** the answer cites it inline as `[document title](url)` where a bracketed number
  would otherwise appear
- **AND** the final answer contains no bare `[1]`/`[2]` index tokens

#### Scenario: No fabricated URLs

- **WHEN** a retrieved result has no `url`
- **THEN** the agent names the source in plain text and does NOT invent a URL from the hash,
  filename, or document id

### Requirement: HTML ingestion captures the document title

The HTML ingestion path SHALL extract the page title (`<title>`, falling back to `<h1>`
then `og:title`) into the resource metadata before HTML-to-Markdown conversion, so HTML
documents carry a clean title for citation — matching the title already captured for PDFs
and selenium-rendered pages. It SHALL NOT overwrite a non-empty title already present.

#### Scenario: An ingested HTML page persists a title

- **WHEN** an HTML resource is processed at persist time and has a `<title>` element
- **THEN** the persisted document's metadata `title` is the trimmed title text (not empty)

#### Scenario: An existing title is preserved

- **WHEN** an HTML resource already carries a non-empty `title` (selenium/SSO or PDF path)
- **THEN** title extraction leaves it unchanged (no clobbering)

#### Scenario: Titleless HTML never blocks ingest

- **WHEN** an HTML resource has no `<title>`/`<h1>`/`og:title`
- **THEN** the document is still ingested with an empty title (citation falls back to
  `display_name`), and no error is raised

### Requirement: Citation guidance is committed, not deployment-only

The agent SHALL receive its inline `[title](url)` citation instruction (cite the title+url shown
for each search result; never bare `[n]` indices; never a fabricated URL) from committed code as
a default appended to the system prompt, so the behavior does not depend solely on a
per-deployment (and possibly gitignored) prompt file. The default SHALL be applied to agents
wired with a catalog/vectorstore retrieval tool and SHALL NOT be applied to agents without one.

#### Scenario: A retrieval agent gets the citation default from committed code

- **WHEN** an agent that declares the vectorstore retriever tool (`search_vectorstore_hybrid` or
  `search_knowledge_base`) is resolved — even from a minimal prompt body that says nothing about
  citations
- **THEN** its resolved system prompt includes the committed citation guidance instructing inline
  `[title](url)` citation and forbidding bare `[n]` indices

#### Scenario: A non-retrieval agent does not get citation guidance

- **WHEN** an agent that declares no vectorstore retriever tool is resolved
- **THEN** the committed citation guidance is NOT appended

#### Scenario: Tracked examples model the hyperlink style

- **WHEN** the tracked example agents under `examples/agents/` are read
- **THEN** none instruct the model to cite by bare numeric result indices, and the citation
  style they model is the inline `[title](url)` Markdown link

### Requirement: A /v1 chat completion presents exactly one source list

A `/v1/chat/completions` response SHALL NOT present two source lists. When the `final` stream
event carries the bare answer — the normal path — a response with source documents SHALL
contain exactly one source list in its message content, built by `format_citations`, and the
chat wrapper's own appended source list (`format_links_markdown` output) SHALL NOT appear in
that content. When the bare answer is absent (the defensive arm covered by the last scenario
below), the wrapper's already-appended list is the response's single list and a
`format_citations` list SHALL NOT be added on top of it. The `final` stream event SHALL expose
the bare answer (the pipeline answer without the wrapper's appended source list) alongside the
existing finalized `response` field, which SHALL remain unchanged for existing consumers.

#### Scenario: Non-streaming response with sources has a single source section

- **WHEN** a non-streaming `/v1/chat/completions` request completes with source documents and
  retriever scores, and the `final` event's `response` already ends with the wrapper's
  appended source list
- **THEN** the returned message content contains exactly one `**Sources:**` block (the
  `format_citations` output)
- **AND** the wrapper's `Show all sources` block does not appear in the content

#### Scenario: Non-streaming response without sources has no source section

- **WHEN** a non-streaming `/v1/chat/completions` request completes with no source documents
- **THEN** the returned message content is the bare answer with no source list appended

#### Scenario: Empty answer with sources yields citations only

- **WHEN** a non-streaming `/v1/chat/completions` request completes with an empty bare answer
  but non-empty source documents
- **THEN** the returned message content is exactly the `format_citations` output, and the
  wrapper's appended source list does not reappear via fallback

#### Scenario: A missing pipeline answer is omitted from the final event, never defaulted

- **WHEN** the final stream event is assembled and the pipeline output has no answer value
- **THEN** the bare-answer field is left out of the event entirely rather than emitted as an
  empty string, so a producer-side extraction bug cannot masquerade as a legitimately empty
  answer and silently drop the model's answer from `/v1` content

#### Scenario: Final event without a bare answer never produces two lists

- **WHEN** the `final` event does not carry the bare-answer field (a defensive arm — the
  wrapper and endpoint ship in the same image, so this state indicates a producer regression)
- **THEN** the endpoint uses the event's `response` verbatim and does NOT append
  `format_citations`, so the content carries at most the wrapper's single appended source
  list and the duplicate-list defect cannot be reintroduced
