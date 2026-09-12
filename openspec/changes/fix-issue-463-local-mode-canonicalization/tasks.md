# Tasks — canonicalize and validate the local provider's mode

Rules that apply to **every** task below:

- Test first. Write the failing test, run it and watch it fail, then write the minimum
  code to pass. Never write implementation before a red test.
- Every task ends **green** and **commits**. Never leave the suite red at the end of a
  task — the gate runs on commit and a red task can never be committed.
- Run `bash scripts/gate.sh` **bare** before every commit. It refuses to be piped or
  redirected. Never pass `--no-verify`.
- Format **before** `git add`, then confirm `git status` is empty after the commit. The
  pre-commit hook's black is a writer while CI's is an assert, so a staged-then-formatted
  file can be pushed misformatted.
- Give every new test a unique `def test_...` name. The gate runs no linter, so a reused
  name silently deletes the earlier test.
- When you add a test to an existing file, insert it **after** the final line of the file,
  not above it. Inserting above the last line moves that line into your new test and
  silently strips the previous test's last assertion.
- Commit messages: short, lowercase, `fix(#463): ...`. No `Co-Authored-By` trailer.

## 1. The shared whitelist

- [x] 1.1 Add `src/utils/local_mode.py` and its unit tests in one commit.

  Order of work:

  1. Create `tests/unit/test_local_mode.py`. It must fail because the module does not
     exist yet. Cover:

     | input | `canonical_local_mode` result |
     |---|---|
     | `"openai_compat"` | `"openai_compat"` |
     | `"OpenAI_Compat"` | `"openai_compat"` |
     | `"OPENAI_COMPAT"` | `"openai_compat"` |
     | `" openai_compat "` | `"openai_compat"` |
     | `"ollama"` | `"ollama"` |
     | `"Ollama"` | `"ollama"` |
     | `"  OLLAMA  "` | `"ollama"` |
     | `None` | `None` |
     | `"openai-compat"` | raises `ValueError` |
     | `"vllm"` | raises `ValueError` |
     | `""` | raises `ValueError` |
     | `"   "` | raises `ValueError` |
     | `5` (non-string) | raises `ValueError` |

     Assert the message of one raising case contains the rejected value, `ollama`, and
     `openai_compat` — all three substrings, in one test.

     Then cover `apply_local_mode(extra, raw, *, overwrite=True)`:

     - a recognized raw value writes the canonical string into `extra["local_mode"]`
     - `None` writes nothing, and leaves `extra` unchanged
     - an unrecognized raw value raises, and does so **before** touching `extra`
     - with `overwrite=False` and `local_mode` already in `extra`, the existing value
       wins and is left exactly as it was
     - with `overwrite=False` and no existing key, the canonical value is written
     - with the default `overwrite=True` and an existing key, the canonical value
       replaces it

  2. Watch those tests fail.

  3. Write `src/utils/local_mode.py`. It imports only from `typing`.

     **Do not put it under `src/archi/providers/`.** Importing any submodule of that
     package runs `src/archi/providers/__init__.py`, whose line 21 reaches
     `src/archi/providers/base.py:13` — `from
     langchain_core.language_models.chat_models import BaseChatModel`. So even a
     `typing`-only module placed there pulls LangChain into every seam that imports it, and
     `src/data_manager/collectors/processing.py:784` imports the provider lazily precisely
     to avoid that (see its docstring at lines 775-783). `src/utils/` is LangChain-free and
     `processing.py:36` already imports from it. No test would catch the regression —
     LangChain is installed in the gate environment.

     Define:

     - `MODE_OLLAMA = "ollama"` — the **only** assignment of that literal in the repository
     - `MODE_OPENAI_COMPAT = "openai_compat"` — the **only** assignment of that literal in
       the repository
     - `LOCAL_MODES` — the accepted pair
     - `canonical_local_mode(value)` — returns `None` for `None`; otherwise strips and
       lower-cases a string and returns it if it is in `LOCAL_MODES`; raises `ValueError`
       naming the rejected value and the valid values for anything else, including a
       non-string
     - `apply_local_mode(extra, raw, *, overwrite=True)` — canonicalizes `raw` **first**,
       then writes the key only when the result is not `None` and the precedence rule
       allows it

     `canonical_local_mode` must be idempotent: feeding it its own output returns that
     output unchanged.

  4. Green, format, gate, commit.

## 2. The provider

