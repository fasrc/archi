## 1. RED — failing tests for the /v1 single-source-list contract

- [ ] 1.1 `model: sonnet` — in `tests/unit/test_openai_compat_endpoints.py`, add a failing
      test: non-streaming request whose `final` event carries `answer` (bare), `response`
      (bare + wrapper's `Show all sources` block), `source_documents`, and
      `retriever_scores`; assert the message content contains exactly one `**Sources:**`
      block and no `Show all sources` block. Watch it fail on the duplicate pair.
- [ ] 1.2 `model: sonnet` — add a failing test: `final` event with `answer` present and no
      source documents → content equals the bare answer, no source section.
- [ ] 1.3 `model: sonnet` — add a failing test: `final` event with `answer: ""` and non-empty
      source documents → content is exactly the `format_citations` output (empty answer must
      not fall back to the list-bearing `response`).
- [ ] 1.4 `model: sonnet` — add a failing test for the defensive arm: `final` event without
      an `answer` key but with source documents → content is `response` verbatim with NO
      `format_citations` append (at most the wrapper's own single list; the duplicate-list
      defect must be unreachable from every arm). Fails on dev, which appends.

## 2. GREEN — endpoint content selection

- [ ] 2.1 `model: sonnet` — in `src/interfaces/chat_app/openai_compat.py`
      `_non_streaming_response`, select the content base per design D3: `answer` when the key
      is present (explicit `is not None`) + one `format_citations` append; when absent,
      `response` verbatim with no append. All group 1 tests green.

## 3. Final-event assembly helper (RED → GREEN)

- [ ] 3.1 `model: sonnet` — new `tests/unit/test_final_event.py`, failing: drive
      `build_final_event` with realistic pipeline output and assert (a) `answer` passthrough
      from `last_output["answer"]`, including empty string; (b) the `answer` key is OMITTED
      when `last_output` lacks it or is None — never defaulted to `""` (design D2, the
      answer-loss guard from adversarial round 3); (c) `response` passthrough untouched;
      (d) field parity with the event shape `stream()` emits on dev today (`type`,
      `conversation_id`, `archi_msg_id`, `message_id`, `user_message_id`, `trace_id`,
      `server_response_msg_ts`, `final_response_msg_ts`, `usage`, `model`, `model_used`,
      `source_documents`, `retriever_scores`).
- [ ] 3.2 `model: sonnet` — implement `src/interfaces/chat_app/final_event.py::
      build_final_event` to green those tests (keyword-only params; the
      `config_fingerprint.py` thin-call-site pattern).
- [ ] 3.3 `model: opus` — in `src/interfaces/chat_app/app.py` `stream()`, replace the inline
      `final` event dict literal (`app.py:2699-2718` on dev) with a `build_final_event(...)`
      call passing the in-scope locals 1:1. These lines are inside the generator body no unit
      test executes and this file is the known gate trap: keep the diff to the call site
      only and verify patch coverage stays ≥80%.

## 4. Verify, gate, and rule out the streaming path

- [ ] 4.1 `model: sonnet` — run the full unit suite and `bash scripts/gate.sh` (archi conda
      env); confirm patch coverage ≥80% with the uncovered `app.py` call-site lines counted.
- [ ] 4.2 `model: sonnet` — confirm and write down the streaming-path rule-out for the PR
      body: `_streaming_response` accumulates only mid-pipeline `chunk` events and never
      re-emits the finalized `response`, so it already emits exactly one source list.
- [ ] 4.3 `model: haiku` — `openspec validate fix-issue-245-single-v1-source-list --strict`.
- [ ] 4.4 `model: sonnet` — live end-to-end verification, BLOCKING for archive and for
      closing #245 (not for opening the PR): at the first dev redeploy carrying this change,
      issue a real non-streaming `/v1/chat/completions` request that returns sources and
      assert the content has exactly one `**Sources:**` section and no `Show all sources`
      block; confirm a native chat turn still renders its own source list
      (archi-dev-deploy-verify flow). This is the only check exercising the call-site wiring
      inside the real `stream()`. The PR body must state this pending condition. Deliberately
      not a pre-merge deploy: unmerged code on the shared dev stack would re-seed Postgres
      config and re-scrape the corpus overnight, and the producer failure classes are already
      pinned pre-merge by the helper tests (wrong value) and the endpoint's defensive arm
      (missing key) — design D2/D3, adversarial rounds 1–3.
