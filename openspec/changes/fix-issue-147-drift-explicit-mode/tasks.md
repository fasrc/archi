## 1. Baseline & scope confirmation

- [x] 1.1 `export PATH=/home/austin/miniforge3/envs/archi/bin:$PATH`; run `python -m pytest tests/unit/test_goldenset_maintenance.py tests/unit/test_goldenset_maintenance_script.py -q` and record the passing count as the baseline. (baseline: 302 passed)
- [x] 1.2 Confirm no `.sh`/`.yaml`/`.yml`/`.md` caller relies on the implicit no-flag drift: `grep -rn "goldenset_maintenance.py drift" --include='*.sh' --include='*.yaml' --include='*.yml' --include='*.md' .` — expect no invocation that omits both `--model` and a mode flag. (If one exists, STOP: the break is not safe.) (confirmed: zero `.sh`/`.yaml`/`.yml` matches; the 4 no-flag `.md` examples are doc examples in `docs/docs/benchmarking.md`, updated in task 4, not automation callers)

## 2. RED tests (write first, watch fail)

- [x] 2.1 In `tests/unit/test_goldenset_maintenance_script.py`: `drift` invoked with a bank and `--allowed-hosts` but **neither** `--model` nor `--tripwire-only` exits non-zero, and the error text contains **both** `--model` and `--tripwire-only`.
- [x] 2.2 `drift ... --tripwire-only` exits 0, reports a moved hash, and the injected/patched `build_ask_llm` (or the drift LLM hook) is **never called** — assert on the mock, not on output absence.
- [x] 2.3 `drift ... --model <id> --tripwire-only` exits non-zero (contradictory instruction rejected).
- [x] 2.4 A `drift ... --tripwire-only` run's stdout **header** states the hash-only / tripwire-only mode — assert on the header string, not on the absence of verdicts.
- [x] 2.5 Regression: the `report` subcommand with **no** `--model` still runs the drift pass hash-only and exits 0 (proves the group-5 cron seam is intact). Mirror the existing report test's fixture/patching style.
- [x] 2.6 Run the new tests and confirm they FAIL for the expected reasons before writing implementation.

## 3. Implementation

- [x] 3.1 In `build_parser()`, replace the standalone `drift.add_argument("--model", …)` with a `drift.add_mutually_exclusive_group(required=True)` containing `--model` (unchanged help, tightened to say it selects the semantic pass) and a new `--tripwire-only` (`action="store_true"`, help: explicitly select the hash-only pass, no LLM call). Follow the `coverage`/`report` `add_mutually_exclusive_group()` shape.
- [x] 3.2 In `run_drift()`, replace the mode line with a defensive resolve: `tripwire_only = getattr(args, "tripwire_only", False)` then `ask_llm = None if tripwire_only else (build_ask_llm(args.model) if args.model else None)`. Do not otherwise change the pass logic.
- [x] 3.3 Add `tripwire_only=False` to `report.set_defaults(...)` so the reused runner reads a defined, inert attribute for the cron pass (matching the file's "named here rather than defaulted inside each runner" idiom). Leave `report`'s optional `--model` untouched.
- [x] 3.4 Add an unconditional mode-declaring header line at the top of `run_drift()` (before the `locked rows: …` summary) naming hash-only/tripwire vs reference-compared/semantic. Keep the existing drift NOTE (fires only when rows drifted) as-is.
- [x] 3.5 Run the new tests and the full `tests/unit/test_goldenset_maintenance*.py` suite green.

## 4. Docs

- [x] 4.1 In `docs/docs/benchmarking.md`, update the two `drift` examples so `--tripwire-only` and `--model` read as a deliberate choice, and state which mode the group-5 cron uses (hash-only, via `report` without `--model`).
- [x] 4.2 Ensure **every** `drift` example in that file carries `--allowed-hosts`; verify the two grep counts are equal: `grep -c 'goldenset_maintenance.py drift' docs/docs/benchmarking.md` equals `grep -A 3 'goldenset_maintenance.py drift' docs/docs/benchmarking.md | grep -c 'allowed-hosts'`.
- [x] 4.3 `(cd docs && mkdocs build --strict)` exits 0.

## 5. Verify & gate

- [ ] 5.1 `python -m pytest tests/unit/ -q` passes; total count > 1290.
- [ ] 5.2 `black src/ scripts/ tests/ -q && isort src/ scripts/ tests/ -q` clean; `openspec validate fix-issue-147-drift-explicit-mode --strict` passes.
- [ ] 5.3 Commit only green (pre-commit gate: format → lint → test, ≥80% diff coverage; never `--no-verify`). Short lowercase commit messages, no `Co-Authored-By`.
- [ ] 5.4 Open the PR: `gh pr create --repo fasrc/archi --base dev` with a `closes #147` body.
