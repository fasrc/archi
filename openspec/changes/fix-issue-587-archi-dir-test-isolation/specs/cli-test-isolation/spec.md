## ADDED Requirements

### Requirement: CLI unit tests SHALL isolate the module-level ARCHI_DIR
A unit test that invokes a `cli_main` command with a temporary `ARCHI_DIR` MUST patch `cli_main.ARCHI_DIR` to the same path as the env var, so that the test result does not depend on which test imported `src.cli.cli_main` first.

#### Scenario: A smoke test runs after another test imported cli_main
- **WHEN** `tests/unit/test_render_preflight.py`, `tests/unit/test_render_preflight_review.py`, and `tests/unit/test_cli_create_dev_smoke.py` run together in that order
- **THEN** pytest exits 0 with no `Deployment 'smoke' already exists` failure

#### Scenario: No bare ARCHI_DIR env var in the smoke tests
- **WHEN** `grep -n 'setenv("ARCHI_DIR"' tests/unit/test_cli_create_dev_smoke.py` runs
- **THEN** each match is inside the `archi_home` fixture or in a test that also calls `monkeypatch.setattr(cli_main, "ARCHI_DIR", ...)`
