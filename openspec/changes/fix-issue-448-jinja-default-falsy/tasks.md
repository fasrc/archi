# Tasks — honor a configured falsy value when `base-config.yaml` is rendered

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail, make the smallest change that turns it green, run
`bash scripts/gate.sh`, commit. Never end a task with the suite red and never use
`--no-verify`.

Standing notes for every task:

- **Line numbers** are as of `origin/dev` at `c6112167`. They shift as you edit the
  template. Re-derive them before quoting any of them in the PR body.
- **Coverage.** `scripts/gate.sh` runs `--cov=src`, which measures Python only, so the
  template contributes no lines to `diff-cover`. Patch coverage is decided by the new test
  file — keep it under `tests/unit/`. Black and isort **do** enforce `tests/`.
- **Flaky test.** `tests/unit/evaluation/qa/test_jobs_history.py::test_job_manager_
  terminates_running_evaluation_process` fails intermittently with a `KeyError`
  (fasrc/archi#435). If that test alone fails, re-run the gate. Do not "fix" it here and
  do not bypass the gate.
- **Unique test names.** The gate runs no linter, so a repeated `def test_...` in one file
  silently replaces the earlier one. Grep the file for the name before you add it.
- **Appending to a test file.** Insert new tests at the end of the file and then read the
  diff's trailing context — an insert placed above the file's last line silently steals an
  assertion from the previous test.
- **Do not touch** `deploy/**`, `config/**`, `.github/workflows/**`, `scripts/gate.sh`,
  `hooks/**`, `ralph.conf`, `PROMPT.md`, `Makefile`, or `Containerfile`.
- **No `Co-Authored-By`** or session trailers on commits in this repository.

## The 28 conversion sites

Every row moves from the `default` filter to the `{%- set %}` + ternary form. Rows marked
**bug** hide an explicit `false` today; rows marked **null** already honor `false` but
render an explicit `null` as the truthy string `'None'`.

| line | key | input path | default | class |
|---|---|---|---|---|
| `:43` | `enabled` | `services.benchmarking.anchors.enabled` | `true` | null |
| `:122` | `flask_debug_mode` | `services.chat_app.flask_debug_mode` | `true` | null |
| `:163` | `enabled` | `services.data_manager.auth.enabled` | `false` | null |
| `:164` | `enabled` | `services.data_manager.enabled` | `true` | bug |
| `:188` | `flask_debug_mode` | `services.grader_app.flask_debug_mode` | `true` | null |
| `:214` | `normalize_embeddings` | `data_manager.embedding_class_map.HuggingFaceEmbeddings.kwargs.encode_kwargs.normalize_embeddings` | `true` | bug |
| `:259` | `reset_collection` | `data_manager.reset_collection` | `true` | bug |
| `:280` | `enabled` | `data_manager.retrievers.hierarchical_rerank.enabled` | `true` | null |
| `:290` | `enabled` | `data_manager.sources.local_files.enabled` | `true` | bug |
| `:291` | `visible` | `data_manager.sources.local_files.visible` | `true` | bug |
| `:301` | `enabled` | `data_manager.sources.links.enabled` | `true` | bug |
| `:302` | `visible` | `data_manager.sources.links.visible` | `true` | bug |
| `:310` | `reset_data` | `data_manager.sources.links.html_scraper.reset_data` | `true` | bug |
| `:343` | `headless` | `data_manager.sources.links.selenium_scraper.selenium_class_map.CERNSSOScraper.kwargs.headless` | `true` | bug |
| `:345` | `enabled` | `data_manager.sources.git.enabled` | `true` | bug |
| `:346` | `visible` | `data_manager.sources.git.visible` | `true` | bug |
| `:352` | `enabled` | `data_manager.sources.sso.enabled` | `true` | bug |
| `:353` | `visible` | `data_manager.sources.sso.visible` | `true` | bug |
| `:357` | `visible` | `data_manager.sources.indico.visible` | `true` | bug |
| `:360` | `use_sso` | `data_manager.sources.indico.use_sso` | `true` | null |
| `:375` | `headless` | `data_manager.sources.indico.sso_kwargs.headless` | `true` | bug |
| `:378` | `enabled` | `data_manager.sources.indico.slide_conversion.enabled` | `true` | null |
| `:390` | `enabled` | `data_manager.sources.jira.enabled` | `true` | bug |
| `:392` | `visible` | `data_manager.sources.jira.visible` | `true` | bug |
| `:402` | `enabled` | `data_manager.sources.redmine.enabled` | `true` | bug |
| `:410` | `anonymize_data` | `data_manager.sources.redmine.anonymize_data` | `true` | bug |
| `:416` | `verify_ssl` | `data_manager.sources.elog.verify_ssl` | `true` | bug |
| `:426` | `enabled` | `data_manager.processing.html_to_markdown.enabled` | `true` | bug |

Two traps in that table:

- **`:43` sits inside a `{%- if %}` block** that ends at `:45`. Render the template and
  read the output before asserting — if the key is absent from the rendered YAML, satisfy
  the enclosing condition in the test input rather than deleting the case.
- **`:214` and `:343` have paths near 100 characters.** That is why the form is `{%- set
  v = <path> %}` on its own line followed by the ternary, exactly as `:253-258` and `:331`
  already do it. Give each `set` a distinct variable name.

## 1. The three keys the issue proves

- [x] 1.1 `model: opus` — Create `tests/unit/test_base_config_falsy_defaults_render.py`.
      Copy the render harness shape from `tests/unit/test_base_config_sitemap_render.py:15-24`
      (`PackageLoader("src.cli")`, `select_autoescape()`, `ChainableUndefined`,
      `yaml.safe_load`) — do not re-implement rendering. Add a helper that expands a dotted
      path such as `"data_manager.sources.git.enabled"` into the nested dict the template
      expects, and a helper that reads a dotted path out of the parsed result. RED tests,
      nine assertions over three keys — `data_manager.processing.html_to_markdown.enabled`
      (`:426`), `data_manager.sources.git.enabled` (`:345`), `services.data_manager.enabled`
      (`:164`) — for three inputs each: configured `false` renders `False`, the key absent
      renders `True`, the key explicitly `None` renders `True`. Assert identity
      (`is False` / `is True`), not truthiness, so a `'None'` string or a `0` cannot pass.
      Watch the `false` cases fail. Then convert those three template sites to
      `{%- set %}` + `{{ v if v is defined and v is not none else true }}`. Gate green;
      commit.

## 2. The remaining 18 sites that hide an explicit false

- [x] 2.1 `model: opus` — Rewrite the three cases from 1.1 as one `pytest.mark.parametrize`
      table driven by `(input_path, rendered_path, default)` and extend it to all 21 **bug**
      rows above. Keep the three-input-per-key structure. Watch the 18 unconverted keys
      fail on the `false` case. Convert all 18 template sites to the same ternary form.
      Handle `:43`'s enclosing `{%- if %}` trap if it bites here. Gate green; commit.

## 3. Null-safety for the seven sites that already honor false

- [ ] 3.1 `model: sonnet` — Add the 7 **null** rows to the same parametrized table. The RED
      case is the explicit-`None` input: today `:43 :122 :163 :188 :280 :360 :378` render
      the four characters `None`, which `yaml.safe_load` reads as the string `'None'`, so
      an identity assertion against the row's default fails. Note that `:163` defaults to
      `false`, so its expected value is `False` for both the absent and the null case.
      Watch them fail. Convert all seven. Delete the now-wrong note at `:278-279` that
      recommends bare `default(true)` — task 6.1 replaces it with the file-level rule.
      Gate green; commit.

## 4. The two numeric bounds where zero is meaningful

- [ ] 4.1 `model: sonnet` — RED test, four assertions:
      `data_manager.sources.links.base_source_depth: 0` renders the integer `0` and unset
      renders `1` (`:299`); `data_manager.sources.links.sitemap.max_pages: 0` renders `0`
      and unset renders `20000` (`:332`). Assert `== 0` **and** `is not False`, because
      `yaml.safe_load` reads `False` and `0` as different types and a wrong fix produces
      the other one. Watch the two zero cases fail. Convert both sites to the ternary —
      `:331` directly above `:332` is already written that way; match it. Leave every other
      numeric default alone. Gate green; commit.

## 5. The guard that stops the pattern coming back

- [ ] 5.1 `model: opus` — Add a guard test to the same file that parses the template with
      `jinja2.Environment().parse(source)` and walks `nodes.Filter` nodes named `default`
      (the AST, not a regex: the template's own text is inconsistent — `:334` reads
      `default(false, True)` with a capital second argument — and a comment mentioning the
      pattern must not fail the guard). Two assertions:
      (a) **zero** `default` calls carry a boolean-literal default in any shape other than
      `default(false, true)`, with the failure message listing the offending line numbers
      and naming the ternary as the replacement;
      (b) the number of `default(<truthy non-boolean literal>, true)` calls equals the
      frozen baseline **79**, with a message telling the author to use the ternary for a
      new site or to lower the baseline in the same commit when a site is removed.
      Compute the baseline with the test's own walker and assert the literal 79 — if your
      walker reports a different number, find out why before changing the number.
      Prove the guard is not vacuous: temporarily rewrite one converted site back to
      `default(true, true)`, watch (a) fail, restore it. Gate green; commit.

## 6. State the rule once

- [ ] 6.1 `model: sonnet` — Add a comment block near the top of
      `src/cli/templates/base-config.yaml` stating the rule: a boolean flag, and any number
      whose `0` is meaningful, uses `{%- set v = <path> %}` plus
      `{{ v if v is defined and v is not none else <default> }}`; `default(<truthy>, true)`
      hides an explicit `false` and `default(<boolean>)` renders an explicit `null` as the
      string `'None'`; `default(false, true)` is the one permitted boolean use; the guard
      test in `tests/unit/test_base_config_falsy_defaults_render.py` enforces it. Leave the
      numeric note at `:255-257` where it is — it explains a local decision and still reads
      correctly. Gate green; commit.

## 7. Close out

- [ ] 7.1 `model: haiku` — Run `bash scripts/gate.sh` once more on the finished change and
      confirm it exits 0. Confirm `git status --porcelain` is empty. Push with
      `git push -u origin fix/issue-448-jinja-default-falsy` — the branch tracks
      `origin/dev`, so `-u` is required. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, and put `closes #448` in the **body**
      (a closing keyword in the title does not link the issue). In the body, record: the
      before/after render table from proposal.md, the correction to the issue's `None` row,
      the 28-convert / 22-keep split, and the reviewer question from design.md D7 (does any
      live deployment config carry one of these keys as `false` while expecting the feature
      on?). Then stop. **Do not merge.**
