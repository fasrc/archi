# Ralph Loop: archi

You are building this project iteratively. Each invocation, do ONE task.

Your ENTIRE job this invocation is the first unchecked task in /tasks.md —
nothing else. Do not do work that is not that task: no memory management, no
unrequested refactors, no documentation side-quests, no reorganizing. If the
task is small, finish it and stop; do not look for extra things to do.

**Review findings come first.** If /review-findings.md exists and is non-empty,
an independent review flagged problems with already-committed work — resolving
those findings (then committing) IS your one task this invocation, ahead of any
/tasks.md task. Fix the implementation to satisfy the review; do not edit specs
or tests to silence a finding. Do not pick a /tasks.md task while
/review-findings.md still has open items.

## Workflow
1. Read /openspec/ to understand the architecture. `/tasks.md` is a symlink to
   `openspec/changes/add-title-aware-retrieval/tasks.md`; that change's specs
   live in `openspec/changes/add-title-aware-retrieval/specs/` and the baseline
   capabilities in `openspec/specs/`.
2. Read /tasks.md and pick the first unchecked task
3. If the task requires a spec that doesn't exist, write the spec first
   in /openspec/ (follow the existing OpenSpec change/delta layout) and stop.
   Mark task progress, commit, exit.
4. If the spec exists but tests don't, write the test suite in
   /tests/ (mirror the existing `tests/unit/` layout) derived from the spec.
   Stop. Commit. Exit.
5. If tests exist but fail or are missing implementation, implement
   until all tests pass. Then commit. Exit.
6. Before any commit, run the FULL gate and fix any failure before
   committing (do not commit red):
   `./scripts/gate.sh`
   This is the single source of the gate (the same script CI and the
   pre-commit hook run), executing format → lint → test (with COVERAGE) in CI
   order. It FORMATS the tree (black, isort) rather than only checking, so you
   never fail CI on formatting. The gate enforces a coverage threshold on
   `src/data_manager/vectorstore` — write tests that exercise the REAL code
   path to meet it. If some code genuinely cannot be reasonably tested, exclude
   it with the standard coverage pragma (`# pragma: no cover`) or escalate to
   /docs/questions.md; NEVER delete tests or lower the threshold to make the
   gate pass. The pre-commit hook runs this script and BLOCKS the commit if it
   fails — never bypass it with `git commit --no-verify`.
7. Mark the task complete in /tasks.md if and only if the full
   spec→test→implementation cycle for it is done and green.

## Conventions
- Prefer existing, well-tested libraries over bespoke code; document any
  adopt/reject choice in /docs/decisions/
- One subsystem per commit; commit messages reference the task
- End each commit message with a trailer line `Ralph-Task: <the tasks.md task
  this commit completes>` so git history links cleanly to the task list
- Do NOT add a `Co-Authored-By` trailer (or any AI/assistant attribution) to
  commits
- Remove ALL temporary debugging scaffolding before committing — `print()`,
  ad-hoc logging, diagnostics, etc. Such scaffolding must never land in a
  commit. If a test is failing, fix the cause; do not leave probes behind.
- Never modify another subsystem's tests to make your code pass
- A task may carry a trailing work-class tag like `(stateful)` — an operator hint
  the RUNNER reads to pick the model for the turn. Leave it intact when you check
  the task off; do not add, remove, or invent these tags. Classification is the
  operator's job, never yours.

## Discipline
You act as an automated operator inside each turn — you reproduce failures and
reason about external systems — so undisciplined action burns the turn. Apply:
- **Triage before brute force.** When a test fails intermittently, triage first:
  is the failure *possible from the code path* you changed, an *infrastructure
  artifact* (network, clock, ordering), and is reproducing it *worth this turn*?
  Only then run a reproduction loop — never spin reproductions blind.
- **Defer to ground truth on external systems.** When you would explain why CI,
  the reviewer, or an API behaved a certain way, state what you have *verified*
  and flag what is *inferred* — do not assert a tidy causal narrative from
  indirect signals. The gate and CI are ground truth; read them, don't theorize
  past them.
- **Keep scope constraints in this prompt, not the filesystem.** Do not create
  scratch files, scope notes, or self-directed TODO markers to steer yourself;
  the task plus these rules are the scope, and any debug scaffolding you add is
  removed before the commit (above).

## Stop conditions
- If /tasks.md is fully checked, write "RALPH: project complete"
  to /STATUS.md and exit
- If you encounter a decision not covered by specs, write the
  question to /docs/questions.md and exit without committing code
- If tests have been red for 3 consecutive commits on the same task,
  write to /STATUS.md and exit for human review

## Important
- Do not invent requirements not in the specs
- Do not skip the spec or test phase to get to implementation faster
- Do not modify /openspec/ specs to match your implementation; modify
  the implementation to match the spec, or escalate via /docs/questions.md
- NEVER write to /STATUS.md except exactly as the Stop conditions specify,
  and when you do, write a non-empty one-line reason — never a blank or
  whitespace-only file (a blank STATUS.md falsely signals the loop to stop)
- A single focused implementation is preferred. Do not spin up multi-agent
  workflows/subagents for a routine single-module task — it costs more than
  it's worth and this is one task per invocation
