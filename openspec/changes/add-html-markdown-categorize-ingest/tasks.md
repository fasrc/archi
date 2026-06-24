## 1. Dependency (both files)

- [ ] 1.1 Add `markdownify==<pin>` to `pyproject.toml` `dependencies` AND `requirements/requirements-base.txt` (next to `beautifulsoup4`), matching pins; resolve latest stable at apply time.
- [ ] 1.2 `pip install -e .`; confirm `import markdownify` works.

## 2. HtmlToMarkdownProcessor (test-first)

- [ ] 2.1 Write failing tests in `tests/unit/test_resource_processing.py`: html→markdown + suffix flip to `md` + `converted_from`; hash unchanged after conversion; bytes/non-html passthrough; converter-raises → unchanged; already-`md` resource is a no-op.
- [ ] 2.2 Implement `ResourceProcessor` (Protocol, mirroring `collectors/base.py`), `ResourcePipeline`, and `HtmlToMarkdownProcessor` guarding on `isinstance(content,str)` and `getattr(resource,"suffix","").lstrip(".").lower() in {"html","htm"}`; `markdownify(content, heading_style="ATX")`. Green.

## 3. CategorizationProcessor (test-first)

- [ ] 3.1 Failing tests (mock chat model): valid in-list label → `category`; out-of-list → `uncategorized`; `invoke` raises → `uncategorized`; empty/missing category list → `uncategorized`; content truncated to `max_chars` before invoke.
- [ ] 3.2 Implement `CategorizationProcessor` using a lazily-built `BaseChatModel` via `get_model(provider, model, provider_config)`; invoke with a message list; validate the label against the configured list. Green.

## 4. ProcessingPersistenceService (test-first)

- [ ] 4.1 Failing tests: `persist_resource` runs the pipeline then delegates to a mock inner `persist_resource` with the transformed resource and all three args; pass-through delegation for `delete_resource`/`flush_index`/`delete_by_metadata_filter`/`reset_directory` and attribute access for `catalog`/`data_path`/`pg_config` (enumerate each attr callers read).
- [ ] 4.2 Implement `ProcessingPersistenceService` with explicit `persist_resource` override + `__getattr__` fallthrough to the inner instance. Green.

## 5. Shared factory + wiring (test-first)

- [ ] 5.1 Failing tests: a `processing` config with a processor enabled yields a wrapped service; all-disabled yields the bare `PersistenceService` (no-op).
- [ ] 5.2 Implement `build_persistence(config, data_path, pg_config) -> PersistenceService` reading `data_manager.processing`; build the chat model lazily.
- [ ] 5.3 Wire `data_manager.py:31` and `uploader_app/app.py:50` to the factory (or descope the uploader with a test asserting current behavior + a follow-up issue, per the design open question).

## 6. Config block

- [ ] 6.1 Insert a `processing:` block under `data_manager` between base-config.yaml lines 314 and 315 (sibling of `sources`/`utils`): `html_to_markdown.enabled` default true; `categorization.enabled` default false; `provider`, `model`, `max_chars`, `categories`; Jinja `{{ ... | default(..., true) }}` style.

## 7. Docs

- [ ] 7.1 Document the `processing` block in `docs/docs/configuration.md`: config keys, the convert→categorize→persist data-flow, the per-document LLM cost caveat, the local-`.html`-upload limitation, and the out-of-scope items.

## 8. Validate & gate

- [ ] 8.1 `openspec validate add-html-markdown-categorize-ingest --strict` passes.
- [ ] 8.2 `bash scripts/gate.sh`: tests pass; diff-cover ≥ 80% on changed lines (cover success and error branches in `processing.py`); `isort src/`, `black --check` on touched files.
