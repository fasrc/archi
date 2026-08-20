> **Every group below must end with a green suite.** The gate runs before every commit and a
> group that ends red cannot be committed, which stalls the loop. Where a group contains a
> red step, the red observation and the fix that clears it belong to that **same** group and
> the same commit — do not split them across turns.
>
> Line numbers are as of `origin/dev` @ `07e007df`. Re-derive them with `grep -n` before
> relying on them; group 2 moves them.
>
> "the gate" below means the project gate command from `CLAUDE.md`. Both target files are
> black- and isort-clean on `origin/dev` (verified: zero churn lines), so there is no reformat
> trap. Run `black` and `isort` **before** `git add`, and confirm `git status` is empty after
> committing — the pre-commit hook formats after staging, so a file formatted by the hook can
> otherwise be pushed misformatted and redden CI.

## 1. Make the existing success-path test test what its name claims

`tests/unit/test_cli_create_dev_smoke.py::test_force_evaluate_still_removes_existing_runtime`
(`:673`) passes `examples/deployments/basic-openai/config.yaml`, which declares no
`services.benchmarking` block. Measured stage trace on `origin/dev`:
`['teardown', 'validate_configs:enter']` → `ValueError: Missing required field:
'services.benchmarking.agent_class'`. Its `len(teardowns) == 1` assertion passes today only
because the teardown precedes that refusal, so group 2 would turn it red for the wrong
reason. Fix the fixture first, while the assertion is still reachable — this group is green
on unmodified `src/`.

- [x] 1.1 Add a `benchmark_config` fixture writing a benchmarking-valid config into
  `tmp_path`. `validate_configs` requires all five blank-valued benchmarking keys from the
  rendered `base-config.yaml` — `agent_class`, `agent_md_file`, `provider`, `model`,
  `ollama_url`. **`ollama_url` is required even when `provider` is not `local`**; the
  provider-conditional rule at `config_manager.py:328-333` is a separate check with a
  different message. `agent_md_file` must resolve to an existing `.md` file
  (`config_manager.py:335-348`) — point it at `examples/agents/cms-comp-ops.md` via
  `REPO_ROOT`. Model the rest of the file on `examples/deployments/basic-openai/config.yaml`
  (`chat_app`, `vectorstore`, `data_manager`, plus the `data_manager.sources.links` block).
  Do **not** reuse `examples/benchmarking/benchmarking_configs/example_conf.yaml`: it sets
  `provider: local` with no `ollama_url` and fails validation too.
- [x] 1.2 Point `test_force_evaluate_still_removes_existing_runtime` at that fixture and add
  the `cli_main.TemplateManager` → `RuntimeError(SENTINEL)` guard used by the `create` tests
  (`:181-183`). The guard is **mandatory** here: with a valid config the run no longer dies
  at `validate_configs`, so without it the test would proceed to `VolumeManager` and
  `DeploymentManager` and create real volumes and containers on the host.
- [x] 1.3 Assert `SENTINEL in result.output` alongside the existing `len(teardowns) == 1` and
  `"already exists" not in result.output`. The sentinel is what proves the run got as far as
  deployment setup rather than dying early and passing vacuously — the exact failure this
  group exists to remove.
- [x] 1.4 Run it against **unmodified** `src/` and confirm it passes. It must: the teardown at
  `:803` still runs before anything can refuse. A failure here means the fixture is wrong, not
  the code.
- [x] 1.5 Run the gate, then commit. Tests-only diff, so diff-cover reports no measurable
  lines and the 80% floor does not apply.

## 2. Move the teardown below everything that can refuse, and prove it

Red steps and the fix ship together in this one commit.

- [ ] 2.1 Add `test_force_evaluate_with_missing_secret_keeps_existing_runtime`: the group-1
  `benchmark_config`, an env file omitting `PG_PASSWORD` (postgres is always in
  `enabled_services` for evaluate), `DeploymentManager.delete_deployment` patched to record
  **and** `shutil.rmtree` the directory, a marker file inside the existing runtime. Assert
  exit non-zero, `teardowns == []`, and the marker survives.
- [ ] 2.2 Add `test_force_evaluate_with_invalid_config_keeps_existing_runtime`, same shape but
  passing `EXAMPLE_CONFIG` (`basic-openai`, no `services.benchmarking`) so `validate_configs`
  is the refusing step. This distinguishes an ordering fix from a secrets special-case: a
  teardown moved below only `validate_secrets` passes 2.1 and fails this.
- [ ] 2.3 Add `test_evaluate_without_force_refuses_existing_runtime`: no `--force`, existing
  runtime, valid config. Assert exit non-zero, `teardowns == []`, and that the output names
  the runtime as already existing. This guards the precedence that
  `handle_existing_deployment()` staying put is meant to preserve.
