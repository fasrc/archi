## Context

archi's ingest stores scraped HTML raw; conversion to text happens at embed time via
`BSHTMLLoader`, which flattens markup before chunking (`loader_utils.py:31`). There is
no document categorization. This change inserts one configurable processing stage
between collection and persistence.

Every collector funnels resources through `PersistenceService.persist_resource`
(`persistence.py:24`), constructed in `DataManager.__init__` (`data_manager.py:31`) —
the natural seam to wrap. This design promotes an earlier planning doc
(`plans/html-markdown-categorize-ingest.md`); its references were re-verified against
current `dev`, which corrected several stale assumptions (below).

Re-grounded facts (current `dev`):
- Seam signature: `persist_resource(self, resource, target_dir, overwrite=False) -> Path`
  (`persistence.py:24`); the wrapper must forward all three args. Public surface to
  delegate: `delete_resource` (69), `delete_by_metadata_filter` (92),
  `reset_directory` (105), `flush_index` (138), and attrs `catalog` (22), `data_path`,
  `pg_config`.
- Resource metadata flows to `documents.extra_json` via `catalog.upsert_resource`
  (`persistence.py:65`) and reaches `document_chunks.metadata` via the
  `file_level_metadata` merge (`manager.py:431`/`:700`). Retrievers do not filter on
  metadata today — category is stored, not yet queried.
- Hashes are identity-based — `ScrapedResource.get_hash()=md5(url)`
  (`scraped_resource.py:29`), `LocalFileResource`=md5(path) — so flipping
  suffix/content does not duplicate catalog rows.

## Goals / Non-Goals

**Goals:**
- One stage that converts HTML→Markdown and optionally categorizes every document,
  built once and applied across ingest entry points.
- Never block ingest on a conversion or model failure.
- No-op when disabled; opt-in cost for categorization.
- No connector rewrites; no new behavior for existing capabilities when disabled.
- No new third-party dependency beyond `markdownify` (built on already-present `bs4`).

