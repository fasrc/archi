## Why

The documented install is `pip install -e .`, but `pyproject.toml` declares no `flask`. The
lightest `src.utils` import reaches a module-scope `flask` import, so every
`from src.utils.<anything> import ...` fails with `ModuleNotFoundError` in that environment.
The flask pins live only in `requirements/requirements-base.txt:9-12` (issue #468).

The import chain: `src/utils/__init__.py:36` imports `postgres_service_factory` →
`src/utils/user_service.py:25` imports `src.utils.rbac.audit` →
`src/utils/rbac/__init__.py:19` imports `decorators`, which imports `flask` at module scope.
The only flask-adjacent entry in `pyproject.toml`,
`opentelemetry-instrumentation-flask==0.65b0`, declares `flask` only under its optional
`instruments` extra, which the pin does not request.

## What Changes

- Add `flask`, `flask-login`, `flask-session`, and `flask-cors` to `pyproject.toml`
  `[project].dependencies`, each pinned to the exact version in
  `requirements/requirements-base.txt`, with a comment block in the house style that names
  this issue and the import chain.
- Add a unit test that parses both files and fails if any of the four is absent from
  `pyproject.toml` or its version differs from the `requirements-base.txt` pin. The test reads
  the versions from the files; it does not hard-code them.

Decision (operator, 2026-09-26): option A, declare the pins. On-demand loading in
`src/utils/__init__.py` (option B) and a CLI-only install (option C) are out of scope.

## Capabilities

### New Capabilities
- `package-install-dependencies`: the editable install declares what the `src.utils` import
  chain needs, and the declared flask pins agree with `requirements-base.txt`.

### Modified Capabilities
<!-- none -->

## Impact

- `pyproject.toml` (four new pins and a comment block).
- New test `tests/unit/test_pyproject_flask_pins.py`.
- No `src/` change. Diff coverage reports no measurable lines, which is the expected result.
- Images run `pip install .` (for example `src/cli/templates/dockerfiles/Dockerfile-chat:41`)
  after the base requirements; the new pins equal the base pins, so the resolver gets no new
  constraint.
- Limit: `pyproject.toml` still declares no langchain, so the full service stack still needs
  `requirements-base.txt`. This change fixes the `src.utils` import chain only.
