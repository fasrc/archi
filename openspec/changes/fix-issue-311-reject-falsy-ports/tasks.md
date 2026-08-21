# Tasks - fix-issue-311-reject-falsy-ports

> Every numbered group below ends with the suite green and the project gate passing. A red
> test and the code that turns it green belong to the SAME group and the SAME commit,
> because a task that ends red can never be committed and the loop cannot proceed past it.
> Watch each red test fail mid-group and keep the output for the PR body.
>
> "The project gate" means the single documented gate command for this repo. Run it
> unmodified before every commit and never bypass it.
>
> Line anchors are as of `origin/dev` at `2c404822`. Re-derive before citing any of them in
> the PR body - do not paste these numbers forward.
>
> Scope fences (design.md D6) - if you find yourself editing any of these, stop and reduce
> the change: `_apply_host_mode_port_overrides` (`:936-948`, that is #310 / PR #316),
> `_normalize_port` (`:168-179`, its rejections are the behaviour being restored),
> `src/cli/utils/helpers.py` (that is #300), `test_extract_port_config_host_mode_uses_port_for_both`
> (`:78`, that is #310), and any call site in `src/cli/cli_main.py` (that is #293 / #294).

## 1. A configured falsy port survives extraction and is refused before the teardown (red tests + fix + characterization updates, one commit)

- [x] 1.1 Confirm the defect first, so the red you see later is the right red. From the repo
      root, using the enabled-service plan helper and a config manager over
      `{"services": {"chat_app": {"port": X}}}` with `host_mode=True`, record for
      `X in (0, "", None, "notaport")`: whether `chatbot_port_host` is in
      `extract_port_config()`'s output, and the `errors` list from `validate_port_config()`.
      Expected before the fix: the first three are absent with `errors == []`, and
      `'notaport'` is present and raises. That last one is the control - it proves the
      pipeline works and truthiness is the only difference.
- [x] 1.2 Add the end-to-end red test to `tests/unit/test_cli_create_dev_smoke.py`, modelled
      on `test_force_create_with_invalid_port_keeps_existing_deployment` (`:587`). Copy it,
      set `data["services"]["chat_app"]["port"] = 0` instead of `"notaport"`, keep
      `--hostmode`, and keep all three assertions: `teardowns == []`, the marker file still
      exists, and a non-zero exit. This is the test that proves the user-visible fix (AC1).
- [x] 1.3 Add red unit tests to `tests/unit/test_templates_port_checks.py` using the file's
      own `_plan` / `_cm` helpers. Host mode unless stated:
      - `{"chat_app": {"port": 0}}` -> `chatbot_port_host` **is** in the extraction output
        and equals `0`;
      - `{"chat_app": {"port": ""}}` -> present and equals `""` (AC2);
      - `{"chat_app": {"port": None}}` -> present and equals `None` (design.md D1);
      - each of those three, passed to `validate_port_config`, raises `ValueError` whose
        message contains both `chatbot` and `services.chat_app` (AC3);
      - the scalar route: `{"chat_app": 0}` and `{"chat_app": ""}` -> host key present.
        This is the route the two characterization tests use, so cover it explicitly rather
        than assuming the dict route implies it.
- [x] 1.4 Add the regression guards that fence the fix (design.md D4, AC4). These must pass
      both before and after the source change - they are the tests that catch the obvious
      wrong fix, so write them now:
      - `_cm({})` (no `chat_app` section) -> `chatbot_port_host == 7861` and
        `chatbot_port_container == 7861`, no error;
      - `_cm({"chat_app": {"external_port": 9000}})` in host mode (section present, no
        `port` key) -> both ports are the registry default `7861`, no error;
      - host mode with `chatbot` enabled -> `postgres_port_host` is **not** in the extraction
        output, and `validate_port_config` reports no error mentioning `postgres`. Postgres
        is auto-enabled and has no port default and no config path, so this is the test that
        fails loudly if the emission guard is replaced by a bare emit.
- [x] 1.5 Run `python -m pytest tests/unit/test_templates_port_checks.py -k "falsy or zero or empty or null or postgres" tests/unit/test_cli_create_dev_smoke.py -k "port" -q`
      and confirm the failures are the missing-key and missing-raise assertions plus
      `teardowns != []` - NOT an import error, a fixture error, or a helper signature
      mismatch. A red for the wrong reason proves nothing. Capture the output.
- [x] 1.6 Add `_UNSET = object()` at module level in
      `src/cli/managers/templates_manager.py`, next to the port helpers, with a one-sentence
      comment saying it means "the configuration did not supply a value" and that a
      `None`-valued registry default is not a substitute (design.md D1).
- [x] 1.7 Change the dict branch of `_resolve_ports_from_config` (`:198-204`) to read by key
      presence - `config_value["port"] if "port" in config_value else <default>`, and the
      same for `external_port` on the non-host side - so an explicit `null` is preserved and
      an absent key still falls back. Leave the host-mode mirroring
      (`host_port = container_port`) and the non-dict branch (`:205-206`) exactly as they
      are: host-mode derivation is #310's business (D6).
- [x] 1.8 Have `extract_port_config` (`:210-240`) pass `_UNSET` as both defaults into the
      resolver and apply the registry defaults itself afterwards, so it knows which side the
      configuration actually supplied. Then replace the two truthiness guards at `:235-238`
      with, per side, "emit when the configuration supplied a value **or** the registry
      default is not `None`". Comment the guard with the reason, naming postgres - the next
      reader will otherwise simplify it straight back into the bug (D4).
- [x] 1.9 Replace the skip test in `validate_port_config` (`:260-262`) with a key-presence
      test on `f"{key_prefix}_port_host"`, so a configured `None` is normalized while an
      absent key is still skipped (D5). Do not change `_normalize_port` or the message
      format - AC3 is already satisfied by the existing `config_hint` plumbing.
- [x] 1.10 Update the three characterization tests in this same commit, because they pin the
      old behaviour on purpose and leaving them would make the commit red and unable to pass
      the gate:
      - `test_extract_port_config_falsy_zero_dropped` (`:118`) - rename so the name no
        longer says "dropped" (for example `..._falsy_zero_preserved`), flip the assertion to
        key-present-and-equal-`0`, and replace the "lifted verbatim" docstring comment with
        one sentence saying a configured falsy port is now validated, not discarded;
      - `test_extract_port_config_falsy_empty_string_dropped` (`:126`) - the same treatment;
      - `test_validate_port_config_port_zero_raises_when_reached` (`:174`) - its premise
        ("dropped by extract_port_config, so validate never sees it") is now false. Re-assert
        it to expect the `ValueError`, and rewrite the comment. Note the name already says
        "raises when reached", so it needs no rename.
- [x] 1.11 Confirm these still pass with **no** edit, and stop and reduce the change if any
      needs one: `test_extract_port_config_host_mode_uses_port_for_both` (`:78`),
      `test_extract_port_config_non_host_mode_uses_external_port` (`:69`),
      `test_extract_port_config_scalar_config_value` (`:92`),
      `test_extract_port_config_falls_back_to_registry_defaults` (`:105`), and every
      `_check_ports_available` delegator test (`:334-409`).
- [x] 1.12 Run the full suite: `python -m pytest tests/unit/ -q`. `extract_port_config` is on
      the `create` path, the `restart` path, and the compose-render path, so read any failure
      rather than assuming this module is isolated. Then run the project gate and commit only
      on green.

## 2. The container side is range-checked, and non-host mode is covered (red tests + fix, one commit)

- [x] 2.1 Establish the gap by measurement: in **non**-host mode, resolve
      `{"port": 0}` for `chat_app` and record the pair. Expected `host=7861, container=0` -
      the host side is valid, so after group 1 the config is still accepted. That is the red
      this group fixes (design.md D2).
- [x] 2.2 Add the red end-to-end test: the group 1.2 test without `--hostmode`, still
      asserting `teardowns == []`, the marker file, and a non-zero exit (AC1 outside host
      mode).
- [x] 2.3 Add red unit tests: non-host mode, `{"chat_app": {"port": 0}}` and
      `{"chat_app": {"port": ""}}` -> `validate_port_config` raises `ValueError` naming
      `chatbot` and `services.chat_app`.
- [x] 2.4 Add the fence test **first**, and confirm it is green before and after (D2, AC6):
      `chatbot` and `grader` both enabled with the default registry ports
      (chatbot host 7861 / container 7861, grader host 7862 / container 7861) ->
      `validate_port_config` returns no "multiple services" error, and `7861` maps to
      `chatbot` alone in `port_to_services`. Container ports share a value by design; if this
      test ever goes red, the container value has been added to duplicate detection and the
      default registry now refuses itself.
- [x] 2.5 Run the new tests, confirm the red is the missing `ValueError` and
      `teardowns != []`, and capture the output.
- [x] 2.6 In `validate_port_config`, normalize the **configured** container value for
      validity only: call `_normalize_port` with the same service name and `config_hint`, and
      do **not** append the result to `port_usages`. Validate it only when the configuration
      supplied it - reuse the signal group 1.8 already computes rather than adding a second
      walk, per #293's standing "one derivation on the validation path" requirement. A
      registry default must never be re-checked, or a service's verdict changes with no
      config change.
- [x] 2.7 Full suite green (`python -m pytest tests/unit/ -q`), the project gate exits 0,
      commit.

## 3. Verify against the acceptance criteria and open the PR

- [x] 3.1 Re-run the 1.1 and 2.1 measurements. Host mode `port: 0` / `""` / `null` must now
      be present in the extraction output and raise in validation, and non-host `port: 0`
      must raise. `'notaport'` must behave exactly as before. Paste before and after into the
      PR body - that table is the evidence this change exists for.
- [x] 3.2 Prove no new value reaches the socket probe (design.md D3, and the issue's explicit
      constraint). State in the PR body that `_normalize_port` raises inside
      `validate_port_config` (`:900`) before the probe loop (`:908`), so every newly
      preserved value is refused before `_probe_port` is reachable, and name the delegator
      tests at `:334-409` that still pass unchanged as the evidence.
- [x] 3.3 Walk the issue's acceptance criteria one by one and name, for each, the test that
      covers it. Pay particular attention to "No test in the repo still asserts that a
      configured falsy port is silently dropped" - `grep -rn "dropped\|falsy" tests/unit/`
      and read every hit, rather than assuming the three tests from 1.10 were the only ones.
      Any criterion without a test is unfinished work, not a judgement call.
- [x] 3.4 The project gate exits 0 from a clean tree. Patch coverage is measured against
      `origin/dev`; the touched source lines are few and directly tested, so a low number
      means a test is not reaching them - investigate rather than adding filler. Confirm
      `git status --porcelain` is empty after the commit: the pre-commit formatter is a
      writer while CI only asserts, so format before `git add`, not after.
- [x] 3.5 Push the branch with `git push -u origin fix/issue-311-reject-falsy-ports` (the
      `-u` matters: the branch was created from `origin/dev` and would otherwise track the
      trunk). Open the PR with `gh pr create --repo fasrc/archi --base dev`. Put
      `closes #311` in the PR **body** - a closing keyword in the title does not link the
      issue. No `Co-Authored-By` or session trailers.
- [x] 3.6 In the PR body state plainly: this is a behaviour change, not a refactor. A config
      that previously passed the port check and failed later is now refused at the port
      check, and that refusal is the fix working. Say which reading of `port: null` was
      encoded and why (design.md D1). Name #310 / PR #316 as touching the same functions for
      a different reason, and #300 as adjacent and deliberately untouched. STOP at the open
      PR - do not merge.
