# Tasks — fix-issue-293-validate-ports-before-teardown

> Every numbered group below ends with the suite green and the gate passing — red tests
> and the code that turns them green are folded into the same group, because a task that
> ends red can never be committed. Watch each red test fail mid-group and capture the
> output for the PR body.
>
> Line anchors are as of `origin/dev` at `cdd6e35d`; re-derive before citing in the PR.

## 1. Lift the derivation and the pure checks (red unit tests + refactor, one commit)

- [x] 1.1 Create `tests/unit/test_templates_port_checks.py` targeting the module-level
      functions `extract_port_config(plan, config_manager)` and
      `validate_port_config(plan, config_manager, port_config)` in
      `src.cli.managers.templates_manager`. Cover:
      - a valid multi-service plan returns the expected `<service>_port_host` /
        `<service>_port_container` keys;
      - a nonnumeric **host-side** port value raises `ValueError` naming the value, the
        service, and the config hint (host-side means the `port` key under
        `host_mode=True`, `external_port` otherwise — see design.md fact 9);
      - an out-of-range host-side port raises `ValueError`;
      - one host port assigned to two ENABLED services yields that error string in the
        returned errors list — `validate_port_config` returns
        `(port_to_services, errors)` and does NOT raise on duplicates (the caller
        raises; see design.md D1);
      - a DISABLED service with an invalid or conflicting port produces NO error
        (negative case — the check filters to `plan.get_enabled_services()`);
      - the host-mode postgres entry from `services.postgres.port`;
      - `host_mode=True` vs `host_mode=False` derivation (the dict branch of
        `_resolve_ports_from_config` forks on it: `port` vs `external_port`);
      - the scalar-config-value branch;
      - the registry-defaults fallback when the config omits a service's section;
      - falsy configured values (`0`, `""`) are dropped without error — pre-existing
        behaviour lifted verbatim, pinned so a future "fix" is a conscious choice.
      Run them and watch them fail (`ImportError`: the functions do not exist yet) —
      capture the output.
