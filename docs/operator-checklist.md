# Operator checklist — running the Ralph loop well

The harness instruments the *agent* (commit graph, gate, review, `.ralph/` state),
but it cannot gate **your** actions as the operator. This is the substitute: the
checklists to run *before* the high-risk operator actions, and the preconditions
to confirm *before* you walk away. The loop's clean output is not evidence you ran
it well — see "Output ≠ discipline" at the bottom.

## Pre-action checklists

Run the matching checklist *before* the action, not after it goes wrong.

### Before you background a job
- [ ] Will I actually read this job's output, or am I backgrounding it to avoid waiting?
- [ ] Is there a bound on it (timeout / max iterations), so it cannot run forever unwatched?
- [ ] If it fails or hangs, will I find out — or will it fail silently behind me?
- [ ] Is a single foreground run cheaper than the coordination cost of backgrounding it?

### Before you reproduce a failure
- [ ] Is this failure **possible** from the code path I'm actually changing?
- [ ] Could it be an **infrastructure artifact** (network, clock, ordering, a flaky dep) rather than a real defect?
- [ ] Is reproducing it **worth it** right now — or do I already have enough signal to act?
- [ ] If I start a reproduction loop, what is my stop condition? (Never spin reproductions blind.)

### Before you assert a causal "why" about an external system
- [ ] What have I actually **verified** vs. what am I **inferring** from indirect signals?
- [ ] Did I read the **ground truth** (CI logs, the reviewer's actual comment, the API response) — not a proxy?
- [ ] Am I about to commit to a tidy narrative that the evidence doesn't yet support?
- [ ] State the verified facts and flag the inferences *as* inferences — do not present a guess as a finding.

## Autonomy preconditions — walk away only when ALL four hold

Unattended (walk-away) operation is an **opt-in mode**, not the default and not a
general accelerator. In evidence it was *catalytic and narrow-band* — it helped on
well-specified work where the model was right from the start, and was net-negative
otherwise. Confirm all four before leaving the loop unattended:

1. **Well-specified work.** The specs/tasks are clear enough that a turn cannot
   reasonably go sideways needing your judgment.
2. **Model matched from turn 1.** The work class is tagged and the dispatch table
   maps it to a capable-enough model — no "start cheap, escalate after a stall."
3. **You are genuinely absent.** If you'll be watching anyway, run it supervised
   (`make loop-once`, or `make loop` with eyes on it) — supervision catches more.
4. **For fan-out only:** single-unit build time dominates per-unit coordination
   cost. Parallel content fan-out (`extras/`) is **serial-by-default / unsupported**
   and was net-negative at small unit size — do not reach for it as a turnkey speedup.

If **any** precondition is false, build **supervised-direct** instead of invoking
unattended mode.

## Going faster: target serial latency, not turn throughput

The bottleneck is the **human-gate cycle** — PR → CI → review → fixes → merge — not
how many turns run. So velocity effort goes to *serial latency*:

- Batch a milestone's worth of work per PR (fewer gate cycles).
- Auto-merge on a clean review (`RALPH_AUTO_MERGE=1`) once you trust the gate.
- Match the model to the work class (the dispatch table) so a turn lands right the first time.

Adding containers/agents to the *same* milestone does **not** help: systems-layer
work shares files and collides, and turn throughput was never the constraint.

## Output ≠ discipline

A clean commit graph is **not** evidence of a well-run session. Resilience masks
sloppiness: a session can ship zero red commits while getting there through runaway
processes, brute-force reproduction loops, or wrong theories about external systems.
Discipline and output quality are decoupled — judge the run by whether you applied
the checklists above, not by the green graph the harness shows you.
