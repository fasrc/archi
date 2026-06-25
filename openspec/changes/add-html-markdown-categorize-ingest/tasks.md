## 1. Dependency (both files)

- [ ] 1.1 Add `markdownify==<pin>` to `pyproject.toml` `dependencies` AND `requirements/requirements-base.txt` (next to `beautifulsoup4`), matching pins; resolve latest stable at apply time.
- [ ] 1.2 `pip install -e .`; confirm `import markdownify` works.

## 2. Resource metadata attachment (test-first)

- [ ] 2.1 Failing tests: attaching a metadata field works for `ScrapedResource`, `TicketResource`, AND `LocalFileResource`; the attached value appears in `get_metadata()` / the dict `persist_resource` reads.
- [ ] 2.2 Add a mutable `metadata` dict to `LocalFileResource` and thread it through its `get_metadata()`; add a `set_metadata_field(key, value)` helper on `BaseResource` (or equivalent) so processors attach `converted_from`/`llm_category` uniformly. (Addresses the no-settable-metadata gap on `LocalFileResource`.)

## 3. HtmlToMarkdownProcessor (test-first)

- [ ] 3.1 Failing tests: html→markdown + suffix flip to `md` + path fields (`relative_path`/`file_name`) rewritten to `.md` + `converted_from`; hash unchanged; bytes/non-html passthrough; converter-raises → original kept; **blank/whitespace markdown output → original kept** (never empty-persist); already-`md` resource is a no-op.
- [ ] 3.2 Implement `ResourceProcessor` (Protocol, mirroring `collectors/base.py`), `ResourcePipeline`, and `HtmlToMarkdownProcessor`: guard on `isinstance(content,str)` and `getattr(resource,"suffix","").lstrip(".").lower() in {"html","htm"}`; `markdownify(content, heading_style="ATX")`; rewrite `suffix` + `relative_path`/`file_name` when set; on raise/blank-output return the original resource. Green.

## 4. CategorizationProcessor (test-first)

- [ ] 4.1 Failing tests (mock chat model): valid in-list label → `metadata["llm_category"]`; out-of-list → `uncategorized`; `invoke` raises → `uncategorized`; empty/missing category list → `uncategorized`; content truncated to `max_chars`; a pre-existing `metadata["category"]` (e.g. Indico source category) is **never** overwritten; categorization disabled → no model built, no `llm_category` written.
- [ ] 4.2 Implement `CategorizationProcessor` writing `metadata["llm_category"]` (distinct from source `category`) via the metadata-attachment helper; lazily build the chat model via `get_model(provider, model, provider_config)`; invoke with a message list; validate against the configured list.
- [ ] 4.3 Source `provider_config` from `services.chat_app.providers.<provider>` (NOT `data_manager.processing`), mirroring `base_react.py`'s `_build_provider_config` (base_url/mode/models/extra_kwargs) — so custom local/vLLM endpoints work. Test that the resolved config is passed to `get_model`.

## 5. ProcessingPersistenceService (test-first)

- [ ] 5.1 Failing tests: `persist_resource` runs the pipeline then delegates to a mock inner `persist_resource` with the transformed resource and all three args; pass-through for `delete_resource`/`flush_index`/`delete_by_metadata_filter`/`reset_directory` and attribute access for `catalog`/`data_path`/`pg_config` (enumerate each attr callers read).
- [ ] 5.2 Implement `ProcessingPersistenceService` with explicit `persist_resource` override + `__getattr__` fallthrough to the inner instance. Green.

## 6. Shared factory + wiring (test-first)

- [ ] 6.1 Failing tests: a `processing` config with a processor enabled yields a wrapped service; all-disabled yields the bare `PersistenceService`; a missing block yields conversion-on/categorization-off.
- [ ] 6.2 Implement `build_persistence(config, data_path, pg_config) -> PersistenceService` reading `data_manager.processing` (+ provider config from `services.chat_app.providers`); build the chat model lazily.
- [ ] 6.3 Wire `data_manager.py:31` AND `uploader_app/app.py:50` to the factory — **uploader wiring is required** (the spec mandates UI uploads are not bypassed); `chat_app/document_utils.py:74` is delete-only and stays unwrapped.

## 7. Re-ingest chunk refresh (test-first)

- [ ] 7.1 Failing test: a previously-indexed HTML doc re-ingested as `.md` under the same hash does not leave stale HTML-derived chunks in `document_chunks`.
- [ ] 7.2 Implement detection of changed content under an unchanged hash (e.g. compare `documents.size_bytes`/`file_modified_at`, or a content hash) and delete/refresh that document's chunks before re-embedding in the vectorstore update path.

## 8. Config block

- [ ] 8.1 Insert a `processing:` block under `data_manager` between base-config.yaml lines 314 and 315 (sibling of `sources`/`utils`): `html_to_markdown.enabled` default true; `categorization.enabled` default false; `provider`, `model`, `max_chars`, `categories`; Jinja `{{ ... | default(..., true) }}` style.

## 9. Docs

- [ ] 9.1 Document the `processing` block in `docs/docs/configuration.md`: config keys, the convert→categorize→persist data-flow, the `llm_category` key (vs source `category`), provider-config sourcing, the per-document LLM cost caveat, the local-`.html`-upload limitation, the re-ingest/stale-chunk behavior, and the out-of-scope items.

## 10. Validate & gate

- [ ] 10.1 `openspec validate add-html-markdown-categorize-ingest --strict` passes.
- [ ] 10.2 `bash scripts/gate.sh`: tests pass; diff-cover ≥ 80% on changed lines (cover success and error branches); `isort src/`, `black --check` on touched files.
