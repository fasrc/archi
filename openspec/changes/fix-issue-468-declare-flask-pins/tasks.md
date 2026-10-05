## 1. Declare the flask pins, driven by a test that fails first

- [ ] 1.1 Add `tests/unit/test_pyproject_flask_pins.py` and add the pins in the same task.
  **Red first, inside this task:** write the guard per `design.md` Decisions 2–3. Parse
  `pyproject.toml` with `tomllib` (copy the pattern in
  `tests/unit/test_python_version_declaration.py`), read
  `requirements/requirements-base.txt` line by line, normalize names with
  `packaging.utils.canonicalize_name`, parse each entry with
  `packaging.requirements.Requirement`, and for each of `flask`, `flask-login`,
  `flask-session`, `flask-cors` assert: present in `pyproject.toml`; the base file pins it
  with `==` exactly once; the `pyproject.toml` specifier is one `==` clause with the same
  version. Do not hard-code version numbers. Each failure message names the package (and
  both versions on a mismatch). Run
  `python -m pytest tests/unit/test_pyproject_flask_pins.py -q` and confirm it FAILS because
  the four packages are absent from `pyproject.toml`. Only then add the four pins to
  `pyproject.toml` `[project].dependencies` (`flask==3.0.3`, `flask-login==0.6.3`,
  `flask-session==0.8.0`, `flask-cors==5.0.0` — copy from `requirements-base.txt:9-12`) with
  a comment block in the house style (see the `add-slack-service` block): name issue #468,
  state the import chain (`src/utils/__init__.py` → `user_service` → `rbac.audit` →
  `rbac.decorators` imports `flask`), and say the versions match `requirements-base.txt`.
  Re-run the test to green. Do not split the red step into its own task: a task that ends
  red can never pass the gate.
- [ ] 1.2 In the same test file, add parametrized unit tests of the comparison helper that
  prove each failure path fires on synthetic input (missing package, version drift, a base
  file with zero or two pins for a name, a name spelled `Flask_Login`). Feed the helper
  strings or `tmp_path` files; never edit the real files. Use a new, unique name for each
  test function. End green.
- [ ] 1.3 Run `black` and `isort` on the new test file **before** `git add` — the pre-commit
  step reformats after staging. Confirm `grep -n '^ *"flask' pyproject.toml` shows the four
  pins. Run the gate bare — `bash scripts/gate.sh`, no pipe and no redirect — then commit.
  Confirm `git status` is clean after the commit.

## 2. Validate and open the PR

- [ ] 2.1 Run `openspec validate fix-issue-468-declare-flask-pins --strict` and confirm it
  passes.
- [ ] 2.2 Push with `git push -u origin fix/issue-468-declare-flask-pins`. The `-u` matters:
  the branch was created from `origin/dev`, so its upstream is the trunk.
- [ ] 2.3 Open the PR against `fasrc/archi:dev` with
  `gh pr create --repo fasrc/archi --base dev`. Put `Closes #468` in the **body** — a
  closing keyword in the title does not link the issue. Include the output of
  `grep -n '^ *"flask' pyproject.toml` and `sed -n 9,12p requirements/requirements-base.txt`,
  the red-then-green test run, and the note that no `src/` file changes, so diff coverage
  reports no measurable lines (expected, not a skipped gate). State the limit: langchain is
  still undeclared, so the full service stack still needs `requirements-base.txt`. Do not
  commit the PR body file to the repository. **Never merge** — a human merges.
