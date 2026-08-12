# Tasks — fix-issue-200-embedding-guard-unit-tests

Every task below ends with the suite **green** and is committed on its own. Do not split a task so
that a turn ends red — a red tree cannot pass the commit gate, and the loop would deadlock. Where a
task writes a test that must be *proved* to bind, the proof is a temporary mutation performed and
reverted **inside that same task**.

Run the gate bare before every commit — no pipe, no redirect:

```bash
bash scripts/gate.sh
```

Prefix shell work with `export PATH=/home/austin/miniforge3/envs/archi/bin:$PATH`.

## 1. Confirm the premise

- [x] 1.1 Confirm the gate collects nothing for the guard today:
  `python -m pytest tests/unit/ --collect-only -q | grep -ci embedding_guard` → `0`.
- [x] 1.2 Confirm the symbols to extract and their current lines:
  `grep -n '_NETWORK_ERROR_TYPES\|_HUB_HTTP_ERROR\|_NETWORK_ERRNOS\|_TRANSIENT_4XX_STATUSES\|def _is_transient_status\|_GUARDED_ERRORS\|def _is_network_failure\|def _response\|def _assert_propagates\|def _import_or_skip' tests/smoke/test_embedding_benchmarks.py`
  (expected on this base: `:66`, `:86`, `:123`, `:144`, `:147`, `:168`, `:171`, `:209`, `:217`,
  `:238`). If they have drifted, use what the grep says and note it in the pull-request body.
- [x] 1.3 Confirm `tests/support/` does not exist and `tests/` has no `__init__.py`, so the new
  package is additive: `ls tests/` and `ls tests/__init__.py` (expect "No such file").
- [x] 1.4 Confirm the one pure test that must gain a gated equivalent:
  `grep -n 'def test_no_named_network_type_drags_in_a_local_defect' tests/smoke/test_embedding_benchmarks.py`.

## 2. Extract the pure layer (refactor only — no behaviour change)

- [x] 2.1 Create `tests/support/__init__.py` (empty, or a one-line docstring).
- [x] 2.2 Create `tests/support/embedding_guard.py` and **move** — do not copy — from
  `tests/smoke/test_embedding_benchmarks.py`: the four conditional-import blocks and
  `_NETWORK_ERROR_TYPES`, `_HUB_HTTP_ERROR`, `_NETWORK_ERRNOS`, `_TRANSIENT_4XX_STATUSES`,
  `_is_transient_status`, `_GUARDED_ERRORS`, `_is_network_failure`, `_response`,
  `_assert_propagates`, `_import_or_skip`. Preserve every explanatory comment verbatim — those
  comments are the record of three review rounds and are the reason the allowlist is shaped as it
  is. Preserve each `except ImportError: pass` so the module imports in a minimal environment.
  Give the module a docstring saying it is a test-support seam shared by `tests/unit/` and
  `tests/smoke/`, and that the names stay private because they are not a public API.
- [x] 2.3 In `tests/smoke/test_embedding_benchmarks.py`, replace the moved definitions with
  `from tests.support.embedding_guard import (...)`. Keep `_load_model`, all four test classes and
  the module docstring where they are. Leave `test_no_named_network_type_drags_in_a_local_defect` in
  place — it now inspects the imported tuple (see design D4).
- [x] 2.4 Prove the refactor is inert. The whole smoke file must still collect, and the pure test
  must still pass, without the network:
  `python -m pytest tests/smoke/test_embedding_benchmarks.py --collect-only -q | tail -3` and
  `python -m pytest tests/smoke/test_embedding_benchmarks.py -k no_named_network_type -q`.
- [x] 2.5 Gate, then commit. Subject: `refactor(#200): extract the embedding network classifier`.

## 3. Gate the classifier: types, errnos, and the allowlist invariant

- [x] 3.1 Create `tests/unit/test_embedding_guard_classifier.py` importing from
  `tests.support.embedding_guard`. It must import **no** third-party library and **no**
  `langchain_huggingface`; build every exception it needs synthetically.
- [x] 3.2 Test: every type in `_NETWORK_ERROR_TYPES` is classified as a network failure. Iterate the
  tuple rather than retyping a list of names, so a family added later is covered automatically.
  Construct each instance defensively — some types need arguments; fall back to
  `type.__new__(cls)`-style construction or `pytest.skip` for a type that cannot be instantiated
  bare, and assert the tuple is non-empty so a tuple that silently emptied cannot pass vacuously.
- [x] 3.3 Test: every errno in `_NETWORK_ERRNOS` is classified as a network failure via
  `OSError(errno_value, "…")`. Iterate the frozenset; assert it is non-empty.
- [x] 3.4 Test: the allowlist invariant — for every type in `_NETWORK_ERROR_TYPES`, walk
  `__subclasses__()` recursively and assert no subclass is a client-side or definitive error. Mirror
  the assertion in `test_no_named_network_type_drags_in_a_local_defect`; read that test first and
  keep the same offending-pair message shape so a failure names the type and the subclass.
