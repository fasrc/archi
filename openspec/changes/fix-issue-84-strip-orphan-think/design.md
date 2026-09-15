## Context

`BaseReActAgent._parse_thinking_content` (`src/archi/pipelines/agents/base_react.py`,
~line 195) separates model output into `(visible_content, thinking_content)`. Today it
uses a single balanced-pair regex:

```python
thinking_pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL)
```

Qwen3 with reasoning enabled emits its opening `<think>` into the *prompt* (chat-template
prefill), so the *output* contains only orphan closing `</think>` tags. Those never match
the balanced pattern, so the reasoning before them leaks into the visible answer. The
method is the single definition shared by the sync `stream()` and async `astream()`
paths, so one fix covers both. There is currently no test for it.

## Goals / Non-Goals

**Goals:**
- Strip orphan `</think>` closing tags (one or many) from the visible answer while
  preserving the existing balanced-pair behavior exactly.
- Keep the removed reasoning in `thinking_content` (captured, not discarded).
- Guarantee the returned visible content never contains `<think>` or `</think>`.
- Add unit coverage for a previously untested method.

**Non-Goals:**
- Stripping untagged residual model prose (governed by `enable_thinking` / the agent
  prompt, not this stripper).
- Changing callers, the streaming paths, or the `(visible, thinking)` return contract.
- Handling orphan *opening* `<think>` with no close (not observed; the model prefills the
  open tag, so the failure mode is always a dangling close).

## Decisions

**Decision: two-pass strip — balanced pairs first, then split on the last orphan
`</think>`.**
1. Remove all balanced `<think>…</think>` pairs with the existing regex (unchanged
   behavior, unchanged capture into `thinking_content`).
2. If any `</think>` still remains in the intermediate text, treat everything up to and
   including the **last** remaining `</think>` as thinking; keep only the suffix after it
   as visible. Append that removed span to `thinking_content`.

Rationale: real leaked output interleaves several reasoning segments each ended by an
orphan `</think>`, with the genuine answer after the final one (the incident showed three).
Splitting on the *last* orphan collapses any number of leading orphan segments in one step
and yields the trailing answer, which matches the observed shape and the spec's
multiple-orphan scenario.

*Alternatives considered:*
- *Iteratively strip each `</think>` and the text before it* — equivalent result but more
  code; "split on the last occurrence" is the minimal expression.
- *Make the open tag optional in the regex* (`(?:<think>)?(.*?)</think>`) — greedy/lazy
  interaction across multiple orphans is error-prone and would still need care to avoid
  eating the real answer; a simple `rfind`/`rsplit` on `</think>` is clearer and provably
  correct against the fixtures.
- *Normalize by injecting a synthetic `<think>` at the start* — hacky and changes
  `thinking_content` boundaries unpredictably.

## Risks / Trade-offs

- **[A legitimate answer that itself contains the literal string `</think>` would be
  truncated.]** → Accepted: a real chat answer containing a raw `</think>` token is
  implausible, and the whole point is that any `</think>` reaching the user is a defect to
  strip. The spec makes "visible contains no `</think>`" the contract.
- **[Two-pass logic is slightly more than the one-liner it replaces.]** → Mitigated by
  confining the change to the method body (keeps the diff black/isort-clean and easy to
  review) and by exhaustive unit fixtures (balanced, single orphan, multiple orphans,
  no-tags, empty).
- **[Coverage gate.]** diff-cover requires ≥80% on changed lines; every new branch is
  exercised by a dedicated fixture, targeting 100% patch coverage.
