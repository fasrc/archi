## MODIFIED Requirements

### Requirement: HTML-to-Markdown conversion at persist time
The system SHALL, when `data_manager.processing.html_to_markdown.enabled` is true and a
resource's content is a string with an `html`/`htm` suffix, convert it to Markdown before
persistence, preserving headings, lists, links, and tables, setting the suffix to `md` and
recording `metadata["converted_from"]="html"`. When the converted Markdown contains a
recognized article **start landmark** (`Table of Contents`) **and** an **end landmark**
(`Bookmarkable Links`, or else `Last Updated`), the system SHALL keep only the article body
between them — dropping everything up to and including the start landmark and everything
from the end landmark onward. When both landmarks are not present, or the slice would be
blank, the system SHALL keep the full converted Markdown, so no article is ever dropped.

#### Scenario: Scraped HTML resource is converted
- **WHEN** conversion is enabled and a resource with suffix `html` and string content `<h1>Title</h1>` is processed
- **THEN** its content becomes ATX Markdown (`# Title`), its suffix becomes `md`, and `metadata["converted_from"]` is `html`

#### Scenario: KB article body is sliced between landmarks
- **WHEN** a KB page converts to Markdown containing `Table of Contents`, an article body, then `Bookmarkable Links` and `Last Updated`
- **THEN** the persisted Markdown contains the article body and omits the pre-`Table of Contents` navigation and the `Bookmarkable Links`/footer content

#### Scenario: End landmark falls back to Last Updated
- **WHEN** a converted KB page contains `Table of Contents` and `Last Updated` but no `Bookmarkable Links`
- **THEN** the body is sliced from after `Table of Contents` up to `Last Updated`

#### Scenario: No landmarks keeps full-page conversion
- **WHEN** a converted page contains neither landmark pair (a non-KB source)
- **THEN** the full converted Markdown is kept unchanged, the suffix is `md`, and `metadata["converted_from"]` is `html`

#### Scenario: Conversion disabled
- **WHEN** `html_to_markdown.enabled` is false
- **THEN** the resource's content, suffix, and metadata are unchanged

### Requirement: Metadata reaches chunks
The system SHALL store `converted_from` (when converted), `llm_category` (when
categorization is enabled and ran), and `category` (when a source category was captured)
on the resource metadata such that they propagate to `documents.extra_json` and onward to
`document_chunks.metadata`.

#### Scenario: Metadata persisted to the catalog
- **WHEN** a converted, category-captured resource is persisted
- **THEN** `catalog.upsert_resource` receives a metadata dict containing `converted_from` and `category`

## ADDED Requirements

### Requirement: Source category capture from HTML before conversion
The system SHALL, when `data_manager.processing.html_to_markdown.enabled` is true, capture
a source-provided category from an HTML resource **before** Markdown conversion (while the
content is still raw HTML) and store it under `metadata["category"]`. The captured value
SHALL be read from the page's breadcrumb category. The step SHALL never raise, SHALL leave
content and suffix unchanged, SHALL NOT overwrite a non-empty `category` already present
(e.g. one set by a source scraper), and SHALL write nothing when no category is found. This
`category` key is distinct from the `llm_category` written by LLM categorization.

#### Scenario: Breadcrumb category captured
- **WHEN** an HTML resource whose page renders a `Home › <Category> › <Article>` breadcrumb is processed and no `category` is already set
- **THEN** `metadata["category"]` equals `<Category>` and the content/suffix are unchanged

#### Scenario: Captured before conversion strips the breadcrumb
- **WHEN** the capture step and `HtmlToMarkdownProcessor` both run in the pipeline
- **THEN** the category is captured from the raw HTML before conversion, so `metadata["category"]` is populated even though the converted Markdown body no longer contains the breadcrumb

#### Scenario: No breadcrumb present
- **WHEN** an HTML resource has no recognizable breadcrumb (a non-KB page)
- **THEN** no `category` is written and the resource passes through unchanged

#### Scenario: Existing source category is not overwritten
- **WHEN** a resource already carries a non-empty `metadata["category"]` set by a source scraper
- **THEN** the capture step leaves that value unchanged

#### Scenario: Non-HTML content is skipped
- **WHEN** a resource with `bytes` content or a non-`html`/`htm` suffix passes through the capture step
- **THEN** no `category` is written and content/suffix are unchanged
