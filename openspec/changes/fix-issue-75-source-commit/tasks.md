## 1. Source-commit helper (TDD)

- [x] 1.1 Write a failing unit test `tests/unit/test_source_version.py` for
  `resolve_source_commit(repo_root)` covering: clean checkout returns the short SHA;
  dirty checkout returns `<sha>-dirty`; non-git path / git-unavailable returns `unknown`;
  the helper never raises. Mock/patch the git invocation (e.g. patch `subprocess.run`) so the
  test does not depend on the runner's own git state. Watch it fail.
- [x] 1.2 Implement `src/cli/managers/source_version.py` with `resolve_source_commit(repo_root=None)`:
  run `git rev-parse --short HEAD` and `git status --porcelain` with `cwd=repo_root`
  (default: the archi package root derived from `__file__`); return `<sha>` or `<sha>-dirty`,
  or `unknown` on any failure. Wrap all logic in a broad `except Exception` so it never raises.
  Keep the module black-clean. Run the test green.

## 2. Wire into deployment-artifact preparation

- [x] 2.1 In `src/cli/managers/templates_manager.py` `prepare_artifacts`, resolve the source
  commit and `logger.info` it next to the existing "Preparing deployment artifacts" line.
- [x] 2.2 Write the resolved value to a `SOURCE_COMMIT` file in `context.base_dir`, guarding the
  write so an IO error is logged but never propagates (best-effort, never fatal).

## 3. Gate and finalize

- [x] 3.1 Run `bash scripts/gate.sh`; confirm it exits 0 with ≥80% diff coverage on changed lines.
- [ ] 3.2 Confirm no changes under `deploy/fasrc-dev/**` and no new runtime dependencies; update
  `docs/` only if a user-facing deploy behavior is documented there.
