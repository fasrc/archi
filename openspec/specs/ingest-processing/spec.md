# ingest-processing Specification

## Purpose
TBD - created by archiving change add-html-markdown-categorize-ingest. Update Purpose after archive.
## Requirements
### Requirement: HTML-to-Markdown conversion at persist time
The system SHALL, when `data_manager.processing.html_to_markdown.enabled` is true,
convert a resource's HTML content to Markdown before persistence if the content is a
string and the suffix is `html`/`htm`, preserving headings, lists, links, and tables,
setting the suffix to `md` and recording `metadata["converted_from"]="html"`.

#### Scenario: Scraped HTML resource is converted
- **WHEN** conversion is enabled and a resource with suffix `html` and string content `<h1>Title</h1>` is processed
- **THEN** its content becomes ATX Markdown (`# Title`), its suffix becomes `md`, and `metadata["converted_from"]` is `html`

#### Scenario: Conversion disabled
- **WHEN** `html_to_markdown.enabled` is false
- **THEN** the resource's content, suffix, and metadata are unchanged

### Requirement: Converted resources persist with a Markdown path
The system SHALL update a converted resource's path-bearing fields (`relative_path`
and `file_name` when set), not only `suffix`, so the persisted file has a `.md`
extension and is later loaded via `TextLoader` rather than `BSHTMLLoader`.

#### Scenario: Git-harvested HTML resource keeps a consistent .md path
- **WHEN** a resource whose `file_name`/`relative_path` end in `.html` is converted
- **THEN** those fields are rewritten to `.md` so `get_file_path()`/`get_filename()` and `select_loader()` all agree on Markdown

### Requirement: Conversion or blank-output failure never blocks ingest
The system SHALL return the resource unchanged (original content and suffix), log a
warning, and never raise when HTML-to-Markdown conversion raises OR yields empty or
whitespace-only Markdown, so persistence's empty-content guard cannot block ingest.

#### Scenario: Converter raises
- **WHEN** the converter raises while processing an HTML resource
- **THEN** the original resource is returned, no exception escapes, and persistence proceeds

#### Scenario: Conversion yields blank Markdown
- **WHEN** conversion produces an empty or whitespace-only string (e.g. a script-only page)
- **THEN** the original HTML resource is kept and persisted, avoiding the empty-content `ValueError`

### Requirement: Conversion leaves non-HTML content untouched
The system SHALL leave a resource's content and suffix unchanged in the conversion
step when its content is `bytes` or its suffix is not `html`/`htm` (PDFs, code,
tickets, binary uploads). This requirement governs only the conversion step and makes
no claim about the separate categorization step.

#### Scenario: Bytes or non-HTML resource not converted
- **WHEN** a resource with `bytes` content or a `pdf` suffix passes through the conversion step
- **THEN** its content and suffix are unmodified

### Requirement: LLM-based categorization
The system SHALL, when `data_manager.processing.categorization.enabled` is true,
assign a label from the configured category list using a LangChain chat model
obtained via `get_model(provider, model, provider_config)`, truncating content to the
configured `max_chars` before the call, and SHALL store the result under
`metadata["llm_category"]` (a key distinct from any source-provided `category`).

#### Scenario: Valid category assigned
- **WHEN** categorization is enabled and a mock chat model returns a label present in the configured list
- **THEN** `metadata["llm_category"]` equals that label and any existing `metadata["category"]` is unchanged

#### Scenario: Content truncated before the model call
- **WHEN** content exceeds the configured `max_chars`
- **THEN** the model receives content truncated to `max_chars`

#### Scenario: Categorization disabled
- **WHEN** `categorization.enabled` is false
- **THEN** no chat model is constructed, no model call is made, and no `llm_category` is written

### Requirement: Categorization errors default to uncategorized
The system SHALL, only on the enabled categorization path, set
`metadata["llm_category"]="uncategorized"` and continue when the model raises, returns
an out-of-list label, is not configured, or the category list is empty — never
blocking ingest and never overwriting a source-provided `category`.

