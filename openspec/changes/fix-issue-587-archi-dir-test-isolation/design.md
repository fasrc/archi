## Context

`cli_main.ARCHI_DIR` is a module constant set at import time (`src/cli/cli_main.py:47`). `create()` uses it at `cli_main.py:186`. A test that calls `monkeypatch.setenv("ARCHI_DIR", ...)` after another test imported `cli_main` does not change the constant. The `archi_home` fixture in `tests/unit/test_cli_create_dev_smoke.py` already solves this: it sets the env var and patches `cli_main.ARCHI_DIR` to the same `tmp_path / "archi-home"`.

## Goals / Non-Goals

**Goals:** the 3 tests get their own `ARCHI_DIR`, independent of test order.

**Non-Goals:** no change to `src/cli/cli_main.py`; no change to the test assertions.

## Decisions

- **D1: use the `archi_home` fixture.** Add `archi_home` to each test's parameters and delete the bare `monkeypatch.setenv("ARCHI_DIR", ...)` line. This reuses one tested mechanism instead of three copies of `setattr`. If a test still needs `monkeypatch` for other patches, keep that parameter.
- **D2: red proof is the order-sensitive repro.** The proof that the change is necessary is the 3-file pytest command in the proposal: it fails 2 tests on `dev` and must pass after the change. The red run and the fix land in one task (one commit), because a task that ends red cannot pass the commit gate.

## Risks / Trade-offs

- A test-only diff has no `src/` lines, so diff-cover scores nothing. The repro command is the real check; record its before and after counts in the PR body.
