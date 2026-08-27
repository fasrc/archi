# Tasks — HUIT Bedrock tool calling

Every numbered **section** below is one loop turn and ends **green and committed**: write
the failing test, watch it fail *for the right reason*, write the smallest code that passes,
run the gate, and commit. The checkboxes inside a section are the steps of that one turn, so
the intermediate ones are expected to be red — only the section boundary is a commit point.
Never end a *section* with the suite red, and never use `--no-verify`.

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

## 8. RED: a profile's timeout reaches the transport

`ModelDescriptor.provider_kwargs` passes an evaluator profile's timeout as `timeout`, but
`get_chat_model` copies only `max_tokens`, `temperature`, `anthropic_version` and
`request_timeout` — so the profile value is dropped and the model keeps its 120s default.
RAGAS judges the same model at 300s, so this is part of judge parity, not a stray fix.

- [ ] Test: `get_chat_model(model, timeout=300)` produces a model with
      `request_timeout == 300`.
- [ ] Test: when both `timeout` and `request_timeout` are given, `request_timeout` wins.
- [ ] Implement the alias.
- [ ] Gate, commit.

## 9. The catalog stays honest

- [ ] Test: every entry in `DEFAULT_HUIT_BEDROCK_MODELS` still reports
      `supports_tools is False`, with the test's docstring recording *why* — single-shot
      bound tools work, but the flag is read by the chat app's model picker
      (`src/interfaces/chat_app/app.py:3954`) where it answers "can the agent use tools",
      and multi-turn loops still break on history serialization.
- [ ] Confirm it passes with no production change. Do **not** flip the flags here; that
      belongs to the follow-up that fixes tool-call history.
- [ ] Gate, commit.

## 10. Documentation

AGENTS.md requires docs for user-facing behavior and public API changes, or a stated reason
none is needed. `bind_tools` on a provider is a public API change.

- [ ] Update the HUIT Bedrock section of the provider documentation to say single-shot
      bound tools and structured output now work, that multi-turn agent tool loops do not
      yet, and that `supports_tools` stays `false` until they do.
- [ ] Note in the PR description which docs changed.
- [ ] Gate, commit.

## 11. Verify end-to-end through the console, not just the proxy

A direct model probe cannot catch a failure in the path this change exists to unblock:
evaluator-profile loading, provider construction from an empty provider config, console
orchestration, and persisted results. AGENTS.md's validation policy asks for an end-to-end
check against the running deployment.

- [ ] Full suite plus `bash scripts/gate.sh`; confirm patch coverage clears 80%.
- [ ] Live probe of the shipped `bind_tools` path against the real proxy, by hand. Record
      it in the PR body; do not add it to the suite.
- [ ] **After merge and `bash deploy/scripts/redeploy.sh`** — the console's scorer runs on
      baked site-packages, so the merged provider does not exist in the container until a
      redeploy — upload a `huit_bedrock` evaluator profile through `/evaluations`, run one
      small evaluation, and confirm a scored run with real atom judgments plus clean
      chatbot logs. Record the run id.
- [ ] Push the branch, open the PR against `fasrc/archi` base `dev`, and post
      `@codex review`.
