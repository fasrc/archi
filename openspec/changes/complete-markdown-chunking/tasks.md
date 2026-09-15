# Tasks — complete the optional markdown chunking strategy

TDD discipline per group: write the failing tests first, watch them fail, then the minimum
code to pass, then refactor. Run tests with `python -m pytest` (bare `pytest` in a worktree
imports the main checkout's code).

## 1. Red: node_parsing markdown-strategy tests (tests/unit/test_node_parsing.py)

- [x] 1.1 Test: nested doc (`#`/`##`/`###`) — every parent and child metadata carries the right `header_path`; document metadata keys still present
- [x] 1.2 Test: preamble and H1 sections carry `header_path == "/"` (key always present, never absent)
- [x] 1.3 Test: `#` lines inside a ``` fence start no new section
- [x] 1.4 Test: empty heading (`### `) processes without error and sections still come out
- [x] 1.5 Test: a section longer than `parent_chunk_size` yields >1 parent, all with the same `header_path`, no content overlap between pieces
- [x] 1.6 Test: `child_chunk_size=64` (below the 200 default overlap) does not raise on either hierarchical strategy
- [x] 1.7 Test: `sentence` strategy output and metadata unchanged (explicit metadata assertion as the refactor guard)
- [x] 1.8 Watch all new tests fail for the expected reason
- [x] 1.9 Test: packed parents count the `\n\n` joins against `parent_chunk_size` (PR #402 review round 1: Greptile P1 / Codex P2)
- [x] 1.10 Test: `~~~` fences stay whole under the cap, and a ``` line inside one does not close it (PR #402 review round 2: Codex P2)
- [x] 1.11 Test: capped parents are verbatim slices of the section (a long URL comes back intact), with the `\n\n` join kept as the fallback when a piece cannot be located (PR #402 review round 2: Codex P2)
- [x] 1.12 Test: a `#` line inside a `~~~` fence starts no section; the section parser is a fence-aware subclass of `MarkdownNodeParser` because upstream (llama-index-core 0.14.19) protects backtick fences only (PR #402 review round 3: Codex P2)
- [x] 1.13 Test: a ```` fence holding a literal ``` line and a `#` line stays one section and one parent; `_fence_segments` honors the opening run's character and length (CommonMark close rule) (PR #402 review round 4: Codex P2)
- [x] 1.14 Test: a fence behind `> ` blockquote markers stays whole under the cap; `_FENCE_RUN` accepts blockquote prefixes (PR #402 review round 5: Codex P2)
- [x] 1.15 Tests pinning two deliberate deviations from strict CommonMark, measured on the FASRC KB corpus (3,040 fence lines): indentation is not limited to three spaces (all 68 deep-indented fence lines sit in list-item content), and a closer with glued trailing text still closes (3 conversion artifacts, 0 "```not-a-close" lines) (PR #402 review round 5: Codex P2 ×2, pushback)

## 2. Green: node_parsing implementation (src/data_manager/vectorstore/node_parsing.py)

- [x] 2.1 `_parse_markdown`: accept `parent_chunk_size`; extract `section.metadata["header_path"]`; sub-split oversized sections with `SentenceSplitter(chunk_size=parent_chunk_size, chunk_overlap=0)`
- [x] 2.2 Child splitter: explicit clamped overlap (20 tokens, clamped below chunk size) on the markdown path; same clamp where the sentence path constructs its splitter if it shares the defect
- [x] 2.3 Internal 3-tuple shape `(parent_text, child_texts, extra_metadata)` for both `_parse_sentence` ({} extra) and `_parse_markdown`; merge in `build_hierarchical_nodes` as `{**document_metadata, **extra_metadata}`
- [x] 2.4 Update the module docstring (markdown strategy description: header_path, cap, dispatch)
- [x] 2.5 All group-1 tests green; full `python -m pytest tests/unit/test_node_parsing.py` green

## 3. Red: per-file dispatch tests

- [x] 3.1 Tests in test_node_parsing.py for the dispatch helper: suffix normalization accepts `"md"`, `".md"`, `"MD"`, `"markdown"`; rejects `"py"`, `"txt"`, `"pdf"`; filename-extension fallback works when suffix is missing
- [x] 3.2 Tests in tests/unit/test_vectorstore_manager_hierarchical.py: under `strategy: markdown`, a `.md` file takes the markdown parse and a non-markdown file takes the sentence parse (both dispatch branches exercised, for diff-cover)
- [x] 3.3 Watch the new tests fail for the expected reason
- [x] 3.4 Test in tests/unit/test_loader_utils.py: `select_loader` accepts `.markdown`, so the advertised suffix reaches the dispatch instead of failing as an unsupported format (PR #402 review round 2: Codex P2)

## 4. Green: dispatch implementation

- [x] 4.1 Dispatch helper in node_parsing.py (suffix + filename normalization → effective strategy)
- [x] 4.2 One thin call in `_build_hierarchical_payload` (manager.py, before the `build_hierarchical_nodes` call at ~:815, outside the `apply_stemming` branch)
- [x] 4.3 All group-3 tests green
- [x] 4.4 `_resolve_chunking_strategy` helper in manager.py: an absent `strategy` key resolves to `sentence` (the template default and the spec's opt-in scenario), not legacy `character`; tested in test_vectorstore_manager_hierarchical.py (PR #402 review round 4: Codex P2)

## 5. Docs

- [x] 5.1 `deploy/fasrc-dev/config.example.yaml`: comment documenting the `markdown` opt-in and the re-ingest caution (a strategy flip re-chunks nothing; clean paths: volume nuke + recreate, or delete the collection's rows and re-run the data manager)
- [x] 5.2 Same short caution comment beside the `chunking:` block in `src/cli/templates/base-config.yaml` (comment only — no new keys)
- [x] 5.3 `docs/docs/configuration.md` Chunking section: document the completed `markdown` strategy (per-file dispatch, `header_path`, section cap, fence handling, overlap clamp) and the safe forced re-ingest path — delete the collection's `document_chunks` and `document_parent_nodes` rows, keep `documents` (PR #402 review round 1: Greptile P2 / Codex P2 ×2)
- [x] 5.4 Docs and `config.example.yaml` comment: the re-ingest delete predicate is `metadata->>'collection' = '<name>' OR IS NULL`, because legacy rows carry NULL and `_collect_postgres_hashes` counts them as this collection's (PR #402 review round 2: Codex P2)
- [x] 5.5 Spec wording (hierarchical-rerank-retrieval delta): the overlap clamp is "at most the chunk size", upstream's legal range, matching the exact clamp settled in the pre-PR adversarial review; the near-duplicate regime at chunk sizes ≤ 20 is routed to #403, where the overlap becomes configurable (PR #402 review round 4: Codex P2, pushback)

## 6. Gate and wrap-up

- [x] 6.1 Full `python -m pytest tests/unit/` green
- [x] 6.2 `bash scripts/gate.sh` green (archi conda env; run it bare — no pipes or redirects)
- [x] 6.3 `git status` after every commit (the gate's black writer can rewrite the tree after staging)
- [x] 6.4 Adversarial review (`/codex:adversarial-review`); verify each finding against the code before acting; address findings
- [x] 6.5 Push branch `feat/markdown-aware-chunking`; open PR to `dev` on fasrc/archi with the five-file change set verified (proposal, design, 2 delta specs, tasks all committed); print the full PR URL
