# Tasks — render the judge's own local mode into the RAGAS block

Rules that apply to **every** task below:

- Test first. Write the failing test, run it and watch it fail, then write the minimum
  code to pass. Never write implementation before a red test.
- Every task ends **green** and **commits**. Never leave the suite red at the end of a
  task — the gate runs on commit, so a task that ends red can never be committed and the
  loop deadlocks. That is why the red tests and the template change below are one task.
- Run `bash scripts/gate.sh` **bare** before every commit. It refuses to be piped or
  redirected. Never pass `--no-verify`.
- Format **before** `git add`, then confirm `git status` is empty after the commit. The
  pre-commit hook's black is a writer while CI's is an assert, so a staged-then-formatted
  file can be pushed misformatted and redden CI on an otherwise green branch.
- Give every new test a unique `def test_...` name. The gate runs no linter, so a reused
  name silently deletes the earlier test while staying green.
- When you add tests to an existing file, insert them **after the final line of the file**,
  not above it. Inserting above the last line moves that line into your new test and
  silently strips the previous test's last assertion.
- Commit messages: short, lowercase, `fix(#469): ...`. No `Co-Authored-By` trailer and no
  session trailer.
- **Expect a vacuous coverage number.** This change touches a Jinja template, tests, a
  spec and a docs page — no Python under `src/`. `diff-cover` will print **"no lines with
  coverage information"** and `--fail-under=80` will pass trivially. That is the correct
  result. Do not invent a `src/**.py` edit to manufacture a coverage percentage.

## 1. The render

- [ ] 1.1 Add the failing render tests and the template key, in one commit.

  Order of work:

  1. Append tests to the **end** of `tests/unit/test_base_config_benchmark_render.py`
     (122 lines today — append after the last line). Mirror the tests already in that file
     for `services.benchmarking.provider_mode`; they are the same assertions one level
     deeper in the config tree.

     Build the config through the file's existing `_render` helper (line 20), nesting the
     key where the consumer reads it:

     ```python
     _render({"benchmarking": {"provider": "local", "mode_settings": {
         "ragas_settings": {"evaluator_provider": "local",
                            "evaluator_provider_mode": <value>}}}})
     ```

     and read it back from
     `cfg["services"]["benchmarking"]["mode_settings"]["ragas_settings"]`.

     Cover, one test each unless noted:

     | configured value | expected rendered result |
     |---|---|
     | `"openai_compat"` | the string `"openai_compat"` |
     | `False` | the boolean `False`, key present |
     | `0` | the integer `0`, key present |
     | key absent entirely | key absent from the rendered config |
     | `None` | key absent from the rendered config |
     | `""` | the empty string, key present |
     | the 8 `YAML_SPECIAL_MODE_STRINGS` | each keeps its string type (parametrized) |

     Reuse the module-level `YAML_SPECIAL_MODE_STRINGS` list already defined at line 93 —
     do not redefine it.

     Then add the two tests that assert **through the consumer**, using the
     `resolve_local_mode` already imported at line 17:

     - a rendered judge mode of `False` raises `ValueError` from
       `resolve_local_mode("http://gpu-vllm:8000", value)`
     - a rendered judge mode of each `YAML_SPECIAL_MODE_STRINGS` entry raises `ValueError`
       from the same call (parametrized)

     These two are the load-bearing ones. A test that only asserts "the key is present"
     passes even when the render retypes the operator's string, which is the second defect
     `d37afb3a` fixed for the SUT key.

     Add one more test that an empty judge mode still inherits: render `""` and assert
     `resolve_local_mode("http://gpu-vllm:8000/v1", value) == "openai_compat"`, so the
     inherit path is pinned and the presence guard cannot regress it.

  2. **Watch them fail.** Run
     `pytest tests/unit/test_base_config_benchmark_render.py -x -q` and confirm the new
     tests fail because the key is missing from the rendered config — not because of a
     typo in the nesting. The pre-change failure mode is a `KeyError` or an
     `assert "evaluator_provider_mode" in ...` miss, never a `ValueError` from the
     consumer.

  3. Add the key to the RAGAS block of `src/cli/templates/base-config.yaml`, immediately
     after `evaluator_ollama_url` (line 100). Use the presence guard and `| tojson`
     exactly as the SUT key at lines 46-61 does:

     ```jinja
     {%- if services.benchmarking.mode_settings.ragas_settings.evaluator_provider_mode is defined and services.benchmarking.mode_settings.ragas_settings.evaluator_provider_mode is not none %}
     evaluator_provider_mode: {{ services.benchmarking.mode_settings.ragas_settings.evaluator_provider_mode | tojson }}
     {%- endif %}
     ```

     at the block's existing indentation (8 spaces, matching `evaluator_ollama_url`).

     Mind the whitespace control. `{%- if %}` and `{%- endif %}` strip the preceding
     newline; the SUT key at line 46 shows the working form. If the rendered YAML fails to
     parse, or the key lands at the wrong indent level and attaches to the wrong parent,
     that is a whitespace-control bug — compare against lines 46-61 rather than guessing.

     Add a short comment above the guard saying why this key is guarded and
     JSON-serialized while the three sibling `evaluator_*` keys above it use
     `| default("", true)`: a mode reaches a validator, so a configured `false`, `0` or
     `"null"` must arrive as written. Without that comment the next reader tidies it into
     the sibling style and silently restores the defect. Keep it to about three lines; the
     full reasoning lives in `design.md`.

     **Do not** change `evaluator_provider`, `evaluator_model` or `evaluator_ollama_url`,
     and **do not** change `src/bin/service_benchmark.py`. The consumer is already correct.

  4. Confirm the issue's own reproduce probe now prints `True`. Pipe it via stdin so you
     measure this branch and not an installed copy of the package:

     ```bash
     python - <<'PY'
     from tests.unit.test_base_config_benchmark_render import _render
     cfg = _render({"benchmarking": {"provider": "local", "mode_settings": {
         "ragas_settings": {"evaluator_provider": "local",
                            "evaluator_provider_mode": "openai_compat"}}}})
     r = cfg["services"]["benchmarking"]["mode_settings"]["ragas_settings"]
     print("rendered keys:", sorted(r))
     print("evaluator_provider_mode present?", "evaluator_provider_mode" in r)
     PY
     ```

     Keep the before and after output — the PR body needs both.

  5. Run the **whole** unit suite, not just the one file. The template is rendered by other
     tests, and a whitespace-control mistake breaks them rather than yours.

  6. Green, format, gate, commit.