#### Scenario: Model raises
- **WHEN** categorization is enabled and the chat model's `invoke` raises
- **THEN** `metadata["llm_category"]` is `uncategorized`, `metadata["category"]` (if any) is untouched, and no exception escapes

#### Scenario: Out-of-list label
- **WHEN** the model returns a label not in the configured categories
- **THEN** `metadata["llm_category"]` is `uncategorized`

### Requirement: Metadata attachment across resource types
The system SHALL attach processor-generated metadata (`converted_from`,
`llm_category`) through an interface that works for every resource type, including
`LocalFileResource`, whose metadata is not a mutable field today; attached values
SHALL reach `resource.get_metadata()` and therefore the catalog.

#### Scenario: Local file resource receives a label
- **WHEN** categorization is enabled and a `LocalFileResource` is processed
- **THEN** its `llm_category` is attached without error and is present in the metadata passed to `catalog.upsert_resource`

### Requirement: Dedup integrity preserved across conversion
The system SHALL NOT change a resource's hash when conversion flips its suffix,
content, or path fields, so re-ingestion does not create duplicate catalog entries.

#### Scenario: Hash unchanged after conversion
- **WHEN** a resource is converted from `html` to `md`
- **THEN** `get_hash()` returns the same value before and after processing

### Requirement: Re-ingest refreshes chunks for changed content
The system SHALL ensure that when a previously-indexed document's persisted content
changes (e.g. HTML→Markdown) under an unchanged hash, its stale chunks are not left in
`document_chunks` — the document's chunks SHALL be refreshed (or removed before
re-embedding) so retrieval never serves the old flattened content alongside the new.

#### Scenario: Converted re-ingest does not leave stale chunks
- **WHEN** a document already indexed as raw HTML is re-ingested with conversion enabled (same hash, new `.md` content)
- **THEN** its old HTML-derived chunks are replaced by Markdown-derived chunks, not duplicated or left stale

### Requirement: Single-seam wrapping across all ingest entry points
The system SHALL apply the processing pipeline through `PersistenceService.persist_resource`
at every construction site that ingests content — both `DataManager` (scheduled/startup)
and the uploader UI — via a shared factory, and the wrapper SHALL delegate all other
persistence methods unchanged. UI uploads SHALL NOT bypass processing.

#### Scenario: Wrapper processes then delegates
- **WHEN** `persist_resource(resource, target_dir, overwrite)` is called on the wrapper
- **THEN** the pipeline transforms the resource, the inner `persist_resource` is called with the transformed resource and all three arguments, and `delete_resource`/`flush_index`/`delete_by_metadata_filter`/`reset_directory`/`catalog` delegate to the inner instance

#### Scenario: Uploader path is wrapped
- **WHEN** a document is ingested through the uploader UI
- **THEN** it is processed by the same pipeline as scheduled ingest (no unwrapped `PersistenceService`)

### Requirement: No-op when disabled
The system SHALL build the processing pipeline from `data_manager.processing` config
such that when all processors are disabled the persistence service behaves identically
to the unwrapped service; a missing `processing` block SHALL mean conversion on and
categorization off (the shipped default), while an explicitly all-disabled block SHALL
yield the bare service.

#### Scenario: Explicitly disabled equals unwrapped
- **WHEN** `html_to_markdown.enabled` and `categorization.enabled` are both false
- **THEN** a resource is persisted byte-for-byte identically to the unwrapped service

#### Scenario: Missing block uses shipped default
- **WHEN** no `processing` block is present in config
- **THEN** HTML is converted and no LLM call is made

### Requirement: Metadata reaches chunks
The system SHALL store `converted_from` (when converted) and `llm_category` (when
categorization is enabled and ran) on the resource metadata such that they propagate to
`documents.extra_json` and onward to `document_chunks.metadata`.

#### Scenario: Metadata persisted to the catalog
- **WHEN** a converted, categorized resource is persisted
- **THEN** `catalog.upsert_resource` receives a metadata dict containing `converted_from` and `llm_category`

