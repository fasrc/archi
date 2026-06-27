## ADDED Requirements

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

The plain-HTTP scraping path SHALL extract the page title (`<title>`, falling back to `<h1>`
then `og:title`) into the resource metadata, so HTML documents carry a clean title for
citation — matching the title already captured for PDFs and selenium-rendered pages.

#### Scenario: A scraped HTML page persists a title

- **WHEN** an HTML page is collected via the plain-HTTP scraper and has a `<title>` element
- **THEN** the persisted document's metadata `title` is the trimmed title text (not empty)

#### Scenario: Titleless HTML never blocks ingest

- **WHEN** a scraped page has no `<title>`/`<h1>`/`og:title`
- **THEN** the document is still ingested with an empty title (citation falls back to
  `display_name`), and no error is raised