- [x] 2.1 Canonicalize inside `LocalProvider` and re-point the predicate, in one commit.

  1. Add tests to the **end** of `tests/unit/test_local_provider_env_override.py`. Build
     the provider directly, as the existing tests in that file do:
     `LocalProvider(ProviderConfig(provider_type=ProviderType.LOCAL, base_url=...,
     extra_kwargs={"local_mode": ...}))`. Use
     `monkeypatch.delenv("OLLAMA_HOST", raising=False)` in every new case so none of them
     leaks the variable.

     Assert the **client**, not the endpoint alone — asserting the resolved `base_url` is
     what let the two sites drift in the first place. Patch `_get_ollama_model` and
     `_get_openai_compat_model` on the instance, call `get_chat_model("m")`, and check
     which one was called. Cover the full probe matrix from issue #463:

     | `local_mode` | expected |
     |---|---|
     | `openai_compat` | OpenAI-dialect client |
     | `OpenAI_Compat` | OpenAI-dialect client — **fails today** |
     | `OPENAI_COMPAT` | OpenAI-dialect client — **fails today** |
     | ` openai_compat ` | OpenAI-dialect client — **fails today** |
     | `ollama` | Ollama client |
     | `Ollama` | Ollama client |
     | `openai-compat` | `ValueError` at construction — **fails today** |
     | `vllm` | `ValueError` at construction — **fails today** |
     | `""` | `ValueError` at construction — **fails today** |
     | `None` | Ollama client, no raise |
     | key absent entirely | Ollama client, no raise |

     Add one test that a compat-mode provider with no `base_url` resolves
     `LocalProvider.DEFAULT_OPENAI_COMPAT_BASE_URL` **and** builds the OpenAI-dialect
     client, in the same test. Reference the class constants rather than repeating URL
     literals.

  2. Watch them fail.

  3. In `src/archi/providers/local_provider.py`:

     - import `MODE_OLLAMA`, `MODE_OPENAI_COMPAT` and `canonical_local_mode` from
       `src.utils.local_mode`
     - in `__init__`, canonicalize `config.extra_kwargs.get("local_mode")` **before** the
       `_is_openai_compat` branch at line 84. When the `local_mode` key is **present**,
       write the result back into `config.extra_kwargs["local_mode"]`, substituting
       `MODE_OLLAMA` when the canonical result is `None`. When the key is **absent**, write
       nothing.

       The `MODE_OLLAMA` substitution is load-bearing, not tidiness. The `local_mode`
       property at line 99 is `.get("local_mode", "ollama")`, so its default fires only on a
       missing key — a key present holding `None` returns `None`, and `list_models` (line
       177) and `validate_connection` (line 266) both compare `self.local_mode == "ollama"`
       and take their non-Ollama branch. Add a test for each of those two readers with
       `extra_kwargs={"local_mode": None}`: `validate_connection` must probe the Ollama
       `/api/tags` route, and `list_models` must attempt Ollama discovery.
     - change `_is_openai_compat` to `return mode == MODE_OPENAI_COMPAT`. Do not
       reintroduce a second mode test anywhere.
     - rewrite the `_is_openai_compat` docstring. It currently says `get_chat_model`
       "falls back to `ChatOllama` for every other value — including `None` and a typo".
       After this change a typo raises and only `None` falls back, so that sentence
       documents behavior the code no longer has.
     - leave the `local_mode` property as a plain read with the `ollama` default, and
       leave the per-call `local_mode` refusal in `get_chat_model` alone.

  4. **Restate the five predating tests that pin the removed behavior.** This step is not
     optional and the task cannot end green without it. `tests/unit/
     test_local_provider_env_override.py` holds 22 tests; five of them build a provider with
     `local_mode="vllm"` through the file's shared `_build()` helper and assert it resolves
     an Ollama endpoint. Applying step 3 turns the file red at exactly these five:

     | line | test |
     |---|---|
     | 95 | `test_unknown_mode_falls_back_to_the_ollama_default` |
     | 101 | `test_unknown_mode_still_honors_ollama_host` |
     | 106 | `test_unknown_mode_configured_base_url_yields_to_ollama_host` |
     | 119 | `test_unknown_mode_endpoint_and_client_agree` |
     | 215 | `test_a_per_call_mode_cannot_promote_an_unrecognized_mode` |

     Restate each one against a **null** mode instead of `vllm`. A null mode still resolves
     the Ollama endpoint and still yields to `OLLAMA_HOST`, so every test keeps its original
     point — the endpoint rule and the client dialect agree; the environment override still
     applies; a per-call mode cannot promote. Rename each to say `null` rather than
     `unknown`, keeping the names unique. This is the same restatement task 4.1 applies to
     the two analogous #450 spec scenarios; keep the two in step.

     **Do not** rescue these five by moving the refusal out of `__init__` and into the four
     seams only. That keeps the whole suite green and the gate happy while the issue's own
     reproduction probe — which constructs `LocalProvider` directly — still prints
     `mode='vllm' client=ChatOllama`. The change would ship green and fix nothing.

     The other 17 tests must pass **unmodified**. List the five restated tests in the PR
     body with the reason.

  5. Green, format, gate, commit.

