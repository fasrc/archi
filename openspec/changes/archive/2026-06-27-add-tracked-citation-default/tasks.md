## 1. Tracked citation default (code, TDD)

> Seam changed during implementation: the default is appended to the resolved agent prompt in
> `agent_spec.load_agent_spec()` (keyed on the spec's declared `tools`), NOT in
> `base_react._build_system_prompt()`. Same outcome — the system prompt carries the baseline for
> retrieval agents — but avoids reflowing the large, non-black-clean `base_react.py` into the
> patch-coverage diff. See design.md "Injection point" seam note.

- [x] 1.1 Add a failing test (`tests/unit/`) that loads an agent spec (via
  `load_agent_spec_from_text`) whose declared `tools` include the vectorstore retriever
  (`search_vectorstore_hybrid`) with a trivial body that says nothing about citations, and
  asserts the resolved `spec.prompt` contains the `[title](url)` guidance and forbids bare `[n]`.
  Confirm it fails today.
- [x] 1.2 Add a test: a spec whose declared `tools` have NO vectorstore retriever (e.g. only
  `search_local_files`/`search_metadata_index`, or a non-retrieval tool) → guidance NOT appended.
  And a test pinning `RETRIEVAL_TOOL_NAMES == {search_knowledge_base, search_vectorstore_hybrid}`.
- [x] 1.3 Implement in `agent_spec.py`: module constants `DEFAULT_CITATION_GUIDANCE` +
  `RETRIEVAL_TOOL_NAMES` + `_apply_citation_guidance(prompt, tools)`, applied in both
  `load_agent_spec` and `load_agent_spec_from_text` so the resolved `prompt` carries the baseline.

## 2. Refresh tracked example agents

- [x] 2.1 Update EVERY tracked example agent under `examples/agents/` that models source
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

- [x] 3.1 `bash scripts/gate.sh` green (diff-cover ≥ 80% on changed lines); no new dependency.
- [x] 3.2 Unit: a minimal retrieval-agent prompt yields a system prompt carrying the citation
  baseline (covered by 1.1).
- [x] 3.3 **End-to-end (required by AGENTS.md "Deployment & Validation Policy"):** redeploy
  fasrc-dev and run at least one live retrieval chat turn; confirm in the response/logs that the
  agent renders an inline `[title](url)` citation and no bare `[n]` index — i.e. the behavior is
  driven by committed code. (To prove the tracked default specifically, temporarily verify with a
  retrieval-agent prompt that carries NO citation wording, or inspect the assembled system prompt
  in logs.) State the container validated (`chatbot-dev` + `data-manager-dev`/`postgres-dev`).