- [x] 1.2 In `src/cli/managers/templates_manager.py`, lift to module level:
      `extract_port_config(plan, config_manager)` (body of `_extract_port_config`,
      `self.registry` → the module-global `service_registry`,
      `self._resolve_ports_from_config` → the lifted module function);
      `validate_port_config(plan, config_manager, port_config)` (the pure half of
      `_check_ports_available` — `port_usages` construction including the host-mode
      postgres entry, with `_normalize_port` raising immediately on bad values as
      today; grouping into `port_to_services`; duplicate-assignment error strings
      collected, not raised — returns `(port_to_services, errors)`); plus module-level
      `_normalize_port`, `_service_port_config_hint`, and `_resolve_ports_from_config`
      (called by `extract_port_config`'s body — omitting it is a `NameError`).
      Convert the methods into delegators: `_extract_port_config(context)` calls
      `extract_port_config(...)`; `_check_ports_available(context, port_config, *,
      allow_port_reuse=False)` calls `validate_port_config(...)`, appends probe errors
      to the returned list (probe loop unchanged, still skipped when `allow_port_reuse`
      is truthy), and raises the single combined
      `ValueError("Port check failed:\n" + ...)` when non-empty — byte-identical to
      today, including co-occurring duplicate + in-use errors in one message.
- [x] 1.3 Add delegator tests (same new test file, `_probe_port` monkeypatched — never
      bind real ports): probe runs when `allow_port_reuse` is falsy; probe skipped when
      truthy while duplicate detection still refuses (the behaviour `restart()` relies
      on via `allow_port_reuse=True`); combined duplicate + in-use message matches
      today's format exactly.
- [x] 1.4 New unit tests pass; full `pytest tests/unit/` green;
      `bash scripts/gate.sh` green. Commit (message like
      `refactor(#293): lift pure port checks to module level`).

## 2. Hoist the pure checks above the teardown in `create()` (red smoke tests + fix, one commit)

- [ ] 2.1 In `tests/unit/test_cli_create_dev_smoke.py`, add
      `test_force_create_with_invalid_port_keeps_existing_deployment` and
      `test_force_create_with_duplicate_ports_keeps_existing_deployment`, modelled on
      `test_force_create_with_unbuildable_compose_plan_keeps_existing_deployment`
      (`:465`) with two additions the model does NOT have (both load-bearing — design.md
      D5): patch the sentinel `monkeypatch.setattr(cli_main, "TemplateManager",
      _stop_before_host_mutation)` so the red run cannot reach
      `VolumeManager.create_required_volumes` and create real docker volumes, and pass
      `--hostmode` (or set `external_port`) so the bad value is on the host side and
      actually refusable. Recipe: existing deployment via
      `_existing_deployment(archi_home)`, teardown recording via
      `_record_teardowns(monkeypatch)`, a copy of the example config in `tmp_path` with
      (a) a nonnumeric or out-of-range port for an enabled service and (b) the same
      port assigned to two enabled services. Assert exit code non-zero,
      `teardowns == []`, the marker file intact, AND the error text in `result.output`
      (substring from `_normalize_port` / the duplicate error — e.g. `Invalid port
      value` / `assigned to multiple services` — so a generic refusal cannot pass; the
      outer handler wraps as `ClickException(str(e))`, preserving the message). Run
      both and watch them fail for the right reason — `teardowns != []` — and capture
      the output for the PR.
- [ ] 2.2 In `src/cli/cli_main.py`, import `extract_port_config` and
      `validate_port_config` alongside the existing `TemplateManager` import, and call
      them in `create()` after `ServiceBuilder.build_compose_config(...)` (`:242-250`)
      and before `remove_existing_deployment(...)` (`:261`), raising
      `ValueError("Port check failed:\n" + ...)` on a non-empty errors list (design.md
      D2), with a comment stating the invariant (pure port checks can refuse the
      deployment, so they run before anything destructive; the availability probe stays
      post-teardown — see 3.1). Do NOT construct a `TemplateManager` here — the smoke
      tests' sentinel patches it to raise, and
      `test_force_create_still_tears_down_once_validation_passes` (`:623`) asserts the
      teardown happens before the sentinel fires.
- [ ] 2.3 Add `test_dry_force_create_with_invalid_port_fails` (`--dry --force`,
      `--hostmode`): exits non-zero, performs no teardown, and does NOT print the
      "[DRY RUN] Would remove existing deployment" notice (a real run would refuse
      before reaching the teardown). Also add
      `test_dry_create_with_invalid_port_fails` (plain `--dry`, no `--force`): exits
      non-zero — the call site is not gated on `force`, and the proposal pins that dry
      runs now mirror real runs. Both should go green with the 2.2 insertion alone
      because the `--dry` return (`:266`) sits below the new call site — if not, the
      call site is in the wrong place; fix the placement, not the test.
- [ ] 2.4 Confirm every pre-existing test in `test_cli_create_dev_smoke.py` passes
      UNMODIFIED — in particular
      `test_force_create_still_tears_down_once_validation_passes` (the sentinel) and
      `test_dry_force_create_reports_teardown_without_performing_it` (valid dry run
      still prints the notice and exits 0).
- [ ] 2.5 Full suite green; `bash scripts/gate.sh` green (diff coverage ≥ 80%). Commit
      (message like `fix(#293): validate port config before the --force teardown`).

## 3. Make the probe's position durable and the artifacts truthful (one commit)

- [ ] 3.1 Add a comment on the probe loop inside `_check_ports_available()` stating why
      it cannot run pre-teardown: the existing deployment still holds its ports, so an
      early probe would report a false conflict for every port the running deployment
      uses (acceptance criterion 5).
- [ ] 3.2 Verify one derivation on the validation path: `grep -rn "port_config_path"
      src/` shows the config walk implemented in `extract_port_config`, the registry
      definitions, the delegator call sites — and ONE pre-existing hit outside the
      validation path: `show_service_urls` (`src/cli/utils/helpers.py:397-424`), the
      display-path walk with divergent fallback semantics that design.md D6 scopes out.
      Any OTHER walk is a defect in this change.
- [ ] 3.3 File the follow-up issue for `show_service_urls` (per design.md D6 and the
      `archi-followup-issue` conventions: objective, paths, plan, acceptance criteria):
      it reimplements the `port_config_path` walk with fallback semantics that diverge
      from `extract_port_config` (host mode: `default_container_port` vs
      `default_host_port` — for grader that is 7861 vs 7862), so the success banner can
      print a URL the validated derivation disagrees with. Consolidating it changes
      printed output, hence out of scope here. The PR body must state that the issue's
      acceptance criterion 4 is met on the validation path and link this follow-up for
      the display path.
- [ ] 3.4 Check `docs/docs/fasrc_archi.md` for any statement about what `create --force`
      validates before teardown; update only if it names ports or enumerates the checks
      in a way this change falsifies. If nothing needs changing, note that in the PR
      body instead of inventing a doc edit.
- [ ] 3.5 Gate green. Commit.

## 4. PR

- [ ] 4.1 Push the branch (`git push -u origin fix/issue-293-validate-ports-before-teardown`)
      and open the PR against `fasrc/archi:dev` with `closes #293` **in the body** (a
      closing keyword in the title does not link the issue). Include: the captured red
      output from 1.1 and 2.1, why the probe cannot move, the double-run rationale
      (design.md D4), the acceptance-criteria checklist from the issue with evidence
      per item — stating explicitly that criterion 4 is satisfied on the validation
      path with the display-path walk deferred to the 3.3 follow-up issue — and the
      note from 3.4 if no doc edit was needed.
- [ ] 4.2 Confirm the PR↔issue link exists via the GraphQL closing-issues API, not by
      reading the body.