**Non-Goals:**
- Retrieval-time filtering by category (retrievers don't read metadata filters).
- Re-converting already-ingested documents (applies going forward only).
- Converting local `.html` **uploads** (bytes content — see Risks).
- Fixing the pre-existing 2-arg `get_model()` calls in `classic_pipelines/base.py`.

## Decisions

**D1 — Wrap the persist seam (vs. alternatives).** Wrap `PersistenceService` with a
`ProcessingPersistenceService` that runs the pipeline then delegates. Rejected: (a)
editing `persist_resource`'s body — persistence shouldn't classify; (b) per-connector
hooks — duplicated across 3+ collectors, easy to miss one; (c) convert at embed time —
loses the clean `.md`-on-disk artifact and re-does work every embed. Implement
delegation as an explicit `persist_resource` override plus `__getattr__` fallthrough to
the inner instance.

**D2 — Shared persistence factory (corrects the plan's "wrap once covers all").**
Research **refuted** that wrapping `DataManager.__init__` covers everything:
`uploader_app/app.py:50` constructs its own `PersistenceService`, so UI uploads would
bypass processing and produce an inconsistent corpus. Introduce
`build_persistence(config, data_path, pg_config) -> PersistenceService` that applies the
wrap from config, and call it from **both** `data_manager.py:31` and
`uploader_app/app.py:50`. `chat_app/document_utils.py:74` builds its own instance too
but is a delete-only path — left unwrapped.

**D3 — Type-guarded conversion (corrects the plan's uniform-`suffix` assumption).** Only
`ScrapedResource` has a mutable `suffix` field and string content. `LocalFileResource`
is always `bytes` with no `suffix` field; `TicketResource` hardcodes `.txt`.
`HtmlToMarkdownProcessor` guards on `isinstance(content, str)` **and**
`getattr(resource, "suffix", "").lstrip(".").lower() in {"html","htm"}` — converting
scraped/web HTML and skipping everything else safely.

**D4 — Provider access via `get_model(provider, model, provider_config)` (corrects the
plan's wrong API).** The plan named `get_provider_by_name(...).get_chat_model(...)`,
which bypasses `provider_config` (base_url/extra_kwargs/models). The correct current API
is `get_model(provider_type, model_name, provider_config, **kwargs) -> BaseChatModel`
(`providers/__init__.py:239`); invoke with a message list. The model is built **lazily**
so disabled categorization costs nothing and needs no provider configured. **`provider_config`
is sourced from `services.chat_app.providers.<provider>`** (base_url/mode/models/extra_kwargs),
mirroring `base_react.py`'s `_build_provider_config` (`base_react.py:839-843,877-897`) —
NOT from `data_manager.processing`. Without this, a custom local/vLLM endpoint would be
ignored and categorization would hit the wrong default server and mark every document
`uncategorized`.

**D5 — html_to_markdown ON by default; categorization OPT-IN.** Conversion is
cheap/local and strictly improves chunk quality. Categorization is one LLM call per
document — expensive on large crawls — so it defaults off. A **missing** `processing`
block means conversion on / categorization off (the shipped default); an explicitly
all-disabled block yields the bare service ("no-op when disabled").

**D6 — Dependency in both files.** `markdownify` goes in `pyproject.toml`
(`dependencies`) **and** `requirements/requirements-base.txt` with a matching pin —
deployment images `pip install .` and read only `pyproject.toml`; a requirements-only
dep crash-loops the container.

**D7 — Rewrite path fields, not just suffix (review finding).** `ScrapedResource.get_file_path()`
prefers `relative_path` and `get_filename()` prefers `file_name` over `suffix`
(`scraped_resource.py:34-44`); `GitScraper._harvest_code` sets both with the original
extension (`git_scraper.py:226-239`), and `select_loader` keys off the actual on-disk
path's suffix (`loader_utils.py:18-32`). So conversion MUST rewrite `relative_path`/`file_name`
to `.md` too, or a git-harvested `.html` resource would be written to a `.html` path and
still route through `BSHTMLLoader`.

**D8 — Distinct `llm_category` key (review finding).** The Indico scraper already writes
`metadata["category"]` from the event's source category (`indico_scraper.py:987,1056`).
Writing the LLM label to the same key would overwrite source truth (and a failure →
`uncategorized` would clobber it). Categorization writes **`metadata["llm_category"]`**
and never touches a source-provided `category`.

**D9 — Metadata attachment across resource types (review finding).** `BaseResource`
exposes only `get_metadata()` with no setter, and `LocalFileResource` has no mutable
`metadata` dict (`resource_base.py:38-40`, `localfile_resource.py:13-21`) — so
`resource.metadata["llm_category"]=...` would `AttributeError` for local files. Add a
mutable `metadata` dict to `LocalFileResource` and a `set_metadata_field()` helper on
`BaseResource`, and have processors attach labels through it uniformly.

**D10 — Blank-output guard (review finding).** `markdownify` of script-only / empty HTML
can yield an empty string; `PersistenceService._write_content` raises
`ValueError("Refusing to persist empty textual content")` (`persistence.py:164-166`),
which would block ingest. `HtmlToMarkdownProcessor` treats blank/whitespace output the
same as a conversion failure and keeps the original resource.

**D11 — Refresh chunks on content change under unchanged hash (review finding).** Hashes
are identity-based (URL/path), so an HTML→Markdown rewrite keeps the same hash. But
`VectorStoreManager.update_vectorstore()` compares only hash *sets* (`manager.py:240-257`)
and re-embeds only new hashes, so a re-ingested-then-converted doc would keep its old
HTML-flattened chunks in `document_chunks`. The implementation must detect changed
content under an unchanged hash (e.g. `documents.size_bytes`/`file_modified_at`, or a
content hash) and refresh/delete that doc's chunks before re-embedding. (Retroactive
conversion of never-re-ingested docs remains out of scope.)

## Risks / Trade-offs

- **Local `.html` uploads not converted** (bytes content) → document the limitation;
  scraped/web HTML is the target. Mitigation/future: a decode-on-`html`-suffix path.
- **UI uploads bypass processing if the factory is skipped** → D2's shared factory; a
  delegation test enumerates every attr callers read off `.persistence`.
- **Categorization cost on large crawls** (1 LLM call/doc) → off by default; enforce a
  `max_chars` truncation budget; document the cost.
- **Flaky/invalid model output** → default `"uncategorized"`, never raise.
- **Coverage gate (diff-cover ≥ 80% on changed lines)** → `processing.py` is pure and
  fully unit-testable with a mock chat model and fake resources; hit both success and
  error branches.
- **`markdownify` of tables/code** → assert structure survival in a conversion test.

## Migration Plan

Additive and opt-in; no data migration. Disabled config = byte-for-byte current
behavior. Rollback = disable the `processing` block (or revert). Applies to new /
re-ingested documents only; already-ingested HTML is unchanged until re-ingested.

## Open Questions

Resolved during PR review (now decisions, not open):
- ~~Uploader scope~~ → **mandatory** (D2): the uploader is wired through the shared
  factory; descoping is not permitted (spec requires UI uploads not bypass processing).
- ~~`provider_config` source~~ → **decided** (D4): sourced from
  `services.chat_app.providers.<provider>`, mirroring `base_react.py`.

Still open:
- **Idempotency:** processing an already-`md` resource must be a no-op (don't
  re-tag `converted_from`) — confirm + test during apply.
- **Chunk-refresh mechanism (D11):** detect changed content via `size_bytes`/`mtime`
  vs. a stored content hash — pick the cheaper reliable signal at apply time.
