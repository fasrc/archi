## Why

archi stores scraped HTML **raw** on disk and defers conversion to embed time,
where `BSHTMLLoader` flattens the markup to plaintext — headings, lists, tables,
and links are lost before chunking (`loader_utils.py:31`). There is also **no
categorization**: the only classification axis is `source_type`. This change adds a
single configurable stage that, at the persistence seam, converts HTML→Markdown
(so structure survives into chunks) and optionally tags each document with an
LLM-assigned category — improving retrieval quality and making `category` a stored,
future-queryable axis, without rewriting any connector.

## What Changes

- Add `src/data_manager/collectors/processing.py`: a `ResourceProcessor` protocol,
  a `ResourcePipeline`, an `HtmlToMarkdownProcessor`, a `CategorizationProcessor`,
  and a `ProcessingPersistenceService` that wraps `PersistenceService` with a
  `convert → categorize → delegate` pipeline.
- **HTML→Markdown at persist time**: when a resource's content is a string with an
  `html`/`htm` suffix, convert it (`markdownify`, ATX headings), flip the suffix to
  `md`, and record `metadata["converted_from"]="html"`. The `.md` file then loads
  via `TextLoader` instead of `BSHTMLLoader`, preserving structure. Conversion
  failures return the resource unchanged — ingest is never blocked.
- **Optional LLM categorization**: assign a label from a configured list using the
  provider layer (`get_model(provider, model, provider_config)` →
  `BaseChatModel.invoke([...])`), stored as `metadata["category"]`. Any error,
  out-of-list label, missing model, or empty category list yields
  `"uncategorized"` — never blocking ingest.
- **Shared persistence factory** so the pipeline is applied at **both** ingest
  construction sites: `DataManager.__init__` (scheduled/startup ingest) **and** the
  uploader UI, which builds its own `PersistenceService`. Wrapping only
  `DataManager` would silently skip UI uploads.
- Add a `data_manager.processing` config block — `html_to_markdown` on by default
  (cheap, local); `categorization` opt-in (one LLM call per document).
- Add the `markdownify` dependency to **both** `pyproject.toml` and
  `requirements/requirements-base.txt`.
- Document the block and data-flow in `docs/docs/configuration.md`.

When the feature is disabled, the persistence service behaves byte-for-byte
identically to today.

## Capabilities

### New Capabilities
- `ingest-processing`: a configurable per-document processing stage at the
  persistence seam — HTML→Markdown conversion plus optional LLM categorization —
  applied uniformly across ingest entry points, that defaults to a no-op and never
  blocks ingest on failure.

### Modified Capabilities
<!-- None. The stage wraps the existing persist seam and does not change persistence,
     scraper, loader, or retrieval behavior when disabled. -->

## Impact

- **New code:** `processing.py`; a shared `build_persistence(...)` factory; wiring at
  `data_manager.py:31` and `uploader_app/app.py:50`.
- **Reads/uses (unchanged):** the provider layer `get_model(provider, model,
  provider_config)` (`providers/__init__.py:239`); loader routing
  (`loader_utils.py:27/31`); identity-based hashing (URL/path — dedup survives the
  suffix flip); metadata → `documents.extra_json` → `document_chunks.metadata`.
- **Dependency:** `markdownify` in **both** `pyproject.toml` and
  `requirements/requirements-base.txt` (deployment images `pip install .` — a
  requirements-only dep crash-loops the container).
- **Docs:** `docs/docs/configuration.md`.
- **Known limitation:** local `.html` **uploads** arrive as `bytes`
  (`LocalFileResource` has no `suffix` field and never string content), so they are
  not converted under the `str`-content guard; scraped/web HTML is the primary
  target. Documented; a decode-on-html path is future work.
- **Cost:** categorization issues one LLM call per document — opt-in, off by default.
- **Out of scope:** retrieval-time filtering by category (retrievers don't read
  metadata filters yet); re-converting already-ingested documents.