- [ ] 2.4 Run 2.1-2.3 and record each failure reason. Expect 2.1 and 2.2 red because the
  teardown ran, and 2.3 green as a pre-existing guard. Paste the assertion text into the PR
  body — "the tests failed" is not evidence they failed for the right reason.
- [ ] 2.5 Move `remove_existing_deployment(...)` from `cli_main.py:803-805` to sit directly
  **after** `ServiceBuilder.build_compose_config(...)` (`:849-857`) and directly **before**
  `template_manager = TemplateManager(env, verbosity)` (`:859`). Above `TemplateManager` and
  not below it, because the tests patch that constructor to stop before host mutation; a
  teardown below it never runs under the guard. `create()` resolves the same tension the same
  way (its teardown at `:261` sits above its own `TemplateManager` construction).
- [ ] 2.6 Move the `if base_dir.exists(): raise "Benchmarking runtime '{name}' already
  exists"` block (`:807-810`) down with the teardown, keeping it immediately below. Keep the
  message byte-identical. Do **not** make it conditional on `not force` — under `not force` it
  is unreachable and under `force` it is the only check that catches a cleanup
  `remove_existing_deployment()` downgraded to a warning, so gating it on `not force` removes
  it exactly where it does work.
- [ ] 2.7 Replace the stale `# Both halves, back to back, ...` comment at `:799-801`, which
  will no longer be true. State the invariant and why: nothing destructive until the
  replacement is known to be valid and constructible, `handle_existing_deployment` stays early
  for error precedence, and the `exists()` assertion travels with the teardown. Reference
  fasrc/archi#290.
- [ ] 2.8 Leave `handle_existing_deployment(...)` at `:802`, `src/cli/utils/helpers.py`
  untouched, and `create()` untouched. The helper split from #287 is reused, not revised.
- [ ] 2.9 Re-run 2.1-2.3 and group 1's test. All four green, and group 1's must still show the
  sentinel — that is what proves the relocated teardown is still reached on the success path.
- [ ] 2.10 Run the gate, then commit.

## 3. Verify nothing else moved

- [ ] 3.1 `pytest tests/unit/test_cli_create_dev_smoke.py` — every pre-existing test other
  than `test_force_evaluate_still_removes_existing_runtime` passes **unmodified**. If any
  `create` test needed touching, the change leaked out of `evaluate()`; revert and re-scope.
- [ ] 3.2 Run the benchmarking and CLI unit tests, since `evaluate()` was edited. Green in the
  full suite.
- [ ] 3.3 `grep -rn "remove_existing_deployment\|handle_existing_deployment" src/ tests/` and
  confirm the caller inventory is unchanged apart from the relocation — derived by grep, not
  from memory. This is the check that caught #287's first draft being wrong about `evaluate()`.
- [ ] 3.4 Confirm the port-availability probe still sits **below** the teardown: it runs inside
  `TemplateManager.prepare_deployment_files()` (`templates_manager.py:727-729`), reached from
  `cli_main.py:867`, which is after the relocated call. It must stay there — the old runtime
  holds its ports until removal, so hoisting it would make every forced evaluate fail
  (fasrc/archi#293).
- [ ] 3.5 Gate green with patch coverage at or above 80%. Compute the patch coverage yourself
  before trusting a red verdict: stale `origin/dev...HEAD` line numbers scored against a dirty
  working tree produce a false red.
- [ ] 3.6 No `Co-Authored-By` or session trailer on any commit.
- [ ] 3.7 **Not run, recorded as a gap:** a real forced evaluate through to a live
  benchmarking runtime. That needs images, containers and roughly fifty minutes of ingest, and
  the project forbids a non-dry run against a real runtime. The teardown code is unchanged —
  only relocated — and group 1's test covers the success path up to the first host mutation.
  State this in the PR body rather than implying end-to-end coverage.

## 4. Open the PR

- [ ] 4.1 Push with `git push -u origin fix/issue-290-evaluate-validate-before-teardown`. The
  branch was created with `checkout -b ... origin/dev`, so its upstream is the trunk until
  `-u` corrects it.
- [ ] 4.2 `gh pr create --repo fasrc/archi --base dev`. Put `closes #290` in the **body** — a
  closing keyword in the title does not link the issue. Include the group-2.4 red-test output,
  the seam decision from `design.md` and why `TemplateManager` bounds it, and the 3.7 gap.
- [ ] 4.3 Verify the link landed via the GraphQL closing-issues API rather than assuming it.
- [ ] 4.4 Request review and work the findings. Never merge.
