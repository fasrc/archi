## 1. Isolate ARCHI_DIR in the smoke tests

- [x] 1.1 Run `python -m pytest -q tests/unit/test_render_preflight.py tests/unit/test_render_preflight_review.py tests/unit/test_cli_create_dev_smoke.py` and record the red result (expected on `dev`: 2 failed, `Deployment 'smoke' already exists`). Then, in `tests/unit/test_cli_create_dev_smoke.py`, change `test_dev_flag_prints_warning_in_dry_run`, `test_dry_run_succeeds_without_docker`, and `test_no_dev_flag_no_warning` to take the `archi_home` fixture and delete their bare `monkeypatch.setenv("ARCHI_DIR", ...)` lines (design D1; keep the assertions). Run the same command again and confirm it exits 0. Confirm `grep -n 'setenv("ARCHI_DIR"' tests/unit/test_cli_create_dev_smoke.py` shows only the fixture line and the line in `test_dry_create_with_invalid_port_fails` (which already patches the constant). Run `bash scripts/gate.sh` green, then commit (the red run and the fix are one commit; do not commit a red state).

## 2. Publish

- [x] 2.1 Push the branch with `git push -u origin fix/issue-587-archi-dir-test-isolation`. Open the PR with `gh pr create --repo fasrc/archi --base dev`. Put `closes #587` in the PR body (not only the title), and record the repro counts before (2 failed) and after (0 failed). No `Co-Authored-By` trailer. Do not merge.
