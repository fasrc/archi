## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-523-arm-divergence-seed-guard` exists, cut from `origin/dev`
      at `db701852`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-523-arm-divergence-seed-guard --strict` passed on the
      host. The `openspec` CLI is **not usable in this container** — do not run it, and do
      not add a task that does. It is already green.
- [x] 0.3 Baseline at `db701852`: `tests/unit/test_config_seed_resolve.py` → **4 passed**.
      `config_seed.py`, `fasrc_docs_agent.py` and `test_config_seed_resolve.py` are
      black-clean and isort-clean.
- [x] 0.4 Every design decision is made in `design.md` (D1–D7). You are not searching for an
      approach. Where the issue body and the design differ, the design wins.

## Rules that apply to EVERY task below

- Code changes are in `src/cli/tools/config_seed.py` (tasks 1–2) and a docstring in
  `src/archi/pipelines/agents/fasrc_docs_agent.py` (task 3). New tests go in a **new** file,
  `tests/unit/test_config_seed_arm_divergence.py`. Do not edit
  `src/utils/benchmark_provenance.py`, `scripts/benchmarking/compare_runs.py`,
  `src/cli/templates/**`, `deploy/**` or `config/**`.
- Import the ignore list and the walker — never copy them:
  `from src.utils.benchmark_provenance import DIVERGENCE_IGNORED_PATHS, asserted_config_divergence`.
- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed and the loop halts there.
- **Do not implement a later task's behaviour early.** Task 1 must not touch `seed_entry`.
- Before you add a test, `grep -n 'def test_' tests/unit/test_config_seed_arm_divergence.py`
  and pick a name that is not already used. The gate runs no linter, so a duplicate
  `def test_...` silently replaces the older test while the suite stays green.
- Never delete or weaken a test in `tests/unit/test_config_seed_resolve.py`; task 3 edits
  only its module docstring.
- Write arm files with `tmp_path` and `yaml.safe_dump`. Never touch real Postgres: patch
  `src.cli.tools.config_seed.PostgresServiceFactory`, `seed` and `record_deployment` with
  `monkeypatch.setattr` on the `config_seed` module.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. The pre-commit
  hook's black is a writer while CI's is an assert, so a commit can be pushed misformatted.
  After each commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer.
- Do not write any file under `docs/`. The PR body goes in `/tmp`, never in the repo. Do not
  add an entry to `docs/questions.md`.

## 1. The comparison helper and the fallback-candidate split

- [ ] 1.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Create `tests/unit/test_config_seed_arm_divergence.py` with a module
      docstring that cites #523, and tests that assert (each arm file is a full small config,
      e.g. `{"name": "a", "services": {"chat_app": {"force_initial_retrieval": True, "agents_dir": "/x"}, "benchmarking": {"agent_md_file": "a.md"}}, "data_manager": {"chunk_size": 1000}}`):
      (a) two files that differ only under `services.benchmarking` →
      `arm_config_divergence([a, b]) == {}`;
      (b) two files that differ only in `name` → `{}`; two files that differ only in
      `services.chat_app.agents_dir` → `{}`; and
      `config_seed.DIVERGENCE_IGNORED_PATHS is benchmark_provenance.DIVERGENCE_IGNORED_PATHS`
      (imported, not copied);
      (c) `force_initial_retrieval: True` vs `False` →
      `{b: ["services.chat_app.force_initial_retrieval"]}`;
      (d) a key present in only one file with a non-null value is reported, in **both**
      directions: extra `services.chat_app.recursion_limit: 50` only in the first file →
      reported for the second file; only in the second file → reported for the second file;
      (e) a difference under `data_manager` (`chunk_size` 1000 vs 500) is reported as
      `data_manager.chunk_size` (design D3);
      (f) three files where only the third differs → the dict has exactly one key, the
      third file; a single path → `{}`; an empty list → `{}`;
      (g) `fallback_candidates(str(dir / "config.yaml"))` with `config.yaml` present →
      `[]`; with `config.yaml` absent and `b.yaml`, `a.yaml` present → the two paths in
      sorted order; with an empty directory → `[]`.
      Run `python -m pytest tests/unit/test_config_seed_arm_divergence.py -q --no-header` and
      confirm the tests FAIL on `ImportError`/`AttributeError` for the missing names — not
      on a fixture error.

      **Then green.** In `config_seed.py`: import the two names from
      `src.utils.benchmark_provenance` (design D1); add `fallback_candidates(config_path)`
      and make `resolve_config_path` use it with no change to its return values (design D5);
      add `arm_config_divergence(paths)` per design D1–D4. Do not edit `seed_entry`.
      Confirm `tests/unit/test_config_seed_resolve.py` is still **4 passed**.

      Then: format, `git add`, `bash scripts/gate.sh` exits 0, commit
      `fix(#523): compare multi-config arm files outside the arm-override paths`.

## 2. Refuse the deployment in seed_entry

- [ ] 2.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add tests to `tests/unit/test_config_seed_arm_divergence.py` that patch
      `config_seed.PostgresServiceFactory`, `config_seed.seed` and
      `config_seed.record_deployment` with recording fakes, then assert:
      (a) arm files `a.yaml` and `b.yaml` that differ in
      `services.chat_app.force_initial_retrieval`, with `config.yaml` absent →
      `seed_entry(str(tmp_path / "config.yaml"), {})` raises `SystemExit` with a non-zero
      `code`; `capsys.readouterr().err` contains
      `services.chat_app.force_initial_retrieval`, `a.yaml` and `b.yaml`; and
      `PostgresServiceFactory.from_env`, `seed` and `record_deployment` were **never**
      called;
      (b) arm files that differ only under `services.benchmarking` → `seed_entry` returns
      normally, `seed` was called once with the config loaded from `a.yaml` (assert on its
      `name`), and `record_deployment` was called once;
      (c) `config.yaml` present plus an `x.yaml` that differs from it in
      `force_initial_retrieval` → `seed_entry` seeds from `config.yaml` and raises nothing
      (design D5: no check when `config.yaml` exists);
      (d) a single arm file and no `config.yaml` → seeds normally.
      Confirm (a) fails (today `seed_entry` reaches the factory) and that the failure is on
      the assertion, not a fixture error.

      **Then green.** In `seed_entry`: call `fallback_candidates(config_path)` once; when it
      returns two or more paths, call `arm_config_divergence` on them before `load_config`
      and before `PostgresServiceFactory.from_env`; on any divergence print the message of
      design D6 to `sys.stderr` and `sys.exit(1)`. Keep the rest of `seed_entry` as it is.

      Then: format, `git add`, gate green, commit
      `fix(#523): refuse a multi-config deployment whose arms disagree`.

## 3. Correct the three docstrings

- [ ] 3.1 Edit, gate, commit.

      Replace the `resolve_config_path` sentence "Seeding from any one config is harmless:
      the benchmarker reads the YAML files directly and never consumes the seeded
      static_config." with the true statement of design D7. Replace the parenthetical
      "(Seeding Postgres from any one config is harmless — ...)" in the module docstring of
      `tests/unit/test_config_seed_resolve.py` with a pointer to the refusal (arms must agree
      outside the arm-override paths; see `test_config_seed_arm_divergence.py`). Replace
      "so prompt-vs-enforcement variants can be A/B'd in the sweep" in
      `src/archi/pipelines/agents/fasrc_docs_agent.py` (around line 250) with the design D7
      statement. Docstring text only — no code change in these files.
      Then `grep -rn "harmless" src/cli/tools/config_seed.py tests/unit/test_config_seed_resolve.py`
      prints nothing, and `grep -n "A/B'd in the sweep" src/archi/pipelines/agents/fasrc_docs_agent.py`
      prints nothing. Run
      `python -m pytest tests/unit/test_config_seed_resolve.py tests/unit/test_config_seed_arm_divergence.py -q --no-header`
      and confirm 0 failed. Format, `git add`, gate green, commit
      `docs(#523): config seeding is not harmless across arms`.

## 4. Publish

- [ ] 4.1 Push the branch and open the PR. The branch was cut with `checkout -b`, so its
      upstream is `origin/dev` — push with
      `git push -u origin fix/issue-523-arm-divergence-seed-guard` to repoint it. Confirm the
      push landed on **fasrc/archi**, not a fork:
      `git ls-remote --heads origin fix/issue-523-arm-divergence-seed-guard` must print the
      same SHA as `git rev-parse HEAD`. If it prints nothing, the push went elsewhere — stop
      and write the halt reason to `STATUS.md`.
      Write the PR body to `/tmp/pr-body-523.md` — **never** under `docs/` — and include the
      literal line `Closes #523`, the breaking-change note (a multi-config deployment whose
      arms differ outside `services.benchmarking`, the deploy-rewritten paths and `name` now
      fails at config-seed — accepted by the operator on 2026-09-26), and the test count
      before and after. If no PR exists for the branch yet
      (`gh pr list --repo fasrc/archi --head fix/issue-523-arm-divergence-seed-guard` is
      empty), run
      `gh pr create --repo fasrc/archi --base dev --title "fix(#523): refuse a multi-config deployment whose arms disagree" --body-file /tmp/pr-body-523.md`.
      If the review gate already opened one, `gh pr edit <pr> --repo fasrc/archi --body-file /tmp/pr-body-523.md`
      instead. A `Closes #523` in the title does not link the issue; it must be in the body.
      Verify the link: `gh pr view <pr> --repo fasrc/archi --json closingIssuesReferences`
      must list 523. If it does not, edit the body and re-verify.
      Then STOP. Do not merge this PR. A human merges, in daylight.

## Commands

```bash
# baseline — 4 passed at db701852, still 4 passed when done
python -m pytest tests/unit/test_config_seed_resolve.py -q --no-header

# the new suite
python -m pytest tests/unit/test_config_seed_arm_divergence.py -q --no-header

# the gate, before every commit
bash scripts/gate.sh
```
