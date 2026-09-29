Work order: issue #260 (milestone v2026.11.0, tier `sonnet`). Branch
`fix/issue-260-catalog-document-char-clamp`, cut from `origin/dev` `ec878b07`. Read
`proposal.md` and `design.md` in this directory first. Every task ends with a green gate
and a commit — no task ends red.

## 1. The helper: red, green, gate, commit

- [x] 1.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Create `tests/unit/test_catalog_document_limits.py`. Import
      `from src.interfaces.uploader_app.document_limits import (MAX_CATALOG_DOCUMENT_CHARS,
      DEFAULT_CATALOG_DOCUMENT_CHARS, clamp_document_chars)`. Use a document
      `doc = "".join(chr(ord("a") + i % 26) for i in range(10000))` and apply the limit as the
      endpoint will: `doc[:clamp_document_chars(value)]`. Assert:
      (a) `"999999"` and `999999` → `len(...) <= 6000` and `== MAX_CATALOG_DOCUMENT_CHARS`;
      (b) `"0"` and `0` → `len(...) <= 6000` (not 10000);
      (c) `"-5"` and `-5` → `len(...) <= 6000`, and the result equals `doc[:len(result)]`
          (a prefix; never `doc[:-5]`);
      (d) `"100"` and `100` → the result equals `doc[:100]` exactly;
      (e) `None` → the result equals `doc[:4000]` exactly, and
          `DEFAULT_CATALOG_DOCUMENT_CHARS == 4000`;
      (f) `"abc"`, `""`, and `True` → `len(...) <= 6000`;
      (g) `MAX_CATALOG_DOCUMENT_CHARS == 6000`.
      Run `python -m pytest tests/unit/test_catalog_document_limits.py -q --no-header` and
      confirm it fails with `ModuleNotFoundError` for `document_limits` (not a fixture or
      syntax error).

      **Then green.** Create `src/interfaces/uploader_app/document_limits.py` as in design D1:
      a module docstring naming issue #260 and why `0` must not mean "no limit", the two
      constants, and `clamp_document_chars(requested: Any) -> int` that returns
      `DEFAULT_CATALOG_DOCUMENT_CHARS` for `None` and otherwise
      `resolve_requested_chars(requested, MAX_CATALOG_DOCUMENT_CHARS)` imported from
      `src.archi.pipelines.agents.tools.result_limits`. Do not change `result_limits.py`.

      Then: `black` and `isort` the two new files, `git add` them, `bash scripts/gate.sh`
      exits 0, commit `fix(#260): add a tested server-side clamp for catalog document max_chars`.

## 2. Wire the endpoint, guard the call site, document the ceiling

- [ ] 2.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Append to `tests/unit/test_catalog_document_limits.py` (after the last
      existing test, and check that the last test keeps all of its assertions) a test that
      reads `src/interfaces/uploader_app/app.py` as text, cuts out the body of
      `def api_catalog_document` (from that line to the next `\n    def `), and asserts:
      the body contains `clamp_document_chars(request.args.get("max_chars"))`; the body does
      not contain `if max_chars and`; the body does not contain `type=int`. Also assert that
      `app.py` contains `from src.interfaces.uploader_app.document_limits import clamp_document_chars`.
      Confirm this new test fails on its assertions.

      **Then green.** In `src/interfaces/uploader_app/app.py`, add the import beside the
      other `src.interfaces` import (isort order). In `api_catalog_document`, replace line
      762 with `limit = clamp_document_chars(request.args.get("max_chars"))`, and replace the
      two-line `if max_chars and len(text) > max_chars:` guard with `text = text[:limit]`.
      Change nothing else in `app.py`. Then `black --check src/interfaces/uploader_app/app.py`
      must pass, and `git diff origin/dev -- src/interfaces/uploader_app/app.py` must show
      at most 5 changed lines.

      In `docs/docs/agents_tools.md`, in the `### fetch_catalog_document` entry, after the
      sentence `Supports truncation with \`max_chars\`.`, add one sentence: the server caps
      `max_chars` at 6000 characters, a request with no `max_chars` returns at most 4000,
      and `0`, a negative, or a malformed value means the 6000 cap, not "no limit". Add no
      heading and no anchor.

      Then run `python -m pytest tests/unit/test_catalog_document_limits.py -q --no-header`
      (all pass), confirm
      `git diff origin/dev -- tests/unit | grep -c '^-.*def test_'` prints `0`, format,
      `git add`, `bash scripts/gate.sh` exits 0, commit
      `fix(#260): clamp api_catalog_document max_chars on the server`.

## 3. Publish

- [ ] 3.1 Push the branch and open the PR. The branch was cut with `checkout -b`, so its
      upstream is `origin/dev` — push with
      `git push -u origin fix/issue-260-catalog-document-char-clamp` to repoint it. Confirm
      the push landed on **fasrc/archi**, not a fork:
      `git ls-remote --heads origin fix/issue-260-catalog-document-char-clamp` must print the
      same SHA as `git rev-parse HEAD`. If it prints nothing, the push went elsewhere — stop
      and write the halt reason to `STATUS.md`.
      Write the PR body to `/tmp/pr-body-260.md` — **never** under `docs/` — and include the
      literal line `Closes #260`, the measured red (before: `max_chars=0` and `-5` were
      unbounded or tail-trimmed), the test count of `test_catalog_document_limits.py`, and
      the contract in four lines (ceiling 6000; absent → 4000; `0`/negative/malformed →
      6000; smaller valid value honoured). If no PR exists for the branch yet
      (`gh pr list --repo fasrc/archi --head fix/issue-260-catalog-document-char-clamp` is
      empty), run
      `gh pr create --repo fasrc/archi --base dev --title "fix(#260): clamp api_catalog_document max_chars on the server" --body-file /tmp/pr-body-260.md`.
      If the review gate already opened one, `gh pr edit <pr> --repo fasrc/archi --body-file /tmp/pr-body-260.md`
      instead. A `Closes #260` in the title does not link the issue; it must be in the body.
      Verify the link: `gh pr view <pr> --repo fasrc/archi --json closingIssuesReferences`
      must list 260. If it does not, edit the body and re-verify.
      Then STOP. Do not merge this PR. A human merges, in daylight.

## Commands

```bash
# the module's own suite
python -m pytest tests/unit/test_catalog_document_limits.py -q --no-header

# the app.py diff stays thin (at most 5 changed lines)
git diff --stat origin/dev -- src/interfaces/uploader_app/app.py

# no test was deleted — must print 0
git diff origin/dev -- tests/unit | grep -c '^-.*def test_'

# the gate, before every commit
bash scripts/gate.sh
```