- [x] 3.5 Prove 3.2–3.4 bind: temporarily append a known over-broad base (for example
  `requests.exceptions.RequestException`, guarded by an import check) to `_NETWORK_ERROR_TYPES` in
  the support module, run `python -m pytest tests/unit/test_embedding_guard_classifier.py -q`,
  observe 3.4 fail and name the pair, then revert. Record the observed failure text in the
  pull-request body.
- [x] 3.6 Gate, then commit. Subject: `test(#200): gate the network classifier's allowlist`.

## 4. Gate the classifier: statuses, local defects, and the import helper

- [x] 4.1 Test: a definitive client error status is **not** an outage. Attach
  `types.SimpleNamespace(status_code=404)` (and `401`, `403`, `410`) as `.response` on a synthetic
  exception and assert `_is_network_failure` is `False` — no `requests` import (design D5).
- [x] 4.2 Test: transient and server-side statuses **are** outages — `408`, `425`, `429`, `500`,
  `503`, and a fronting-CDN code such as `520`/`524`. Assert `_is_transient_status` treats 5xx as a
  range by testing a code no list would contain.
- [x] 4.3 Test: a failure carrying a **success** status (an interrupted transfer) falls through to
  type classification rather than being read as an answer.
- [x] 4.4 Test: local defects propagate — an `AssertionError` and an `OSError(errno.ENOSPC)` /
  `PermissionError` are not network failures. Use `_assert_propagates` for the shape that would
  otherwise report SKIPPED, so a swallowed error is a red failure and not a green skip.
- [x] 4.5 Test: `_import_or_skip` returns the attribute for an installed module (use a stdlib module,
  e.g. `("json", "dumps")`); raises `pytest.skip.Exception` naming a genuinely absent module; and
  **propagates** a `ModuleNotFoundError` raised from *inside* an installed module rather than
  reporting it as "not installed". For the last one, install a temporary module in `sys.modules`
  (or a `tmp_path` on `sys.path`) whose import raises `ModuleNotFoundError` for a *different* name,
  and clean it up in a fixture.
- [ ] 4.6 Prove 4.1–4.5 bind: temporarily change `_is_network_failure` to `return True`, run the new
  unit module, observe the negative tests fail (not skip), and revert. Then temporarily make
  `_import_or_skip` swallow the broken-transitive case and observe 4.5 fail; revert.
- [ ] 4.7 Gate, then commit. Subject: `test(#200): gate the classifier's status and skip contracts`.

## 5. Verify the gate now sees it, and both documented commands still work

- [ ] 5.1 `python -m pytest tests/unit/ --collect-only -q | grep -ci embedding_guard` → **greater
  than 0** (this is the acceptance criterion from the issue's "Start here", inverted).
- [ ] 5.2 Confirm the gated suite imports no embedding library:
  `python -m pytest tests/unit/test_embedding_guard_classifier.py -q` passes, and
  `grep -rn 'langchain' tests/unit/test_embedding_guard_classifier.py tests/support/embedding_guard.py`
  returns nothing.
- [ ] 5.3 Confirm the smoke suite still degrades correctly with the library absent — run
  `python -m pytest tests/smoke/test_embedding_benchmarks.py -k TestTheGuardTests -q`, which spawns
  the subprocess that re-runs the file with `langchain_huggingface` blocked. This is the check that
  the extracted import resolves in the child process (design D3). If it cannot run in this
  environment, say so explicitly in the pull-request body rather than marking it done.
- [ ] 5.4 Gate, then commit any fixes 5.1–5.3 required. If nothing changed, no commit.

## 6. Mutation verification against the gate itself

- [ ] 6.1 Break `_is_network_failure` to `return True`, run `bash scripts/gate.sh`, and confirm the
  **gate** goes red (not just the module in isolation). Revert.
- [ ] 6.2 Drop one exception family from `_NETWORK_ERROR_TYPES` (delete one conditional block's
  `+=`), run the gate, confirm red. Revert.
- [ ] 6.3 Make `_import_or_skip` swallow a broken transitive import into a skip, run the gate,
  confirm red. Revert.
- [ ] 6.4 Confirm the tree is clean after all three reverts: `git status --porcelain` is empty apart
  from `tasks.md`. Record all three observed failures in the pull-request body — they are the
  deliverable evidence for acceptance criterion 1.
- [ ] 6.5 Gate, then commit the tasks.md updates.

## 7. Open the pull request — do not merge

- [ ] 7.1 Push the branch with an explicit refspec: `git push -u origin HEAD`. The branch was cut
  from `origin/dev` and therefore tracks the trunk; a bare `git push` is refused by
  `push.default=simple`, and must not be "fixed" by pushing to `dev`.
- [ ] 7.2 `gh pr create --repo fasrc/archi --base dev` with `Closes #200.` in the **body** (a
  closes-keyword in the title does not link the issue). Include: the before/after
  `--collect-only | grep -c` numbers, the three mutation results from task 6, the note that no file
  under `src/` changed, and the note that `testpaths`, the gate script and the workflows are
  untouched.
- [ ] 7.3 Stop. A human merges. Do not run `gh pr merge`, and do not enable auto-merge.
