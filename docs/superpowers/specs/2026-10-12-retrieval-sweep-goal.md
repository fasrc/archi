/goal Run the offline retrieval sweep in docs/superpowers/specs/2026-10-12-retrieval-sweep-design.md and its plan docs/superpowers/plans/2026-10-12-retrieval-sweep.md, in this worktree, on claw.

END STATE (all true):
1. Code in scripts/benchmarking/retrieval_sweep/ with unit tests, built test-first; `bash scripts/gate.sh` green (archi env: /home/austin/miniforge3/envs/archi/bin first on PATH, via a wrapper script) on the last commit.
2. Round 0 passed and its numbers are printed in the transcript: stage-1 parity >= 100/102, stage-2 parity >= 100/102, determinism identical.
3. /home/austin/Projects/archi/bench_out/retrieval_sweep/2026-10-12/ledger.jsonl has one row per arm for rounds R1-R5 as the spec's table defines them (status ok or error), all with B0's snapshot fingerprint.
4. docs/docs/retrieval_sweep_2026_10.md exists: tune-set table of every arm (Hit@5, Recall@20/50/100, MRR@5, Hit@8, latency p50/p95, Holm p), the finalists, and their holdout Hit@5 next to the baseline's.
5. Branch pushed to origin as feat/retrieval-sweep and a DRAFT PR to fasrc/archi base dev is open; the transcript prints its full URL.
6. The last assistant message has a line `SWEEP DONE: <PR URL> finalists=<n>`.

PROOF: `gh pr view feat/retrieval-sweep --repo fasrc/archi --json isDraft,url` shows isDraft true, and `wc -l` on ledger.jsonl is printed in the transcript.

CONSTRAINTS:
- Postgres (container postgres-claw) is READ-ONLY: SELECT inside SET TRANSACTION READ ONLY only. No INSERT, UPDATE, DELETE, DDL.
- No redeploy, no `archi create`, no container restart, no edit to config/ (archi-config) or src/ product code, no GPU host, no LLM or judge calls.
- No new dependency. Pin every Hugging Face model to a commit revision; trust_remote_code only at a pinned revision.
- Holdout questions are used only in R5, once per finalist. Never choose with them.
- The finalist thresholds in spec 6.2 are fixed. Do not change them after R1 starts.
- Commit after each round with the ledger state in the message. Short lowercase messages, no Co-Authored-By trailer. Never --no-verify, never push to dev, never force-push, never merge.
- Run long jobs with run_in_background and wait with an until-poll; do not end a turn idle.
- Write prose in the repo's existing style; do not post on GitHub issues.

BLOCKED EXITS: if Round 0 parity fails after 2 fix attempts, or a step needs a human decision or a permission you lack, print `GOAL BLOCKED: <reason>` as the last line and stop. A transcript whose last line starts with `GOAL BLOCKED:` counts as the goal being finished (failed).

BOUND: stop at 14 hours wall time or 80 turns; then commit what exists, push, open the draft PR, and print `GOAL BLOCKED: bound reached at <round>`.
