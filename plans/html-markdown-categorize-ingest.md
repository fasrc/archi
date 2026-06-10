# Plan: HTML → Markdown → Categorize → Ingest

**Branch:** `feature/html-markdown-categorize-ingest`

## Context

Today, archi's ingest pipeline stores scraped HTML **raw** on disk. Conversion to
text is deferred to embed time, where `BSHTMLLoader` flattens the markup to
plaintext — headings, lists, tables, and links are lost before chunking
(`src/data_manager/vectorstore/loader_utils.py:31`). There is also **no
categorization**: the only classification axis is `source_type`.

The goal is a single, elegant stage that, for every incoming document, (1) converts
HTML to Markdown, (2) categorizes it with an LLM, and (3) hands it to the existing
ingest step — without rewriting the connectors.

### Why it fits cleanly

Every connector (web, sso, git, local files, tickets) funnels through **one**
function: `PersistenceService.persist_resource(resource, target_dir)`
(`src/data_manager/collectors/persistence.py:24`). That is the natural seam. We wrap
it (do **not** edit its body — persistence should not classify) with a small
`convert → categorize → delegate` pipeline, constructed once in
`DataManager.__init__` where `self.persistence` is built
(`src/data_manager/data_manager.py:31`). Because the uploader UI, scheduler, and
startup ingestion all reuse `data_manager.persistence`, wrapping it once covers all
entry points.

### Correctness facts (verified during exploration)

- Converting content before persist does **not** break dedup: hashes are
  identity-based, not content-based —
  `ScrapedResource.get_hash() = md5(url)`
  (`src/data_manager/collectors/scrapers/scraped_resource.py:29`),
  `LocalFileResource.get_hash() = md5(path)`
  (`src/data_manager/collectors/localfile_resource.py:23`). Only `suffix`/filename
  changes (`abc.html` → `abc.md`).
- Once content is `.md`, `select_loader` routes it through `TextLoader` instead of
  `BSHTMLLoader`, so markdown structure survives into chunks
  (`src/data_manager/vectorstore/loader_utils.py:27`).
- Category lands in `metadata` → `documents.extra_json`, and auto-propagates to
  `document_chunks.metadata` via the `file_level_metadata` merge in
  `VectorStoreManager._add_to_postgres`. **Retrievers do not filter on metadata
  today** — categories are stored and future-queryable; retrieval-time filtering is
  out of scope for this change.

### Decisions confirmed with the user

- **Categorization: LLM-based**, reusing the existing provider layer
  (`get_provider_by_name(name).get_chat_model(model)` →
  langchain `BaseChatModel.invoke()`, `src/archi/providers/__init__.py:140`,
  `src/archi/providers/base.py:131`).
- **Scope: all HTML** — web, sso, and local `.html` uploads.

## Implementation

### 1. New module: `src/data_manager/collectors/processing.py`

Mirrors the existing `Collector` Protocol style (`src/data_manager/collectors/base.py`).

- `ResourceProcessor` (Protocol): `process(resource: BaseResource) -> BaseResource`
- `ResourcePipeline`: holds an ordered `list[ResourceProcessor]`; `process()` folds
  the resource through each. Empty list = no-op (feature disabled).
- `HtmlToMarkdownProcessor`:
  - Acts only when content is HTML: `resource.suffix.lstrip(".").lower() == "html"`
    and content is `str` (skips PDFs/bytes, git code, tickets). Keys off
    content/suffix, **not** source type — so web, sso, and local `.html` are covered
    uniformly.
  - Converts via `markdownify(content, heading_style="ATX")`.
  - Sets `resource.content = md`, `resource.suffix = "md"`,
    `resource.metadata["converted_from"] = "html"`.
  - On exception: log a warning and return the resource **unchanged** (never block
    ingest).
