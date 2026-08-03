Scope: ONE function (`resolve_persisted_path` in `src/utils/goldenset_maintenance.py:283`),
its docstring, and its tests in `tests/unit/test_goldenset_maintenance.py`. Do not widen it.
Closes fasrc/archi#183.

Every shell task starts with:
```bash
export PATH=/home/austin/miniforge3/envs/archi/bin:$PATH
```
Gate before every commit: `bash scripts/gate.sh` (never `--no-verify`). No `Co-Authored-By`
trailers. Verified already: both files are black/isort-clean, so the gate's writer mode will
not reflow them and collapse diff coverage.

## 1. Confirm the defect (red first)

- [x] 1.1 Reproduce the premise directly, so the failure mode is established before any test
      is written: `python -c "import os,pathlib,tempfile; d=tempfile.mkdtemp(); p=pathlib.Path(d,'loop'); os.symlink(p,p); print(p.resolve())"`.
      Expect `RuntimeError: Symlink loop from '...'`. Record the exact message — the next task
      asserts against this reason, not just "it fails".
- [x] 1.2 Add a failing test beside the existing containment tests
      (`tests/unit/test_goldenset_maintenance.py`, around lines 1207-1253): create a real temp
      data root, create a self-referential symlink inside it (`os.symlink(p, p)`), and assert
      `resolve_persisted_path("<link name>", str(root))` raises `ValueError` whose message
      names the offending `file_path`.
- [x] 1.3 Run `python -m pytest tests/unit/test_goldenset_maintenance.py -k <new test> -q` and
      confirm it fails **for the right reason** — an uncaught `RuntimeError: Symlink loop`, not
      an assertion mismatch, import error, or fixture problem. Paste the failure output into
      the commit message body. Do NOT proceed until the reason matches.
- [x] 1.4 Add a second failing test: `data_path` itself is a symlink loop, so the **root**
      cannot be resolved. Assert `ValueError` naming the data root. Confirm it also fails with
      `RuntimeError` first.

## 2. Make resolution total (green)

- [x] 2.1 Add a module-private helper next to `resolve_persisted_path` that resolves one path
      totally, taking a description used in the refusal message. It MUST:
      catch `RuntimeError` from `.resolve()` (Python 3.11/3.12 symlink-loop behavior); also
      treat a returned path that is still a symlink as unresolved (Python 3.13+, where
      `resolve()` returns the loop path instead of raising); and funnel **both** routes into a
      single `raise ValueError(...)` naming the path, so the type and message are identical on
      every interpreter. One raise site is deliberate — see design.md Decision 3.
- [x] 2.2 Route BOTH existing `.resolve()` calls through the helper: the data root (line 305)
      and the candidate (line 307). Pass each its own description so the root failure names the
      data root and the candidate failure names the row's `file_path`.
- [x] 2.3 Confirm no unguarded resolver remains:
      `grep -n "\.resolve()" src/utils/goldenset_maintenance.py` — every hit must be inside the
      helper, or carry a comment justifying why it cannot fail. This is a stated acceptance
      criterion.
- [x] 2.4 Run both new tests and confirm they pass.

## 3. Prove containment did not go blind

- [x] 3.1 Run the full containment set unmodified:
      `python -m pytest tests/unit/test_goldenset_maintenance.py -k "resolve or persisted or contain" -q`.
      The escape-the-root, `..`-traversal, absolute-path, symlink-out-of-root and sibling-root
      (`/srv/data-old` vs `/srv/data`) cases MUST pass **without being edited**. If any needed
      editing to pass, the fix made the guard blind — stop and rethink, do not adjust the test.
- [x] 3.2 Run the whole goldenset suite: `python -m pytest tests/unit/ -k goldenset -q`.
      Includes `tests/unit/test_goldenset_maintenance_script.py`, which asserts the caller's
      "outside the data root" message reaches stderr (line ~1332) — that path must be intact.
- [x] 3.3 Mutation check: revert ONLY the source fix (keep the tests), re-run the suite, and
      confirm **exactly** the tests from tasks 1.2 and 1.4 fail and nothing else. Then restore
      the fix. This proves the new tests actually pin the new behavior.

## 4. Document the reasoning in place

- [x] 4.1 Extend the `resolve_persisted_path` docstring — which already explains why both sides
      are resolved and why containment is "not optional politeness" — with why an unresolvable
      path is **refused by name** rather than resolved: `os.path.realpath` on a loop returns the
      loop path itself, which *is* inside the data root, so containment would pass and the guard
      would hand back a path whose target is unknown, failing later at `read_text()`. Keep it to
      a few lines in the existing voice; do not restate design.md.
- [x] 4.2 Comment the helper with which interpreter each of the two routes serves, so the
      `is_symlink()` branch is not later deleted as dead code on 3.11.

## 5. Gate and ship

- [x] 5.1 Run `bash scripts/gate.sh` bare (do not pipe or redirect it) and confirm it passes,
      including diff coverage ≥80% on the changed lines.
- [ ] 5.2 Commit on `fix/issue-183-symlink-loop-containment` with a short lowercase message,
      e.g. `fix(goldenset): refuse unresolvable persisted document paths`. No `Co-Authored-By`.
- [ ] 5.3 Push the branch and open the PR — this step is required, the change is not done
      without it:
      `gh pr create --repo fasrc/archi --base dev --title "fix(goldenset): refuse unresolvable persisted document paths (closes #183)"`.
      The body MUST contain `closes #183`, state which resolver strategy was chosen and why
      (design.md Decision 1), and note that the existing containment tests passed unmodified.
      Link the first use of any project term to the glossary.
- [ ] 5.4 Request review on the PR. Do NOT merge — a human merges in daylight.
