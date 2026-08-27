# Tasks — HUIT Bedrock tool calling

Every checkbox below is one loop turn and ends **green and committed**. Where a checkbox
says RED, write the failing test, watch it fail *for the right reason*, write the smallest
code that passes, run the gate, and commit — all inside that one checkbox. Never end a task
with the suite red, and never use `--no-verify`.

The gate is `bash scripts/gate.sh` (see `CLAUDE.md`). On this host it needs the project
interpreter on `PATH`:

```
PATH=/home/a2rchi/miniforge3/envs/archi/bin:$PATH
```

Focused run while working:

```
/home/a2rchi/miniforge3/envs/archi/bin/python -m pytest tests/unit/test_huit_bedrock_tool_calling.py -q
```

Three standing notes for every task:

- **Scope.** The only files this change edits are
  `src/archi/providers/huit_bedrock_provider.py` and
  `tests/unit/test_huit_bedrock_tool_calling.py`. No other provider, no config, no
  template. If a task seems to need a third file, stop and revise the design first.
- **No live calls in tests.** Every test fakes the transport by patching
  `requests.post` in the provider module. The proxy's tool support was already verified
  against the live endpoint during design; the suite must stay offline and free.
- **Inert by default.** The provider is on the live agent path and carries the RAGAS
  judge's traffic. After every task, task 6's regression must still pass.

## 1. RED: structured output stops refusing

- [ ] Test: build a `HuitBedrockChat` and assert `with_structured_output({...})` returns a
      runnable rather than raising `NotImplementedError`. Watch it fail with exactly that
      `NotImplementedError` — the failure message is the proof the test binds to the real
      gap.
- [ ] Implement the minimum: `bind_tools` on `HuitBedrockChat` returning
      `self.bind(tools=[...], ...)`, converting via `convert_to_openai_tool` and renaming
      `parameters` to `input_schema`.
- [ ] Gate, commit.

## 2. RED: tools reach the body in Anthropic shape

- [ ] Test: bind one dict-schema tool, invoke against a faked `requests.post`, and assert
      the captured body's `tools[0]` carries `name`, `description` and `input_schema`, with
      the parameter schema under `input_schema` (not `parameters`).
- [ ] Add a second case binding a Pydantic class, asserting the same shape — this is what
      proves the conversion goes through `convert_to_openai_tool` rather than assuming a
      dict.
- [ ] Gate, commit.

## 3. RED: `tool_choice` encodes all four ways

- [ ] Test, parametrized: `None` → key absent; `"any"` → `{"type": "any"}`; `"auto"` →
      `{"type": "auto"}`; `"my_tool"` → `{"type": "tool", "name": "my_tool"}`; and a dict
      passes through unchanged.
- [ ] Implement the mapping in `bind_tools`.
- [ ] Gate, commit.

## 4. RED: unknown keywords are tolerated

- [ ] Test: `bind_tools([...], tool_choice="any", ls_structured_output_format={...})` does
      not raise. This is the exact call langchain-core's `with_structured_output` makes, so
      the test cites that call site in a comment.
- [ ] Gate, commit.

## 5. RED: a `tool_use` block becomes a tool call

- [ ] Test: fake a response whose `content` holds a `tool_use` block; assert the returned
      `AIMessage.tool_calls` has one entry with the block's name, `args` from its `input`,
      and its `id`.
- [ ] Test: a response mixing a text block and a `tool_use` block keeps the text as message
      content *and* populates `tool_calls`.
- [ ] Implement the block split in `_generate`.
- [ ] Gate, commit.

## 6. RED: unbound calls are byte-for-byte unchanged

- [ ] Test: invoke with no tools bound and assert the captured body contains neither
      `tools` nor `tool_choice`, and that the existing keys (`anthropic_version`,
      `max_tokens`, `temperature`, `messages`) are unchanged.
- [ ] Confirm it passes without new code — if it does not, the earlier tasks over-reached.
- [ ] Gate, commit.

## 7. RED: end-to-end structured output

- [ ] Test: `with_structured_output(schema).invoke([...])` against a faked transport whose
      response carries a `tool_use` block matching the schema; assert the parsed dict comes
      back. This is the test that mirrors `src/evaluation/qa/runtime.py:223`, the call the
      whole change exists to unblock — name it so that is obvious.
- [ ] Gate, commit.

## 8. Verify and open the PR

- [ ] Full suite plus `bash scripts/gate.sh`; confirm patch coverage clears 80%.
- [ ] Re-run the live probe from the design against the real proxy once, by hand, to
      confirm the shipped `bind_tools` path produces the same `stop_reason: tool_use` the
      hand-rolled probe did. Record the result in the PR body; do not add it to the suite.
- [ ] Push the branch, open the PR against `fasrc/archi` base `dev`, and post
      `@codex review`.