- `CategorizationProcessor`:
  - Takes a lazily-built `BaseChatModel` (or a callable returning one) + a category
    list + a `max_chars` truncation budget.
  - Prompts the model with the (truncated) markdown and the allowed categories;
    parses a single label; validates it is in the configured list.
  - Sets `resource.metadata["category"] = label`.
  - On any error / invalid label / disabled model: set `"uncategorized"` and continue
    — a flaky model call must never break ingest.
- `ProcessingPersistenceService`:
  - Wraps a `PersistenceService` + a `ResourcePipeline`.
  - `persist_resource(resource, target_dir, overwrite=False)`:
    `resource = self.pipeline.process(resource)` then
    `return self._inner.persist_resource(resource, target_dir, overwrite)`.
  - Delegates `delete_resource`, `flush_index`, `delete_by_metadata_filter`,
    `reset_directory`, `catalog`, etc. (the methods/attrs collectors and the uploader
    use) straight through to the wrapped instance.

### 2. Wire into `DataManager.__init__` (`src/data_manager/data_manager.py:31`)

- Build `base = PersistenceService(...)` as today.
- Read `proc_cfg = self.config["data_manager"].get("processing", {})`.
- Assemble processors per config flags; build the chat model lazily so categorization
  being disabled (or no provider configured) costs nothing.
- `self.persistence = ProcessingPersistenceService(base, ResourcePipeline(processors))`
  when any processor is enabled, else keep `base` unchanged.

### 3. Dependency

Add `markdownify` to `requirements/requirements-base.txt` (lightweight; built on the
already-present `beautifulsoup4==4.12.3`). Pin a version.

### 4. Config + docs

- Add a `processing` block under `data_manager` in
  `src/cli/templates/base-config.yaml` (sibling of `sources`, inserted ~line 254),
  Jinja-templated like its neighbors:
  ```yaml
  processing:
    html_to_markdown:
      enabled: {{ ... | default(true) }}
    categorization:
      enabled: {{ ... | default(false) }}   # opt-in: per-doc LLM call
      provider: {{ ... | default('openai') }}
      model: {{ ... | default('gpt-4o-mini') }}
      max_chars: {{ ... | default(6000) }}
      categories: [ ... ]                    # user-defined label set
  ```
- Document the block in `docs/docs/configuration.md` (data_manager section) and note
  the data-flow + cost caveat (one LLM call per document on large crawls) per the
  AGENTS.md docs policy.

### 5. Tests — `tests/unit/test_resource_processing.py`

Follow existing unit-test style (`unittest.mock`, no live services — see
`tests/unit/test_persistence_service_size_bytes.py`):

- `HtmlToMarkdownProcessor` converts a `ScrapedResource(suffix="html")` → markdown,
  flips suffix to `md`, sets `converted_from`; **hash is unchanged** (dedup intact).
- Non-HTML resources (pdf bytes, ticket) pass through untouched.
- Conversion failure returns the resource unchanged.
- `CategorizationProcessor` with a **mock** chat model sets `category`; invalid /
  raising model → `uncategorized`.
- `ProcessingPersistenceService` runs the pipeline then delegates to a mock inner
  `persist_resource` with the transformed resource; pass-through methods delegate.

## Verification

1. `pip install markdownify` (or `pip install -e ".[all]"` after the requirement
   lands), then `pip install -e .`.
2. Unit tests: `pytest tests/unit/test_resource_processing.py -v --tb=short`.
3. Lint: `black --check .` and `isort --check .` on changed files.
4. End-to-end (per AGENTS.md deployment policy): in a running data-manager
   deployment, scrape one HTML URL via the uploader; confirm the persisted file has a
   `.md` extension, the markdown preserves headings/links, and the `documents` row's
   `extra_json` carries `category` + `converted_from`. Then confirm the doc reaches
   `document_chunks` with that metadata after vectorstore sync.

## Out of scope (call out in PR)

- Retrieval-time filtering by category (retrievers don't read metadata filters yet).
- Re-converting already-ingested HTML (applies to new/re-ingested docs going forward).
