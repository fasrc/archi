## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-400-strip-empty-headings` exists, cut from `origin/dev` at
      `26e6429e`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-400-strip-empty-headings --strict` passed on the host.
      The `openspec` CLI is **not usable in this container** — do not run it, and do not add a
      task that does. It is already green.
- [x] 0.3 The red is measured at `26e6429e` (table in `design.md`): `<h3></h3><p>x</p>` →
      `'### \n\nx'`; whitespace, `&nbsp;`, empty-span and `<br>`-only headings do the same.
- [x] 0.4 Every design decision is in `design.md` (D1–D5): the helper name, where it is
      called, the exact rule, decompose not unwrap, and why no digest fixture changes. You are
      not searching for an approach. Where the issue body and the design differ, the design
      wins.

## Rules that apply to EVERY task below

- Code work is ONLY in `src/data_manager/collectors/processing.py`. Tests ONLY in
  `tests/unit/test_html_to_markdown_processor.py`. Docs ONLY in `docs/docs/configuration.md`.
  Do not touch `src/utils/goldenset_maintenance.py`, `node_parsing.py`, any config template,
  or anything under `deploy/`, `config/`, `.github/`, `scripts/`, `hooks/`.
- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit, so
  a task that ends red can never be committed and the loop halts there.
- Before you add a test, `grep -n 'def test_' tests/unit/test_html_to_markdown_processor.py`
  and pick a name that is not already used. Prefix every new test with `test_400_`. The gate
  runs no linter, so a duplicate `def test_...` silently replaces the older test.
- Put the new tests in a new section. Insert the marker line
  `# --- issue #400: empty headings are dropped ---------------------------------`
  directly **above** the existing `# --- issue #410: _ArchiMarkdownConverter and markdownify wrapper (task 1.2) ---`
  marker, and add every new test between the two markers. Never append at the end of the file.
- After you insert a test, `git diff` the file and **read the trailing context line**. Confirm
  the test above yours still ends with its own `assert`.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/unit/test_html_to_markdown_processor.py | grep -c '^-.*def test_'`
  must print `0`.
- Run `black` and `isort` on every file you changed **before** `git add`. The pre-commit
  hook's black is a writer while CI's is an assert, so a commit can be pushed misformatted.
- Do not put `${VAR:-x}` or other shell expansions in a commit message. Use
  `git commit -F <file>` for any message with special characters.
- No `Co-Authored-By` trailers. Never `--no-verify`.

## 1. Drop empty headings (red and green together)

- [ ] 1.1 Add the tests, watch them fail, then implement, then commit — all in this one task.
      Tests (in the new `#400` section, each calls `html_to_markdown` and checks that no
      output line starts with `#` unless stated):
      - `test_400_empty_heading_dropped`: `"<h3></h3><p>x</p>"` → no `#` line, `x` present.
      - `test_400_whitespace_and_nbsp_heading_dropped`: `"<h2>   </h2><p>x</p>"` and
        `"<h2>&nbsp;</h2><p>x</p>"`.
      - `test_400_empty_span_and_br_heading_dropped`: `"<h1><span></span></h1><p>x</p>"` and
        `"<h4><br></h4><p>x</p>"`.
      - `test_400_heading_with_content_kept`: exact equality —
        `html_to_markdown("<h3>Title</h3>") == "### Title"`,
        `html_to_markdown("<h3><code>foo</code></h3>") == "### `foo`"`,
        `html_to_markdown('<h3><img src="a.png" alt="A"></h3>') == "### A"`.
      - `test_400_all_heading_levels`: each of `h1`..`h6` empty is dropped, each with text
        `T` is kept as `"#" * level + " T"`.
      - `test_400_processor_drops_empty_heading`: `HtmlToMarkdownProcessor().process(
        _html_resource(content="<h3></h3><p>x</p>"))` → `get_content()` equals
        `html_to_markdown` of the same HTML and has no `#` line (use the existing
        `_html_resource` helper the `test_410_processor_snippet_a` test uses).
      Run `python -m pytest tests/unit/test_html_to_markdown_processor.py -q -k test_400`;
      the drop tests must FAIL and the kept tests must PASS. Then implement per design D1/D2:
      add `_drop_empty_headings(soup: BeautifulSoup) -> None` near `_promote_block_code`,
      and call it in `_promote_block_code` right after the
      `soup = BeautifulSoup(html, "html.parser")` line. Add one docstring sentence to
      `_promote_block_code` naming the call and issue #400. Re-run the module: all green.
      Then `bash scripts/gate.sh` green, then commit `fix(#400): drop empty headings in html_to_markdown`.

## 2. Docs

- [ ] 2.1 In `docs/docs/configuration.md`, in the `html_to_markdown.enabled` table row
      (search for `| \`html_to_markdown.enabled\` |`), add one sentence at the end of the
      description cell: `Empty headings (no text and no image or code) are dropped, so no bare \`#\` line reaches the Markdown.`
      Keep it one table row on one line. Run `bash scripts/gate.sh` green, then commit
      `docs(#400): note that empty headings are dropped`.

## 3. Publish

- [ ] 3.1 Push the branch and open the PR. The branch was cut with `checkout -b`, so its
      upstream can be `origin/dev` — push with
      `git push -u origin fix/issue-400-strip-empty-headings`. Confirm the push landed on
      **fasrc/archi**, not a fork:
      `git ls-remote https://github.com/fasrc/archi refs/heads/fix/issue-400-strip-empty-headings`
      must print the same SHA as `git rev-parse HEAD`. If it prints nothing, the push went
      elsewhere — write the reason to `STATUS.md` and halt. Then write the PR body to `/tmp/pr-body-400.md` (NOT under
      `docs/`), with `Closes #400` on its own line in the body, and run
      `gh pr create --repo fasrc/archi --base dev --title "fix(#400): drop empty headings in html_to_markdown" --body-file /tmp/pr-body-400.md`.
      Do not merge.

## Commands

```bash
# the red probe (before task 1) and the green check (after)
python3 - <<'EOF'
import sys; sys.path.insert(0, ".")
from src.data_manager.collectors.processing import html_to_markdown as h
for s in ["<h3></h3><p>x</p>", "<h2>&nbsp;</h2><p>x</p>", "<h3>Title</h3>"]:
    print(repr(s), "->", repr(h(s)))
EOF

python -m pytest tests/unit/test_html_to_markdown_processor.py -q

# no test was deleted — must print 0
git diff origin/dev -- tests/unit/test_html_to_markdown_processor.py | grep -c '^-.*def test_'

# the gate, before every commit
bash scripts/gate.sh
```
