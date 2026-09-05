# Design — the evaluate path establishes base-image safety before it destroys anything

## Context

`enforce_base_images` (`src/cli/managers/base_image_preflight.py:959`) is the single entry
point the CLI calls. Its signature is

```python
def enforce_base_images(
    compose_config, *, use_podman=False, dry=False, probe=None,
    template_dir=None, pyproject_path=None,
) -> List[Outcome]
```

and its body, in order: build a `ContainerProbe` when none is passed;
`_refuse_uncoverable_templates(template_dir)` (`:919`, whole template directory by design,
fasrc/archi#381); `required_base_image_names(compose_config.gpu_ids, grader_enabled)`;
`_refuse_divergent_base_references(names, template_dir)` (`:935`, scoped to the required
names); resolve each name to a reference; `run_preflight(...)`; raise
`BaseImagePreflightError` if anything refused.

`create()` (`cli_main.py:116`) calls it at `:282` and tears down at `:294`. `evaluate()`
(`cli_main.py:776`) builds its compose plan at `:890` and tears down at `:900`. The slot
between those two lines is the only place a refusal can still be free.

All anchors above were re-verified against this branch's tip `3170498c` on 2026-09-05, not
carried over from the issue body.

## Decisions

### D1 — Enforce over the whole declared service set, reusing `create`'s call shape

The operator settled this on 2026-09-04. The alternative was to scope the refusal to the
templates `evaluate` actually builds. Rejected for three reasons:

1. The existing two-image rule already tracks what `evaluate` builds.
   `base-compose.yaml:675` selects `Dockerfile-benchmarks-gpu` when `gpu_ids` is truthy, and
   `required_base_image_names()` returns the pytorch base under exactly that condition.
   `evaluate` accepts `--gpu-ids`, so the right images fall out with no narrowing.
2. Narrowing needs a template-subset parameter `enforce_base_images` does not have — a
   second call shape to keep in sync with `create` — and narrowing
   `_refuse_uncoverable_templates` would re-open the fail-open that #381 closed.
3. The only cost is a refusal caused by a template `evaluate` will not build. That refusal
   happens **before** the teardown, which is the whole point, and it is the breadth `create`
   already carries.

### D2 — Discard the return value, and pass `dry=False` literally

`create` binds `base_image_outcomes` only to feed `unverified_notes(...)` into
`print_dry_run_summary` inside its `if dry:` block (`cli_main.py:315`). `evaluate` has no
dry-run path and no dry-run summary, so binding the name would leave an unused variable that
the linters flag. `dry=False` matches `:900`, which already passes a literal `False` for
`dry` to `remove_existing_deployment`.

This is not an oversight to "fix" in review. Do not add reporting logic to `cli_main`;
`base_image_preflight.py`'s own docstring (design D8) puts decision logic in that module
precisely so `cli_main` stays a thin call site.

### D3 — The new call runs in the existing `evaluate` tests, and that is safe

This is the risk the issue body does not name, so it was measured rather than argued.

Five tests invoke `cli_main.evaluate` (`test_cli_create_dev_smoke.py:1091`, `:1152`,
`:1213`, `:1259`, `:1312`). **None of them patches the container probe.** The new call sits
above the teardown, so any test that reaches `:890` now constructs a real `ContainerProbe`.

Measured on 2026-09-05 with the change applied: all five pass, in 0.83s. The full unit suite
is green — 3897 passed, 2 skipped, 1 xfailed. Two of the five reach the new call; the other
three refuse earlier, at secret validation or config validation, and never get there.

Why the reaching tests stay green: `ContainerProbe` is constructed but `run_preflight`
treats an unavailable runtime as *unverified*, not *refused*, and only a refused outcome
raises. So a host with no container daemon does not turn these tests red.

**What would change this.** If a future edit makes an unavailable runtime a refusal, these
tests go red and the fix is to give each of them `_patch_probe(monkeypatch)`, not to move
the call below the teardown. Moving it back is the defect.

### D4 — Both new tests and the fix are one task

The gate (`bash scripts/gate.sh`) runs on every commit and the loop commits at the end of
every checkbox, so a task that ends with the suite red can never be committed and the loop
halts. Separately, the second test's red exists **only while the call is absent**: a task
that adds the fix first makes the second test unfailable, and the loop then spins trying to
manufacture a red that cannot exist.

So task 1.1 writes both tests, watches both fail, adds the one call, and ends green. That is
still test-first — the red is observed and recorded inside the task — it just is not left
behind at a commit boundary.

### D5 — The two reds fail on different assertions, and that is deliberate

`test_force_evaluate_with_unobtainable_base_image_keeps_existing_deployment` fails on
`teardowns == []`. That is the assertion the acceptance criteria require: an empty-list
assertion, not merely that an exception was raised.

`test_force_evaluate_with_an_uncoverable_service_template_keeps_existing_deployment` fails
first on `"Dockerfile-probe" in result.output`. On the parent commit the run *does* exit
non-zero — the teardown ran, `delete_deployment` was patched to record without deleting, and
the post-removal existence guard at `cli_main.py:902` raised "already exists". So
`exit_code != 0` passes for the wrong reason on the parent commit. The message assertion is
what makes this test a real red, and the `teardowns == []` and `record["pulled"] == []`
assertions that follow it are what keep it honest. Ordering the assertions this way is
intentional; do not reorder them.

## Measured red, 2026-09-05, parent commit `3170498c`

    tests/unit/test_cli_create_dev_smoke.py ..FF                             [100%]

    __ test_force_evaluate_with_unobtainable_base_image_keeps_existing_deployment __
    E   AssertionError: runtime was torn down before the refusal:
        [{'deployment_name': 'smoke', 'remove_images': False, 'remove_volumes': False,
          'remove_files': True}]

    _ test_force_evaluate_with_an_uncoverable_service_template_keeps_existing_deployment _
    E   AssertionError: the refusal must name the uncoverable template, or the operator
        cannot act on it. output:
          Starting ARCHI benchmarking process...
          [archi] Removing existing deployment at .../archi-home/archi-smoke
          Error: Failed due to the following exception: Benchmarking runtime 'smoke'
          already exists at .../archi-home/archi-smoke

    2 failed, 2 passed, 37 deselected in 0.60s

With the call inserted: `4 passed, 37 deselected in 0.57s`.

## Test fixtures the new tests need

All already exist in `tests/unit/test_cli_create_dev_smoke.py`:

- `:86` `benchmark_config(tmp_path)` — the evaluate path's valid config. Its `tmp_path` is
  the same fixture the uncoverable-template test uses for its template directory; a
  `dockerfiles/` subdirectory does not collide with it.
- `:436` `_existing_deployment(archi_home, name="smoke")` — writes `marker.txt`. The tests
  invoke `evaluate` with `-n smoke`, which is the fixture's default name.
- `:444` `_record_teardowns(monkeypatch)` — patches `DeploymentManager.delete_deployment` to
  record without deleting and returns the list.
- `:1817` `_patch_probe(monkeypatch, *, runtime=True, present=(), fetch_error=None, ...)` —
  pass `fetch_error=base_image_preflight.Cause.UNAUTHORIZED` to make a base unobtainable;
  returns a record whose `pulled` list proves no image work happened.
- `:1285` `test_force_evaluate_refuses_when_removal_silently_fails` — the working
  `CliRunner` shape for `evaluate`. It patches **both** `cli_main.check_docker_available`
  and `cli_main.preflight_benchmark_configs`. Both are required, or the run fails earlier for
  an unrelated reason and the test passes for the wrong cause.
- `:1855` and `:1897` — the two create-path ordering tests to mirror. They must keep passing
  unchanged.

## Out of scope

- No template-subset parameter on `enforce_base_images`; no narrowing of
  `_refuse_uncoverable_templates`. D1 rejected both.
- No refactor of `create()`; no shared preflight-then-teardown helper. Considered and
  declined: a larger diff across a working path.
- No `--dry` flag on `evaluate`.
- No edits to `deploy/**`, `config/**`, `.github/workflows/**`, `scripts/gate.sh`,
  `ralph.conf`, `PROMPT.md`, the `Makefile`, or the `Containerfile`.
- No decision logic in `cli_main.py` — the call site only.
