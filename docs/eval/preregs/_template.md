# Pre-registration: `<eval-round-name>`

> **Lock this file (commit it to the benchmarking branch) BEFORE running the eval.** The whole point of pre-reg is to time-stamp the decision rule so we can't quietly cherry-pick the metric that flatters the preferred config after the fact.

## Round metadata

| Field | Value |
|---|---|
| Round ID | `YYYY-MM-DD-<short-name>` (e.g. `2026-06-15-v2-lean-vs-v1-strict`) |
| Operator | `<your name>` |
| Date locked | `<YYYY-MM-DD>` |
| Commit at lock | `<git short-sha after committing this file>` |
| Question bank | `config/benchmarking/<round>.json` (commit-sha pinned) |
| Anchors | `examples/benchmarking/anchor_questions.json` (commit-sha pinned) |
| Configs compared | `<config A path>` vs `<config B path>` (vs `<config C>`, etc.) |
| Judge | `<provider/model>` (e.g. `huit_bedrock / us.anthropic.claude-sonnet-4-5-20250929-v1:0`) |
| Evaluators (target N) | `<N>` graders, target `min_submitted = <2 or 3>` per record |

## Primary hypothesis

State the hypothesis as a falsifiable claim. **Avoid weasel words** ("might be better", "could improve").

> Example: *Config B (v2-lean prompt) produces responses that human evaluators prefer over Config A (v1-strict prompt) on the FASRC question bank, when both are run against the same SUT model.*

## Primary outcome (the metric that decides)

Exactly one metric. If you list two, pick one as primary and the others go in "secondary".

- **Metric:** `<e.g. A/B winner rate from human evaluators>`
- **Computed how:** `<e.g. per-record majority vote among submitted responses; ties resolved by quality score>`
- **Source data:** `<grades.json from archi grade --export>`

## Decision rule

Three states must be explicitly defined before running. Vague rules invite post-hoc reinterpretation.

| Outcome | Threshold | Action |
|---|---|---|
| **Adopt B** | `<e.g. B wins ≥60% of records with κ ≥ 0.4>` | Switch production to B |
| **Reject B** | `<e.g. B wins ≤45% OR judge-vs-human correlation < 0.3>` | Keep A; close this experiment |
| **Inconclusive** | `<the gap in between>` | Either run another round with more questions, or accept current state |

## Secondary analyses (planned)

List everything you intend to compute beyond the primary outcome. The point is to commit to looking at these BEFORE seeing the data — anything you didn't list here and later compute is exploratory, not confirmatory.

- [ ] Per-failure-mode tag distribution (which kinds of questions does each config struggle on)
- [ ] RAGAS↔human correlation (does the judge predict humans, or are we wasting compute?)
- [ ] Anchor-question regression check (did easy-retrieve scores drop vs. last round?)
- [ ] Per-grader bias distribution (any single grader doing all the heavy lifting?)
- [ ] Should-refuse anchor compliance (did either config hallucinate on out-of-scope?)

## Stopping rule

When do we stop collecting grades? Define BEFORE evaluators start.

- **Target N records graded:** `<number>`
- **Minimum `min_submitted` per record:** `<2 or 3>`
- **Hard deadline:** `<YYYY-MM-DD>` — if not enough grades by this date, analyze with what we have and flag the shortfall

## Known voice/blinding caveats

Pre-record what's NOT blinded so we can't pretend it was later.

- [ ] Model voice (Claude vs Qwen cadence) is detectable to graders even with config metadata hidden
- [ ] Prompt style (terse v2-lean vs verbose v1-strict) is detectable
- [ ] `<any other leak specific to this round>`

## Out of scope for this round

Things that would be confounders if mixed in. Defer them to a separate round.

- `<e.g. don't also change embedding model in this round — that's a separate variable>`
- `<e.g. don't change the judge LLM mid-round — same judge for A and B>`