## 3. The four config seams

- [ ] 3.1 Wire every seam to `apply_local_mode`, in one commit.

  1. Write the failing tests first, one per seam — **all four are testable**:

     - `get_model` in `src/archi/providers/__init__.py` (seam at line 274)
     - `_build_provider_config` in `src/archi/pipelines/agents/base_react.py` (line 1340)
     - `_resolve_provider_config` in `src/data_manager/collectors/processing.py` (line 1013)
     - `_build_provider_config_from_payload` in `src/interfaces/chat_app/app.py` (line 181)

     For each: a `mode` of `OpenAI_Compat` yields `local_mode` of `openai_compat`; a
     `mode` of `vllm` raises `ValueError`; a `mode` of `""` raises `ValueError`; a config
     with no `mode` key writes no `local_mode`. For the two seams that guard on
     `"local_mode" not in extra`, add a test that an existing `extra_kwargs["local_mode"]`
     still wins unchanged.

     The project's CLAUDE.md says `app.py` is not imported by the unit tests. **That is not
     true of this function.** `tests/unit/test_provider_config_override.py:14` imports
     `_build_provider_config_from_payload` directly, three of its tests call it, and one at
     line 48 already asserts on `extra_kwargs["local_mode"]`. A coverage run shows
     `app.py:181-182` executed. Add the app-seam tests to that file, at its end. There is no
     line budget to respect in `app.py` for this change.

  2. Watch them fail.

  3. Replace each seam with a single `apply_local_mode` call, passing the **raw**
     `cfg.get("mode")`, not a truthiness-filtered value. All four seams are truthiness
     -guarded today, so an empty-string mode never reaches `extra_kwargs`; the
     canonicalizer has to run before that guard for `""` to be refused — delete the
     `and cfg.get("mode")` / `if mode and ...` part of each guard, keeping only the
     provider-is-local test. Preserve each seam's existing precedence:

     - `app.py:181-182` and `base_react.py:1340-1341` — default `overwrite=True`
     - `__init__.py:274-275` and `processing.py:1013-1014` — pass `overwrite=False`

  4. **The `base_react.py` seam needs the surrounding `try` narrowed first.** Lines
     1338-1343 are:

     ```python
     try:
         provider_type = ProviderType(provider_key)
         if provider_type == ProviderType.LOCAL and cfg.get("mode"):
             extra["local_mode"] = cfg.get("mode")
     except Exception:
         pass
     ```

     A swap of line 1341 in place leaves the call inside `except Exception: pass`, which
     eats the new `ValueError`. Because `apply_local_mode` raises before it writes, `extra`
     then carries no `local_mode` at all, the provider falls to its `ollama` default, and
     the constructor's refusal never fires — the agent path keeps silently building a
     `ChatOllama` client for `mode: vllm`, which is the defect this change exists to remove.
     Your seam test from step 1 will be red and must stay red until you fix this properly.

     Do not simply hoist the call out of the `try` either: the `try` is there to absorb
     `ProviderType(provider_key)` raising for a provider key that is not an enum member.
     Narrow it to that one expression and put the seam after it:

     ```python
     try:
         provider_type = ProviderType(provider_key)
     except ValueError:
         provider_type = None
     if provider_type == ProviderType.LOCAL:
         apply_local_mode(extra, cfg.get("mode"))
     ```

     Add a test that an unknown provider key still returns without raising and without a
     `local_mode` key, so the narrowing does not change that path.

  5. Green, format, gate, commit.