## 2. Docs and spec

- [ ] 2.1 Document the key and validate the change, in one commit.

  1. In `docs/docs/benchmarking.md`, in the **Judge/SUT split** section (line 436), document
     `evaluator_provider_mode`: it forces the judge's local client dialect the way
     `provider_mode` does for the system under test; it accepts `ollama` and
     `openai_compat`; when it is absent or empty the judge inherits the SUT's
     `provider_mode`; and an unrecognized value is refused with an error rather than
     silently auto-detected.

     Read the shipped code for each sentence you write. Do not claim behavior the code
     does not have — the accepted pair and the refusal both come from
     `resolve_local_mode`, so check it rather than repeating this task text.

     Edit `docs/docs/benchmarking.md` only. **Do not** edit anything under `docs/site/` —
     that tree is generated output.

  2. Build the docs and read the INFO lines:
     `mkdocs build --strict -f docs/mkdocs.yml`. The gate never builds the docs, so a
     broken anchor or link ships green. Read the output, not just the exit code.

  3. Run `openspec validate fix-issue-469-evaluator-provider-mode --strict` and confirm it
     prints that the change is valid.

  4. Green, format, gate, commit.

## 3. Publish

- [ ] 3.1 Push the branch and open the pull request.

  1. Re-run `bash scripts/gate.sh` bare on a **clean** tree and confirm it exits 0. A run
     against a dirty working tree misattributes line numbers and reports a false coverage
     miss, so commit everything first, then measure.

  2. Confirm the consumer really is untouched — this is an acceptance criterion:

     ```bash
     git diff origin/dev...HEAD -- src/bin/service_benchmark.py    # expect: no output
     ```

  3. `git push -u origin HEAD` — the `-u` matters. A branch created with
     `git checkout -b <name> origin/dev` tracks the trunk, so a bare `git push` would
     target `dev`.

  4. `gh pr create --repo fasrc/archi --base dev --title "fix(#469): render
     evaluator_provider_mode into the RAGAS block" --body-file <file>`.

     The body must contain, in prose in the body itself — **not in the title**, because a
     closing keyword in the title does not link the issue:

     - `Closes #469`
     - the before and after output of the reproduce probe from task 1.1 step 4
     - **the behavior change, named plainly**: a deployment configuring an unusable judge
       mode that today runs anyway, with the judge quietly inheriting the SUT's mode, now
       fails with a named `ValueError`
     - why this key is guarded and `tojson`-serialized while the three sibling
       `evaluator_*` keys beside it are not, and that the inconsistency is deliberate and
       scoped (see `design.md`, Decision 1)
     - that the spec delta is `ADDED` because `local-provider-endpoint-resolution` is not
       in `openspec/specs/` yet (see `design.md`, Decision 4)
     - that patch coverage reports "no lines with coverage information" because the change
       touches no Python under `src/`, and that this is expected rather than a gate miss

  5. Confirm the PR exists **and** that it actually links the issue:

     ```bash
     gh pr view --repo fasrc/archi <n>
     ```

     Do **not** merge it. A human merges, in daylight.
