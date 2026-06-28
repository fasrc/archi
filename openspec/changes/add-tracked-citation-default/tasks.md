## 1. Tracked citation default (code, TDD)

- [ ] 1.1 Add a failing test (`tests/unit/`) that builds a `BaseReActAgent` (or the minimal
  seam) whose `selected_tool_names` includes a retrieval tool (e.g. `search_local_files`) and a
  trivial `agent_prompt` with no citation text, and asserts `_build_system_prompt()` contains
  the `[title](url)` guidance and forbids bare `[n]`. Confirm it fails today.
- [ ] 1.2 Add a test: an agent whose `selected_tool_names` has NO retrieval tool → the guidance
  is NOT in the system prompt. And a test pinning `RETRIEVAL_TOOL_NAMES` membership (so adding a
  new retrieval tool without updating the set is visible).
- [ ] 1.3 Implement: module constant `DEFAULT_CITATION_GUIDANCE` + `RETRIEVAL_TOOL_NAMES` +
  `_has_retrieval_tool()`, and append the guidance in `_build_system_prompt()` when a retrieval
  tool is selected. Keep `agent_prompt` + `role_context` ordering intact.

## 2. Refresh tracked example agents

- [ ] 2.1 Update `examples/agents/cms-comp-ops.md` (and any sibling that mentions numbered
  result indices) to model inline `[title](url)` citation and remove the "results are numbered
  `[1]`/`[2]`… result indices" wording. (Verify: `git grep -n "result indices" examples/`
  returns nothing.)

## 3. Verify

- [ ] 3.1 `bash scripts/gate.sh` green (diff-cover ≥ 80% on changed lines); no new dependency.
- [ ] 3.2 Sanity: a fresh checkout with a minimal retrieval-agent prompt yields a system prompt
  carrying the citation baseline (covered by 1.1). No live redeploy required — but if redeployed,
  the gitignored fasrc-docs prompt's now-redundant block may be trimmed (deploy config, separate).
