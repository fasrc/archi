# Tasks — fix issue #178: ImportError during provider construction must warn, not fall back silently

Environment for every task that runs tests or the gate:

```bash
export PATH=/home/austin/miniforge3/envs/archi/bin:$PATH
```

Run the gate **bare** — `bash scripts/gate.sh`. Piping or redirecting it trips the harness
protected-path guard and looks like a failure when it is not.

## 1. Pre-flight — confirm the premise still holds

- [x] 1.1 Confirm the two clauses still disagree: `sed -n '1645,1650p' src/interfaces/chat_app/app.py`
      shows `except ImportError` → `return None` immediately followed by `except Exception` → `raise`.
      If the region has moved, re-locate it with
      `grep -n "Providers module not available" src/interfaces/chat_app/app.py` and use the real
      line numbers for the rest of these tasks.
- [x] 1.2 Confirm `_create_provider_llm` still has exactly one call site:
      `grep -n "_create_provider_llm(" src/interfaces/chat_app/app.py` returns the definition
      (`:1610`) and one call (`:2094`). If anything else now calls it, STOP and record in the PR
      body what began calling it and why removing the `None` return is still safe.
- [x] 1.3 Confirm no existing test executes the real method body: `grep -rn "_create_provider_llm" tests/`
      returns only substitution assignments and a docstring. These are seams for driving the
      caller, not coverage of the body.
- [x] 1.4 Invoke the `black-seam-scout` agent on `src/interfaces/chat_app/app.py` for the edit
      region around `:1645-1650` to determine whether an in-place edit trips the black-reflow
      churn trap, and follow its recommendation. `app.py` is large and a reflowed diff can sink
      diff coverage below the gate's 80% threshold.

## 2. Red tests — write these before touching `app.py`

- [x] 2.1 In `tests/unit/test_chat_override_persistence.py`, add a direct test of the real
      `_create_provider_llm` asserting that an `ImportError` **propagates** rather than returning
      `None`. Force the lazy import at `app.py:1623` to fail with
      `monkeypatch.setitem(sys.modules, "src.archi.providers", None)`, which makes
      `from src.archi.providers import get_provider` raise `ImportError`. Use `monkeypatch` (not a
      bare assignment) so `sys.modules` is restored at teardown. Build the wrapper with
      `object.__new__(ChatWrapper)` and set only the attributes the method touches (`self.config`).
- [x] 2.2 Run it and **watch it fail**: `python -m pytest tests/unit/test_chat_override_persistence.py -k importerror -v`.
      It must fail with "returned None" (or equivalent), not with a setup error — a setup error
      means the test is not reaching the clause. This is the test that proves the defect.
- [x] 2.3 Add the end-to-end test through `stream`, modelled on
      `test_override_generic_error_warns_and_falls_back_to_default`
      (`tests/unit/test_chat_override_persistence.py:341`) and reusing the `_make_stream_wrapper`
      (`:339`) and `_drive_stream` (`:366`) helpers: substitute a `_create_provider_llm` that
      raises `ImportError`, drive `stream`, and assert a
      `{"type": "warning", "message": "Using default model: …"}` event is emitted and the default
      pipeline answers. Note in the test's docstring that this one passes before the fix as well —
      it guards the observable contract, while 2.1 is the reproduction.

## 3. The fix

- [x] 3.1 Delete the `except ImportError as e:` clause at `app.py:1645-1647` so `ImportError`
      falls through to the existing `except Exception` handler, which logs with provider/model
      context and re-raises. Do not add a new branch. Do not touch the `except Exception` clause
      itself.
- [x] 3.2 Update the docstring's Returns section at `app.py:1616-1621` — it currently promises
      "A LangChain BaseChatModel instance, or None if creation fails", which is false once no
      failure path returns `None`. State that the method returns the constructed chat model and
      raises on failure.
- [x] 3.3 Run the tests from group 2 and confirm 2.1 now passes:
      `python -m pytest tests/unit/test_chat_override_persistence.py -v`.
- [x] 3.4 Confirm the untouched paths still behave: the `ValueError` → `{"type": "error",
      "status": 400}` early return (`app.py:2097-2103`) and the generic `except Exception` →
      warning path (`:2104-2109`) are unchanged and their existing tests still pass. The fix must
      not turn the `400` into a warning.
- [x] 3.5 Inspect `git diff src/interfaces/chat_app/app.py` and confirm it contains only the
      clause deletion and the docstring edit — no unrelated black reflow hunks.

## 4. Documentation

- [ ] 4.1 In `docs/docs/api_reference.md:210`, edit the override outcome table's "**nothing at
      all**: no `error`, no `warning`" row to remove the `ImportError` / falsey-construction half.
      Leave the "active pipeline with no `agent_llm` (`app.py:2111`)" half intact — it is a
      separate silent path and is out of scope for this change.
- [ ] 4.2 Remove the now-unreferenced `[ovrimport]` link definition at
      `docs/docs/api_reference.md:228` if 4.1 left no reference to it, and confirm `[ovrguard]`
      (`:227`) is still referenced.
- [ ] 4.3 Re-read the surrounding prose at `docs/docs/api_reference.md:198-220` and confirm no
      sentence still claims an `ImportError` produces no event. In particular check the "A silent
      fallback is a normal-looking success" paragraph below the table.

## 5. Gate and hand off

- [ ] 5.1 Run the full gate bare: `bash scripts/gate.sh`. It must pass — format, lint, tests, and
      **≥80% diff coverage on the changed lines**. Never `--no-verify`.
- [ ] 5.2 Confirm diff coverage specifically covers the changed lines of `app.py`; the deletion
      itself is not a coverage target, so the new direct test from 2.1 is what carries the changed
      region.
- [ ] 5.3 Commit with a short lowercase subject and no `Co-Authored-By` or AI-attribution
      trailers, then push the branch and open a PR into `fasrc/archi:dev` with `closes #178`.
      In the PR body, record the 1.2 finding (call-site count), note that operators with a broken
      provider install will now see `warning` events where responses previously looked clean, and
      list the acceptance criteria with their evidence. **Never merge** — a human merges.
