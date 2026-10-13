# Tasks — delete unreferenced parent nodes on chat-app source removal (#600)

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail, write the smallest fix, run the gate, commit — in ONE task. Never end
a task with the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** Files this change may edit: `src/data_manager/vectorstore/parent_nodes.py`,
  `src/interfaces/chat_app/app.py` (the two call sites and one import of design D3 only),
  `tests/unit/test_parent_nodes.py`. Do NOT edit `manager.py`, `postgres_vectorstore.py`,
  `init.sql`, `deploy/**`, `.github/**`, or any control-plane file.
- **Read `design.md` first.** It fixes the helper API (D1), the call sites (D3), and the
  tests (D4).
- **Fast loop:** `python -m pytest tests/unit/test_parent_nodes.py -q`. Run all of
  `tests/unit/` before the gate.
- **Format before you stage.** Run black and isort, then `git add`, then commit, and
  confirm `git status` is empty.
- **Append tests at the end of the file** after the last complete test function; never
  split an existing test. Never reuse an existing test name.

## 1. Helper and call sites

- [x] 1.1 Append the design D4 tests (1–5) to `tests/unit/test_parent_nodes.py`. Run the fast loop and record the red result (expected: `AttributeError` for `delete_unreferenced_parents_for_resources`). Add the design D1 helper to `src/data_manager/vectorstore/parent_nodes.py`. Run the fast loop green. Then add the design D3 import and the two call sites in `src/interfaces/chat_app/app.py` (git removal after the `chunks_deleted` log near line 6191, Jira removal after the `chunks_deleted` log near line 6825); confirm with `git diff --stat` that `app.py` gains no more than 10 lines. Run `python -c "import ast; ast.parse(open('src/interfaces/chat_app/app.py').read())"` to confirm the file still parses. Run `bash scripts/gate.sh` green, then commit (the red run and the fix are one commit).

## 2. Publish

- [x] 2.1 Push the branch to `origin` with upstream tracking set to `fix/issue-600-prune-parents-on-source-removal` (not the trunk). Open the PR with `gh pr create --repo fasrc/archi --base dev --title "fix: delete unreferenced parent nodes on chat-app source removal (#600)"`. Put `closes #600` in the PR body (not only the title), list the two `app.py` call sites, and note that issue plan step 3 is out of scope (no `PostgresVectorStore.delete()` caller deletes by resource). No `Co-Authored-By` trailer. Do not merge.
