# archi Development Workflow

How we work on **archi** (the `fasrc/archi` fork). Two layers: environment/git
conventions you must know first, then the two spec-driven development loops.

---

## Part A — Environment & Git Conventions (know these before doing anything)

1. **Two remotes, but `origin` is the working trunk.** `origin` = `fasrc/archi`
   (our fork — the de-facto trunk: it holds the Ralph harness and all daily work,
   and is 100+ commits ahead of upstream). `upstream` = `archi-physics/archi`
   (the original; we only sync from it occasionally, we do NOT PR to it day-to-day).
   **All PRs target `fasrc/archi:dev`** (`origin/dev`); `main` is release-only.

2. **Always branch from `origin/dev`.** Start every change with
   `git fetch origin && git checkout -b <branch> origin/dev`. Push to `origin`
   (`git push origin <branch>`), then open the PR with
   `gh pr create --repo fasrc/archi --base dev`.

3. **Commit message rules.** Short, lowercase summaries (e.g. `fix bug`,
   `add provider support`). **Never add `Co-Authored-By` lines.**

4. **Never bypass the pre-commit gate** with `--no-verify`. The local conda env
   `archi` (Python 3.11) has the full gate toolchain (langchain, llama-index,
   spacy, torch-cpu, pytest, diff-cover), so the gate runs locally. Gate requires
   ≥80% diff coverage on changed lines.

5. **Gitignored dev-only files** — do not touch in PRs:
   `deploy/fasrc-dev/config.yaml`, `deploy/fasrc-dev/agents/*.md`. Secrets live in
   `/home/austin/.secrets/archi-secrets.env`.

6. **Deployment reality.** The container runs a **non-editable** install
   (`pip install .`), importing from `site-packages/src`, not the repo tree.
   `docker cp` into a running container is invisible — only a **redeploy** bakes
   code changes. (Caveat: pytest from the repo cwd shadows site-packages, so tests
   can pass while the installed app is stale.)

---

## Part B — Loop 1: Planning & Specification (OpenSpec)

7. **Explore** — `/opsx:explore` to think through the problem before committing to
   a design.

8. **Propose** — `/opsx:propose` to create the change (proposal, design, specs,
   tasks). Validate with `openspec validate <change> --strict`.

9. **Branch + PR for review** — cut a branch for the proposal, push, open a PR.
   GitHub Copilot auto-reviews on open; add an `@codex review` PR comment to
   trigger the second automated reviewer.

10. **Receive reviews** — handle feedback with the
    `superpowers:receiving-code-review` discipline: **verify each finding against
    the codebase before implementing**, give reasoned technical pushback over
    performative agreement (no "you're absolutely right").

11. **Reply inline** — post a threaded reply *in each review thread* (not a
    top-level comment), referencing the fix commit. Then commit any fixes.

12. **Merge** — once review comments are resolved and CI is green.

---

## Part C — Loop 2: Implementation (TDD)

13. **Branch before applying** — always cut a fresh branch before starting
    implementation.

14. **Apply test-first** — `/opsx:apply <change>` to work the tasks, each one via
    `superpowers:test-driven-development`: write a failing test (RED) → watch it
    fail for the right reason → minimum code to pass (GREEN) → refactor. **Never
    write implementation before a failing test.** Mark tasks done as you go.

15. **Verify adversarially** — for non-trivial changes, before opening the PR: run
    parallel review subagents tasked to *refute* correctness, plus the full test
    suite. Scale the effort to the change's risk; skip for trivial edits.

16. **Branch + PR** — push, open the PR. Copilot auto-reviews; add `@codex review`.
    **Exception: do NOT request `@codex review` on mechanical "chore: archive"
    PRs** — nothing substantive to review.

17. **Receive reviews + reply inline** — same as steps 10–11.

18. **Merge** — when comments resolved and CI green.

19. **Release steps** (project-specific) — version bumps, image rebuilds, etc.,
    per the project's own conventions, before/with the change.

20. **Archive** — `/opsx:archive <change>` moves it to the archive and syncs the
    canonical specs. Commit, open the archive PR (no `@codex review`), merge.

---

## Part D — Supporting Habits

21. **Track work in Asana** at session end via the `archi-track-work-asana` skill
    — project p-Search-Engine-LLM, written for a skip-level manager, quantified,
    assigned to me.

22. **Defer work as AI work-orders** via `archi-followup-issue` — file a
    self-contained GitHub issue a future Claude can execute cold (objective, exact
    paths/SHAs, constraints, commands, machine-checkable acceptance criteria).

23. **Dev-deploy verification** uses `archi-dev-deploy-verify`; **LLM failover**
    (vLLM ⇄ Anthropic standby) uses `archi-dev-llm-failover` when dev chat 500s.
