## ADDED Requirements

### Requirement: HTML-to-Markdown conversion at persist time
The system SHALL convert a resource's HTML content to Markdown before persistence
when the resource's content is a string and its suffix is `html` or `htm`,
preserving headings, lists, links, and tables, and SHALL set the suffix to `md` and
record `metadata["converted_from"]="html"`.

#### Scenario: Scraped HTML resource is converted
- **WHEN** a resource with suffix `html` and string content `<h1>Title</h1>` is processed
- **THEN** its content becomes ATX Markdown (`# Title`), its suffix becomes `md`, and `metadata["converted_from"]` is `html`

#### Scenario: Markdown survives into chunk loading
- **WHEN** the converted `.md` file is later selected for loading
- **THEN** it routes through `TextLoader` rather than `BSHTMLLoader`, preserving structure

### Requirement: Conversion failure never blocks ingest
The system SHALL return the resource unchanged and log a warning if HTML-to-Markdown
conversion raises, and SHALL NOT propagate the exception.

#### Scenario: Converter raises
- **WHEN** the converter raises while processing an HTML resource
- **THEN** the resource is returned with original content and suffix, no exception escapes, and persistence proceeds

### Requirement: Non-HTML resources pass through unchanged
The system SHALL leave a resource untouched when its content is `bytes` or its suffix
is not `html`/`htm` (PDFs, code, tickets, binary uploads), making no HTTP or model call.

#### Scenario: Bytes or non-HTML resource skipped
- **WHEN** a resource with `bytes` content or a `pdf` suffix is processed
- **THEN** the same content, suffix, and metadata are returned unmodified

### Requirement: LLM-based categorization
The system SHALL assign a category label from a configured list using a LangChain chat
model obtained via `get_model(provider, model, provider_config)`, truncating content to
the configured `max_chars` budget before the call, and SHALL store the result as
`metadata["category"]`.

#### Scenario: Valid category assigned
- **WHEN** a mock chat model returns a label present in the configured categories
- **THEN** `metadata["category"]` equals that label

#### Scenario: Content truncated before the model call
- **WHEN** the content exceeds the configured `max_chars`
- **THEN** the model receives content truncated to `max_chars`

### Requirement: Categorization errors default to uncategorized
The system SHALL set `metadata["category"]="uncategorized"` and continue when the model
raises, returns an out-of-list label, is not configured, or the category list is empty,
and SHALL NOT block ingest.

#### Scenario: Model raises
- **WHEN** the chat model's `invoke` raises
- **THEN** `metadata["category"]` is `uncategorized` and no exception escapes

#### Scenario: Out-of-list label
- **WHEN** the model returns a label not in the configured categories
- **THEN** `metadata["category"]` is `uncategorized`

### Requirement: Dedup integrity preserved across conversion
The system SHALL NOT change a resource's hash when conversion flips its suffix or
content, so that re-ingestion does not create duplicate catalog entries.

#### Scenario: Hash unchanged after conversion
- **WHEN** a resource is converted from `html` to `md`
- **THEN** `get_hash()` returns the same value before and after processing

### Requirement: Single-seam wrapping across ingest entry points
The system SHALL apply the processing pipeline through `PersistenceService.persist_resource`
so that scheduled/startup ingest and the configured upload path are processed via one
seam, and the wrapper SHALL delegate all other persistence methods unchanged.

#### Scenario: Wrapper processes then delegates
- **WHEN** `persist_resource(resource, target_dir, overwrite)` is called on the wrapper
- **THEN** the pipeline transforms the resource, the inner `persist_resource` is called with the transformed resource and all three arguments, and `delete_resource`/`flush_index`/`delete_by_metadata_filter`/`reset_directory`/`catalog` delegate to the inner instance

### Requirement: Config-driven enablement with no-op default
The system SHALL build the processing pipeline from `data_manager.processing` config, and
when all processors are disabled the persistence service SHALL behave identically to the
unwrapped service.

#### Scenario: Feature disabled
- **WHEN** `html_to_markdown.enabled` and `categorization.enabled` are both false
- **THEN** a resource is persisted byte-for-byte identically to the unwrapped service

#### Scenario: Categorization opt-in
- **WHEN** `categorization.enabled` is false but `html_to_markdown.enabled` is true
- **THEN** HTML is converted and no LLM call is made

### Requirement: Category and conversion metadata reach chunks
The system SHALL store `category` and `converted_from` on the resource metadata such that
they propagate to `documents.extra_json` and onward to `document_chunks.metadata`.

#### Scenario: Metadata persisted to the catalog
- **WHEN** a categorized, converted resource is persisted
- **THEN** `catalog.upsert_resource` receives a metadata dict containing `category` and `converted_from`
