## Why

`src/cli/cli_main.py:47` reads `ARCHI_DIR` once, when the module loads. Three `cli_main` tests in `tests/unit/test_cli_create_dev_smoke.py` set only the env var, so they use whatever value the first importer left. On `origin/dev` `6156b760` this run fails 2 tests (`Deployment 'smoke' already exists`):

```
python -m pytest -q tests/unit/test_render_preflight.py tests/unit/test_render_preflight_review.py tests/unit/test_cli_create_dev_smoke.py
# 2 failed, 69 passed
```

Issue #587 lists 4 tests. `test_dry_create_with_invalid_port_fails` already patches `cli_main.ARCHI_DIR` on `dev` (line 1695), so 3 remain:

- `test_dev_flag_prints_warning_in_dry_run` (setenv at line 163)
- `test_dry_run_succeeds_without_docker` (line 198)
- `test_no_dev_flag_no_warning` (line 424)

## What Changes

- The 3 tests use the existing `archi_home` fixture (lines 63-78), which sets the env var and patches `cli_main.ARCHI_DIR`. Their assertions do not change.
- Test code only. `src/cli/cli_main.py` does not change (issue constraint: a lazy lookup is a behavior change for its own issue).

## Capabilities

### New Capabilities

- `cli-test-isolation`: a `cli_main` unit test that sets `ARCHI_DIR` also patches the module constant, so test order does not change the result.

### Modified Capabilities

None.

## Impact

- `tests/unit/test_cli_create_dev_smoke.py` only.
- Out of scope: `tests/unit/test_benchmark_argilla.py` sets `ARCHI_DIR` with `patch.dict`, but it does not call `cli_main` (it calls the state-file helpers, which read the env var at call time). `tests/unit/test_cli_restart_source_flags.py:41` is inside its own `archi_home` fixture, which already patches the constant.
