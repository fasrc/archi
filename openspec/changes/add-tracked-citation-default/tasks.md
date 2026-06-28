## 1. Tracked citation default (code, TDD)

- [ ] 1.1 Add a failing test (`tests/unit/`) that builds a `BaseReActAgent` (or the minimal
  seam) whose `selected_tool_names` includes the vectorstore retriever (`search_vectorstore_hybrid`)
  and a trivial `agent_prompt` with no citation text, and asserts `_build_system_prompt()`
  contains the `[title](url)` guidance and forbids bare `[n]`. Confirm it fails today.
- [ ] 1.2 Add a test: an agent whose `selected_tool_names` has NO vectorstore retriever (e.g.
  only `search_local_files`, or no tools) → the guidance is NOT in the system prompt. And a test
  pinning `RETRIEVAL_TOOL_NAMES == {search_knowledge_base, search_vectorstore_hybrid}` (so a new
  retriever tool / an accidental broadening is visible).
- [ ] 1.3 Implement: module constant `DEFAULT_CITATION_GUIDANCE` + `RETRIEVAL_TOOL_NAMES` +
  `_has_retrieval_tool()`, and append the guidance in `_build_system_prompt()` when a vectorstore
  retriever tool is selected. Keep `agent_prompt` + `role_context` ordering intact.

## 2. Refresh tracked example agents

- [ ] 2.1 Update EVERY tracked example agent under `examples/agents/` that models source
  citation to use the inline `[title](url)` style:
  - `cms-comp-ops.md`: replace "results are numbered `[1]`/`[2]`… result indices" wording.
  - `indico-assistant.md`: its citation wording ("cite the speaker name, contribution title,
    and event… Include the contribution URL when available") does not model `[title](url)` —
    update it to cite as inline `[title](url)` Markdown links (and not bare indices).
  - Any other sibling with citation wording.
  Verify the spec scenario, not just one phrase: `git grep -nE "result indices|numbered \`\[1\]"
  examples/` returns nothing, AND each example agent that cites sources shows the `[title](url)`
  style (manual read / a small test asserting the example bodies contain `](` link syntax and no
  bare-index citation instruction).

## 3. Verify

- [ ] 3.1 `bash scripts/gate.sh` green (diff-cover ≥ 80% on changed lines); no new dependency.
- [ ] 3.2 Unit: a minimal retrieval-agent prompt yields a system prompt carrying the citation
  baseline (covered by 1.1).
- [ ] 3.3 **End-to-end (required by AGENTS.md "Deployment & Validation Policy"):** redeploy
  fasrc-dev and run at least one live retrieval chat turn; confirm in the response/logs that the
  agent renders an inline `[title](url)` citation and no bare `[n]` index — i.e. the behavior is
  driven by committed code. (To prove the tracked default specifically, temporarily verify with a
  retrieval-agent prompt that carries NO citation wording, or inspect the assembled system prompt
  in logs.) State the container validated (`chatbot-dev` + `data-manager-dev`/`postgres-dev`).