- [ ] 3.2 Delegate the benchmark runner's explicit branch, in one commit.

  1. Add a failing test for `resolve_local_mode` in `src/bin/benchmark_sut.py:27`: an
     explicit `OpenAI_Compat` returns `openai_compat`; an explicit `vllm` raises
     `ValueError`; an empty explicit value with a URL ending in `/v1` still returns
     `openai_compat` by auto-detect and does **not** raise.

  2. Watch it fail, then replace `return str(explicit).lower()` with
     `return canonical_local_mode(explicit)`, imported from `src.utils.local_mode`. Keep
     the `if explicit:` guard exactly as it is — the auto-detect fall-through for an empty
     value is documented behavior of the benchmark runner. Leave the auto-detect branch
     untouched.

     `src/bin/benchmark_sut.py` has only `typing` at module scope today, which is the
     second reason the canonicalizer lives under `src/utils/` — see task 1.1 step 3.

  3. Green, format, gate, commit.

## 4. Docs and spec reconciliation

- [ ] 4.1 Update the docs and the superseded #450 scenarios, in one commit.

  1. `docs/docs/configuration.md:441,858` and `docs/docs/models_providers.md:105,120` show
     `mode:`. State there that the value is matched without regard to case or surrounding
     whitespace, and that any other value is rejected at startup with an error naming the
     valid values. Do not claim behavior the code does not have — read the shipped code
     for each sentence you write.

  2. Amend the now-false text in
     `openspec/changes/fix-issue-450-ollama-host-scope/specs/local-provider-endpoint-resolution/spec.md`,
     under "The endpoint rule and the client dialect read the local mode the same way".
     That change is merged but not archived, so this is still live spec text. Three edits:

     - Scenario "An unrecognized mode resolves the Ollama endpoint" — uses mode `vllm`,
       which now raises.
     - Scenario "An unrecognized mode still yields to the environment" — same.
     - The requirement's **rationale paragraph** at lines 61-64, which reads "`ChatOllama`
       for every other value, including `None` and a misspelling" and "`local_mode` reaches
       the provider straight from operator config and no seam validates or canonicalizes
       it". Both sentences are now false: a misspelling raises, and all four seams
       canonicalize.

     Restate the two scenarios against a **null** mode, which still resolves the Ollama
     endpoint and still yields to `OLLAMA_HOST`, so the requirement keeps its point — one
     predicate serves both sites — without asserting behavior the tree no longer has. Use
     the same restatement as the five tests in task 2.1 step 4, so the spec and the tests
     stay in step.

     **Leave the requirement's SHALL line at line 59 alone.** "Only the exact mode
     `openai_compat`" stays true — this change canonicalizes *before* the predicate and
     keeps one exact comparison against a shared constant.

  3. Build the docs and read the INFO lines: `mkdocs build --strict -f docs/mkdocs.yml`.
     The gate never builds the docs, so a broken anchor ships green.

  4. Run `openspec validate fix-issue-463-local-mode-canonicalization --strict` and
     confirm it prints `is valid`.

  5. Green, format, gate, commit.

## 5. Publish

- [ ] 5.1 Push the branch and open the pull request.

  1. Re-run `bash scripts/gate.sh` bare on a **clean** tree and confirm it exits 0. A run
     against a dirty working tree misattributes line numbers and reports a false coverage
     miss, so commit everything first, then measure.

  2. Run the reproduction probe from issue #463 and capture its table. Pipe the script via
     stdin and print the resolved module file, so you measure this branch and not the
     installed package.

  3. `git push -u origin HEAD` — the `-u` matters. A branch created with
     `git checkout -b <name> origin/dev` tracks the trunk, and a bare `git push` would
     target `dev`.

  4. `gh pr create --repo fasrc/archi --base dev --title "fix(#463): canonicalize and
     validate the local provider's mode at the config seams" --body-file <file>`.

     The body must contain, in prose in the body itself (not the title):

     - `Closes #463`
     - the before and after probe tables
     - **the behavior change, named plainly**: a deployment carrying an unrecognized
       `mode` that silently ran Ollama now fails at construction with a named error
     - the deliberate deviation from the issue's `grep -c` acceptance criterion, and why
       zero occurrences in `local_provider.py` satisfies its purpose better than one
       (see `design.md`, Decision 1)
     - why the module went to `src/utils/` rather than beside the provider, with the
       measured import result (see `design.md`, Decision 1)
     - the amendment to the unarchived #450 change directory, and why
     - the out-of-scope note on the four seams' differing `extra_kwargs` precedence
     - the five restated tests in `test_local_provider_env_override.py`, named, with the
       reason (see `design.md`, Decision 9)
     - the second, pre-existing defect this change closes: a present-but-null `local_mode`
       key sent `list_models` and `validate_connection` down their non-Ollama branches

  5. Confirm the PR actually exists and that it links the issue:
     `gh pr view --repo fasrc/archi <n>`. Do **not** merge it.
