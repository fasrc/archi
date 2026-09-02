# Design — complete the optional markdown chunking strategy

All file:line anchors were verified against `origin/dev` at `7c9915d0` on 2026-09-01. The
approved plan with the full evidence trail is `/home/austin/.claude/plans/structured-mixing-sutton.md`.

## Context

Hierarchical chunk logic lives in `src/data_manager/vectorstore/node_parsing.py` (pure
llama-index). `build_hierarchical_nodes(document, *, strategy, parent_chunk_size,
child_chunk_size)` (node_parsing.py:73) dispatches to `_parse_sentence`
(node_parsing.py:181) or `_parse_markdown` (node_parsing.py:226). The caller is
`VectorStoreManager._build_hierarchical_payload` (manager.py:791), which merges node
metadata into persisted rows at manager.py:834-835 (`{**file_level_metadata,
**node_metadata}`). Both `document_parent_nodes.metadata` and `document_chunks.metadata`
are unfiltered `jsonb`; the retrieval path returns the blob whole
(`postgres_vectorstore.py:23-56` uses it as the base dict).

Current `_parse_markdown` state: it reads only section text
(`section.get_content()`, node_parsing.py:237); it takes no `parent_chunk_size`; the
strategy applies globally to all file types (read once at manager.py:130).

Pinned parser facts (`llama-index-core==0.14.19`, installed source): `MarkdownNodeParser`
writes `node.metadata["header_path"]` on every section (markdown.py:118-123); it is
fence-aware (markdown.py:60-66); an empty heading (`### `) parses with empty text and no
crash; preamble and H1 sections get `header_path == "/"`, never a missing key.
`SentenceSplitter` defaults `chunk_overlap=200` and raises `ValueError` when
`chunk_overlap > chunk_size`; `_resolve_chunk_sizes` (manager.py:35-45) does no bounds
check, so a configured `child_chunk_size < 200` fails every document quietly at ingest.

## Goals / Non-Goals

**Goals:**

- Chunk metadata carries the Markdown header hierarchy under the opt-in `markdown` strategy.
- A header section longer than `parent_chunk_size` splits into several parents.
- Only Markdown files take the markdown parser; other files fall back to `sentence`.
- A small configured `child_chunk_size` works instead of quietly failing the corpus.
- The feature stays off by default; zero new config keys; zero new dependencies; no schema change.

**Non-Goals:**

- The #399/#400 HTML→Markdown ingest fixes (independent data-quality issues).
- A re-embed or re-chunk CLI command.
- Retriever or citation use of `header_path` (stored, not yet consumed).
- Any change to the shipped `sentence` default or the `character` legacy path.
- The langchain splitter family or `MarkdownElementNodeParser` (needs an LLM).

## Decisions

1. **Complete the existing `markdown` strategy in place; no new strategy name.**
   No deployment uses `markdown` today, so in-place completion breaks nobody, and the
   opt-in surface (one existing config value) stays as small as possible.
   Alternative rejected: a second value such as `markdown_auto` — two names for one
   behavior, and the half-built value stays as a trap.

2. **Propagate only `header_path` from section nodes.** Section nodes carry document
   metadata plus `header_path`; the extraction takes only `header_path`, so document
   metadata merges exactly once at `HierarchicalNode` construction. Store `"/"` as-is for
   preamble and H1 sections — the key is then always present under the markdown strategy,
   which keeps `jsonb` queries uniform.

3. **Internal return shape becomes a 3-tuple** `(parent_text, child_texts,
   extra_metadata)` for both `_parse_sentence` (returns `{}`) and `_parse_markdown`.
   Verified: no file outside `node_parsing.py` references the private helpers, and the
   tests import only public names.

4. **Section cap via `SentenceSplitter(chunk_size=parent_chunk_size, chunk_overlap=0)`.**
   Zero overlap because sections are semantic units, not sliding windows — the 200-token
   default would embed duplicate content into sibling parents and their children.
   Alternative rejected: langchain `RecursiveCharacterTextSplitter` — a second splitter
   family with character-based sizes on a token-based path, and `langchain-text-splitters`
   is absent from `pyproject.toml` (deployment images run `pip install .`), so it would
   need a dependency change.

5. **Child splitter gets an explicit clamped overlap** (20 tokens, clamped below the
   chunk size, matching `HierarchicalNodeParser.from_defaults` on the sentence path).
   This fixes the latent `child_chunk_size < 200` crash and makes small-size unit tests
   possible. The crash fix ships inside this change because the section-cap work touches
   the same lines.

6. **Per-file dispatch by suffix, computed in `_build_hierarchical_payload`.**
   Detection: catalog metadata suffix normalized as `suffix.lstrip(".").lower() in
   {"md", "markdown"}` (the idiom `processing.py:166-169` uses), with a
   filename-extension fallback. The normalization matters: converted web pages and
   git-source files store `"md"`, local files store `".md"` (`localfile_resource.py:44`).
   The decision helper lives in `node_parsing.py` (95% covered, black-clean); manager.py
   gets one thin call before the `build_hierarchical_nodes` call at manager.py:815,
   outside the uncovered `apply_stemming` branch (manager.py:809-810, 825-826).
   Alternative rejected: dispatch inside `build_hierarchical_nodes` — the function is
   deliberately file-agnostic (duck-typed document in, nodes out), and the suffix belongs
   to the caller's catalog context.

## Risks / Trade-offs

- [Strategy flip re-chunks nothing] `update_vectorstore` diffs by `resource_hash`
  (manager.py:241-300); strategy is not a change signal, and `redeploy.sh` preserves
  volumes — a flipped deployment stays mixed indefinitely. → Mitigation: the
  config.example.yaml comment states the caution and the two clean paths (volume nuke +
  recreate, or delete the collection's rows from `document_chunks`,
  `document_parent_nodes`, and `documents`, then re-run the data manager).
- [#399 false headings] Broken fence conversion puts false `#` lines into persisted web
  markdown, which the markdown strategy will treat as real section starts. → Mitigation:
  none needed here; the #399 fix improves results independently, and the parser itself
  never splits inside a correctly fenced block (pinned by a test).
- [Overlap change alters existing markdown-strategy output] The child splitter moves from
  default overlap 200 to clamped 20. → Accepted: no deployment uses the strategy, and the
  sentence path already uses 20 via `HierarchicalNodeParser` defaults.
- [Behavior drift on a future llama-index bump] `header_path` format and fence handling
  are upstream behavior. → Mitigation: unit tests pin the fence rule, the `"/"` root
  value, and the metadata key name.

## Migration Plan

None required. No schema change, no config change, no default change. Rollout is: merge,
then any operator who wants the feature sets `chunking.strategy: markdown` and forces a
re-ingest (see the caution above). Rollback is: set the strategy back and force a
re-ingest the same way.

## Open Questions

- GitHub issue + milestone placement (candidate: v2026.10.0 via the #396 toggle-measurement
  argument) — a human scheduling decision, recorded outside this change.
