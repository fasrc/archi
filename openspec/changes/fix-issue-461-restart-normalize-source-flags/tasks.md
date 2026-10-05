> **Every group below must end with a green suite.** The gate (`bash scripts/gate.sh`) runs
> before every commit, and a group that ends red cannot be committed. The red step and the fix
> that clears it are in the SAME group and the SAME commit.
>
> Line numbers are as of `origin/dev` @ `db701852`. Re-derive them with `grep -n` before you
> rely on them. `src/cli/cli_main.py` is black-clean on `origin/dev`. Run `black` and `isort`
> on changed files BEFORE `git add`, and confirm `git status` is empty after each commit.

## 1. Normalize the source flags in restart, test-first

- [ ] 1.1 Create `tests/unit/test_cli_restart_source_flags.py` with the fixtures from
  `design.md` ("Test seam"): a throwaway `ARCHI_DIR` (patch `cli_main.ARCHI_DIR` too), a
  deployment dir with `compose.yaml` and a deployed rendered config made the way `create`
  makes it, and `cli_main.ServiceBuilder.build_compose_config` patched to raise
  `RuntimeError(SENTINEL)`. Base the input config on
  `examples/deployments/basic-openai/config.yaml` (`name: my_archi`; it omits `git`). Add three
  tests: (a) the new config omits `git.enabled`, the deployed config has `git.enabled: false`
  → output has `SENTINEL` and no "Restart config changes are restricted"; (b) recorders on
  `ConfigurationManager.set_sources_enabled`, `ConfigurationManager.validate_configs`, and
  `cli_main._validate_non_chatbot_sections` that call through → `set_sources_enabled` comes
  before both of the others; (c) the new config sets `git.enabled: true` (give `git` what
  `validate_configs` and `get_enabled_sources` need to treat it as selected) while the deployed
  config has `false` → output has "Restart config changes are restricted". Run the file against
  unmodified `src/` and record each result: expect (a) and (b) red, (c) green. Then, in
  `restart` in `src/cli/cli_main.py`, add `config_manager.set_sources_enabled(enabled_sources)`
  directly after `enabled_sources = source_registry.resolve_dependencies(enabled_sources)` and
  directly before `config_manager.validate_configs(enabled_services, enabled_sources)` (`:614`).
  Re-run: all three green. Run `bash scripts/gate.sh`, then commit the test and the fix
  together.

## 2. Verify, push, and open the PR

- [ ] 2.1 Confirm `create` (`:225`, `:249`) and `evaluate` (`:886`, `:892`) are unchanged
  (`git diff origin/dev -- src/` shows one added line in `restart`). Run
  `bash scripts/gate.sh` on the branch tip: green, patch coverage at or above 80%. Push with
  `git push -u origin fix/issue-461-restart-normalize-source-flags`. Open the PR with
  `gh pr create --repo fasrc/archi --base dev`; put `closes #461` in the PR BODY, not the
  title. The body includes the red results from 1.1 and states the gap: no live restart
  against a real deployment was run. No `Co-Authored-By` trailer. Do not merge.
