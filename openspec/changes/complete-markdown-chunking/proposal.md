# Complete the optional markdown chunking strategy

## Why

The config value `data_manager.chunking.strategy: markdown` exists and is valid, but the strategy behind it is half-built: it throws away the header hierarchy that the parser already produces, it puts no size cap on a header section, and it applies to every file type instead of only Markdown. The v2026.10.0 feature-matrix benchmark campaign (#396) will measure this toggle; today it would measure a half-implementation and record misleading numbers. A latent crash compounds this: a configured `child_chunk_size` under 200 makes every document fail ingest quietly, because the splitter's default overlap (200 tokens) exceeds the chunk size and no bounds check exists.

Full triage brief and verified design: `/home/austin/.claude/plans/structured-mixing-sutton.md` (operator-approved 2026-09-01).

## What Changes

- The `markdown` strategy propagates `header_path` (for example `/Intro/Setup/`) from each section into parent and child chunk metadata. The metadata reaches the existing `jsonb` columns through the existing merge — no schema change.
- The `markdown` strategy caps oversized sections: a section longer than `parent_chunk_size` splits into several parents (zero-overlap sentence split), each with the same `header_path`.
- Per-file dispatch: under `strategy: markdown`, only Markdown files (suffix `md`/`markdown`, dotted or not, any case) take the markdown parser; every other file falls back to the `sentence` strategy.
- The child splitter gets an explicit clamped overlap, which fixes the latent `child_chunk_size < 200` crash on both hierarchical strategies.
- Operator documentation for the toggle, with the caution that a strategy flip does not re-chunk stored documents.
- NOT changed: the shipped default (`sentence`), the `character` legacy path, config keys, dependencies, DB schema, retriever code.

## Capabilities

### New Capabilities

- `markdown-structural-chunking`: header-aware chunk behavior for Markdown files under the opt-in `markdown` strategy — header hierarchy in chunk metadata, section size cap, fence and empty-heading tolerance, per-file dispatch.

### Modified Capabilities

- `hierarchical-rerank-retrieval`: the chunking requirement's "MAY use markdown-element parsing for markdown sources" becomes a reference to the new capability; the configured-chunk-sizes requirement gains a scenario that small configured sizes work instead of failing every document.

## Impact

- Code: `src/data_manager/vectorstore/node_parsing.py` (main change; 95% covered, black-clean); `src/data_manager/vectorstore/manager.py` (one thin dispatch line before the `build_hierarchical_nodes` call, outside the uncovered `apply_stemming` branch).
- Tests: `tests/unit/test_node_parsing.py`, `tests/unit/test_vectorstore_manager_hierarchical.py`.
- Docs: `deploy/fasrc-dev/config.example.yaml` comment (opt-in + re-ingest caution).
- Dependencies: none added. Uses pinned `llama-index-core==0.14.19` (`MarkdownNodeParser` there is fence-aware and emits `header_path`; verified in the installed source).
- Config: zero new keys — the Jinja template key-drop trap is not touched.
- Compatibility: no deployment uses `strategy: markdown` today (shipped default is `sentence`), so the in-place completion breaks nobody. Chunks produced under the completed strategy carry one extra metadata key; the retrieval path returns the metadata blob unfiltered, so downstream consumers are unaffected.
