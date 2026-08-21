# Tasks - fix-issue-310-host-mode-external-port

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
> Scope fences (design.md D5) - if you find yourself editing any of these, stop and reduce
> the change: `src/cli/utils/helpers.py` (that is #300),
> `test_validate_port_config_port_zero_raises_when_reached` (that is #311), and
> `_apply_host_mode_port_overrides` (it is the correct side of the disagreement).

## 1. Host-mode derivation follows `external_port` (red tests + fix + characterization update, one commit)

- [x] 1.1 Confirm the defect first, so the red you see later is the right red. From the repo
      root run the repro printed in issue #310 and record both numbers: the validated pair
      from `_resolve_ports_from_config(dict(cfg), host_mode=True, host_default=7861,
      container_default=7861)` for `cfg = {"port": 7861, "external_port": 9000}`, and the
      rendered value of `services.chat_app.port` after
      `_apply_host_mode_port_overrides({"services": {"chat_app": dict(cfg)}})`.
      Expected before the fix: validated `(7861, 7861)`, rendered `9000`.
- [x] 1.2 In `tests/unit/test_templates_port_checks.py`, next to the existing host-mode cases
      (around `:78`), add failing tests using the file's own `_plan` / `_cm` helpers:
      - host mode, `{"port": 7861, "external_port": 9000}` -> `chatbot_port_host == 9000`
        **and** `chatbot_port_container == 9000` (design.md D2);
      - host mode, `{"port": 7861}` with no `external_port` -> both ports `7861` (no
        regression, AC3);
      - host mode, `external_port` present and `port` absent -> both ports are the
        `external_port` value;
      - host mode, `{"port": 7861, "external_port": 0}` -> the derivation treats `0` as
        present (D1). Assert on the *derivation* by calling `_resolve_ports_from_config`
        directly, not through `extract_port_config`, because the truthy guard at `:236`
        drops `0` before it reaches the output dict and that guard is #311's business.
- [x] 1.3 Add a failing duplicate-detection test (AC2): two enabled services in host mode,
      both `external_port: 9000`, with **different** `port` values -
      `{"chat_app": {"port": 7861, "external_port": 9000}, "data_manager": {"port": 7871,
      "external_port": 9000}}`, plan `["chatbot", "data-manager"], host_mode=True`. Assert
      the errors list from `validate_port_config` contains the
      "assigned to multiple services" text. Model the assertion on the existing
      `test_validate_port_config_duplicate_port_returns_error_string` (`:190`) so the
      expected string matches the real one.
- [x] 1.4 Run `python -m pytest tests/unit/test_templates_port_checks.py -k "host_mode or duplicate" -q`
      and confirm the failures are the 7861-vs-9000 assertions and the missing duplicate
      error - NOT an import error, a fixture error, or a helper signature mismatch. A red
      for the wrong reason proves nothing. Capture the output.
- [x] 1.5 Change only the `host_mode` side of the dict branch in
      `_resolve_ports_from_config` (`src/cli/managers/templates_manager.py:189-206`) so that
      when `config_value.get("external_port") is not None` both the host and the container
      port take that value, and otherwise both take `config_value.get("port", <default>)`.
      Key off `is not None`, mirroring `_apply_host_mode_port_overrides` (`:936-948`)
      exactly - see design.md D1. Leave the non-host branch and the non-dict branch
      untouched (D4).
- [x] 1.6 Update the characterization test
      `test_extract_port_config_host_mode_uses_port_for_both` (`:78`) in this same commit:
      rename it to name the new behaviour (for example
      `test_extract_port_config_host_mode_uses_external_port_for_both`), flip its assertions
      to `9000`, and replace its comment with one sentence saying host mode mirrors the
      override. It pins the old behaviour deliberately, so leaving it would make this commit
      red and unable to pass the gate.
- [x] 1.7 Confirm the non-host characterization test
      `test_extract_port_config_non_host_mode_uses_external_port` (`:69`) still passes with
      no edit, and that
      `test_validate_port_config_port_zero_raises_when_reached` (`:174`) still passes with no
      edit. Either one needing a change means the diff has grown past this issue (D4, D5).
- [x] 1.8 Run the full suite: `python -m pytest tests/unit/ -q`. Host mode is also read by
      the compose templates, so read any failure rather than assuming this module is
      isolated. Then run the project gate and commit only on green.

## 2. The error message names the key that was validated (red test + fix, one commit)

- [x] 2.1 Add a failing test (AC5): in host mode, a service whose config sets
      `external_port` and whose host-side value is invalid produces a `ValueError` naming
      `services.chat_app.external_port`; and the same service with `port` only names
      `services.chat_app.port`. Model it on
      `test_validate_port_config_nonnumeric_names_service_and_hint` (`:149`). Note that the
      existing hint assertion at `:158` is a prefix check (`"services.chat_app" in msg`) and
      does not pin the suffix, so it needs no edit - assert the full suffix in the new test.
- [x] 2.2 Run it, confirm it fails on the suffix (host mode still says `.port` for a service
      that sets `external_port`), and capture the output.
- [x] 2.3 Extract the `port_config_path` walk from `extract_port_config` (`:218-225`, the
      `try` / `for key in ...split(".")` / `except (KeyError, TypeError)` block) into one
      module-level helper that returns the walked value or `None`, absorbing
      `KeyError`/`TypeError` exactly as today. Call it from `extract_port_config` in place of
      the inline walk. Do not change what `extract_port_config` returns for any input.
- [x] 2.4 Give `_service_port_config_hint` (`:182-186`) the walked config value and have it
      pick the host-mode suffix accordingly: `external_port` when the value is a dict whose
      `external_port` `is not None`, otherwise `port`. Non-host mode keeps `external_port`
      unconditionally. Feed it from `validate_port_config` using the helper from 2.3 -
      `base_config` is already in scope there, so do not add a second walk (design.md D3).
- [x] 2.5 Full suite green (`python -m pytest tests/unit/ -q`), the project gate exits 0,
      commit.

## 3. Verify against the acceptance criteria and open the PR

- [ ] 3.1 Re-run the 1.1 repro. It must now print validated `(9000, 9000)` and rendered
      `9000`. Paste the before and after into the PR body - that pair is the evidence this
      change exists for.
- [ ] 3.2 Walk the issue's acceptance criteria one by one and name, for each, the test that
      covers it. Any criterion without a test is unfinished work, not a judgement call.
- [ ] 3.3 The project gate exits 0 from a clean tree. Patch coverage is measured against
      `origin/dev`; the touched source lines are few and directly tested, so a low number
      means a test is not reaching them - investigate rather than adding filler. Confirm
      `git status --porcelain` is empty after the commit: the pre-commit formatter is a
      writer while CI only asserts, so format before `git add`, not after.
- [ ] 3.4 Push the branch with `git push -u origin fix/issue-310-host-mode-external-port`
      (the `-u` matters: the branch was created from `origin/dev` and would otherwise track
      the trunk). Open the PR with
      `gh pr create --repo fasrc/archi --base dev`. Put `closes #310` in the PR **body** -
      a closing keyword in the title does not link the issue. No `Co-Authored-By` or session
      trailers.
- [ ] 3.5 In the PR body state plainly: this is a behaviour change, not a refactor; a
      host-mode config with two enabled services sharing one `external_port` is now refused
      where it previously passed, and that refusal is the fix working. Name #300 and #311 as
      adjacent and deliberately untouched. STOP at the open PR - do not merge.
