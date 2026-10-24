## Context

`pyproject.toml:15-87` holds `[project].dependencies`. Several entries already mirror a pin
from `requirements/requirements-base.txt`, and each carries a comment block that says why the
pin is there and that the version matches `requirements-base.txt` (for example the
`add-slack-service` block above `slack_sdk==3.45.0`). `requirements-base.txt:9-12` pins
`flask==3.0.3`, `flask-login==0.6.3`, `flask-session==0.8.0`, and `flask-cors==5.0.0`.

## Goals / Non-Goals

**Goals:**
- `pip install -e .` installs flask and the three flask extensions at the base pins.
- A test fails if a pin goes missing from `pyproject.toml` or drifts from the base file.

**Non-Goals:**
- On-demand loading of `src/utils/__init__.py` names (option B).
- Declaring langchain or any other runtime dependency.
- Any edit to `requirements/requirements-base.txt`.

## Decisions

1. **Pin with `==`, the same version as the base file.** Every pin in this list that
   mirrors the base file is exact. An exact match means the base install and the editable
   install can never ask the resolver for two different versions.
2. **The test reads both files.** It parses `pyproject.toml` with `tomllib` (pattern:
   `tests/unit/test_python_version_declaration.py`) and reads the base file line by line. It
   compares names after PEP 503 normalization (`packaging.utils.canonicalize_name`), so
   `flask_login` and `Flask-Login` match `flask-login`. It parses each entry with
   `packaging.requirements.Requirement`, and asserts the specifier is one `==` clause whose
   version equals the base pin. Hard-coded numbers are not permitted: a later bump of the
   base file must fail the test until `pyproject.toml` follows.
3. **The test fails closed.** If a name is absent from the base file, or the base file pins
   it more than once, the test fails with a message that names the file and the package. It
   does not skip.
4. **Placement.** The new block goes in the `dependencies` list, with a comment that names
   issue #468 and the import chain.

## Risks / Trade-offs

- `requires-python` is not touched, so black's target version does not change and the repo
  does not reformat.
- A future flask bump must now edit two files. The new test makes the second edit
  mandatory, which is the purpose.
