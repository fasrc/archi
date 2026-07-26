## 1. Locate the docstring

- [x] 1.1 Read `src/interfaces/chat_app/app.py:4620-4634` — the `get_chat_response`
  docstring — and confirm the `last_message` description (around `:4626-4628`) still says
  a flat "list of length 2" (`["User", "hello"]`).
- [x] 1.2 Confirm the real contract at `app.py:1633` (`sender, content = tuple(message[0])`)
  and the two in-repo clients (`static/chat.js:266`, `openai_compat.py:242`) send the nested
  `[[sender, content]]` shape — this is the shape the docstring must describe.

## 2. Fix the docstring (doc-only)

- [x] 2.1 Rewrite the `last_message` description so it states `last_message` is a list
  containing a single `[sender, message]` pair, gives the concrete example
  `[["User", "How do I submit a job?"]]`, and notes that only the first pair is read.
  Keep the surrounding docstring style. Do NOT describe it as a flat "list of length 2".
- [x] 2.2 Make no other change: no executable-code edit, no payload validation, no
  400-on-malformed handling (that is a separate PR, out of scope here).

## 3. Verify against acceptance criteria

- [x] 3.1 `sed -n '4620,4634p' src/interfaces/chat_app/app.py` no longer describes
  `last_message` as a flat "list of length 2" and shows a concrete `[["User", ...]]` example.
- [x] 3.2 `git diff origin/dev -- src/interfaces/chat_app/app.py` touches only
  docstring/comment lines (no executable-code change).
- [x] 3.3 Run the gate in the full-deps env and confirm it exits 0:
  `source ~/miniforge3/etc/profile.d/conda.sh && conda activate archi && bash scripts/gate.sh`
  (a docstring-only edit adds no executable lines, so diff-cover has nothing to fail on).

## 4. Ship (post-implementation, handled by the loop)

- [x] 4.1 Commit on `fix/issue-138-chat-docstring-payload-shape` (short lowercase message,
  no `Co-Authored-By` trailer); the pre-commit gate must pass without `--no-verify`.
- [x] 4.2 Open the PR to `fasrc/archi:dev` (`gh pr create --repo fasrc/archi --base dev`),
  linking `closes #138`, then post an `@codex review` comment. Do NOT merge.
