# Design

## D1. Mechanism (b), not (a) — and the template default goes too

Issue #330 offers two mechanisms:

- **(a)** Render no default. The seam's existing "`agent_config_path` is required" error
  then names the key. Smallest diff.
- **(b)** Validate at create time, so `archi create` fails with an actionable message.
  Loudest diagnostic.

**(a) alone does not satisfy the issue's acceptance criteria.** Criterion 1 reads: a test
proves `enabled: true` without `agent_config_path` "either fails create naming
`services.chat_app.evaluations.agent_config_path`, or renders a value
`build_evaluation_service` accepts". Under (a) the render is `null`, which the seam refuses
at `src/interfaces/chat_app/evaluation_console.py:93`; create still exits 0. So (a) meets
neither branch. The second branch is unreachable by construction — the issue's own context
states there is no rendered default that ever works, because the only path the template can
know is the live one.

So (b) is required. But (b) alone leaves the template still rendering the refused path, and
criterion 3 asks that "the seam docstring and the rendered template agree about the
default". The docstring says there is no default. So the default is removed as well.

Taking both is not scope creep: each acceptance criterion needs one of them.

## D2. The check goes above the teardown, not in the staging seam

Issue #330 names `src/cli/managers/templates_manager.py` staging as the home for mechanism
(b). That placement would work and would still be a bug.

`_stage_evaluation_config` (`src/cli/managers/templates_manager.py:603`) runs from
`prepare_deployment_files()`, called at `src/cli/cli_main.py:333`. The `--force` teardown
`remove_existing_deployment()` runs at `src/cli/cli_main.py:295` — 38 lines earlier. A
refusal raised in staging therefore fires *after* the operator's running deployment is gone.

That is the exact defect the `cli-create-preflight` capability exists to prevent: "A
`--force` teardown MUST NOT run until the replacement deployment is known to be both valid
and constructible" (`openspec/specs/cli-create-preflight/spec.md:6-12`). That spec also
records the open general case — every stage of `prepare_deployment_files()` runs after the
teardown — as `fasrc/archi#294`. Adding a new refusal route to staging would enlarge #294.

`config_manager.validate_configs()` runs at `src/cli/cli_main.py:224`, above the teardown,
and `_validate_chat_app_config` (`src/cli/managers/config_manager.py:179`) is already the
per-service validator for this exact config block. It already raises `ValueError` with
"Missing required field: '<dotted.path>'" messages, and `cli_main` converts a `ValueError`
into a non-zero `ClickException` (`src/cli/cli_main.py:355-365`). The new check joins it.

`_stage_evaluation_config` keeps its own `mcp_config_path` validation unchanged. This change
adds nothing there.

## D3. A shared constant, and two different questions about the same path

`LIVE_AGENT_CONFIG_PATH` currently lives in `src/interfaces/chat_app/evaluation_console.py`,
which imports `flask`. The CLI must not import flask to validate a config, so the constant
moves to a new `src/utils/evaluations_config.py` that imports `pathlib` only. Both sides
import it from there. Duplicating the literal in two files was the alternative and was
rejected: the whole defect in #330 is two places disagreeing about one path.

The two sides then ask **different questions** about that path, on purpose:

- **The CLI, on the host**, compares normalized paths only. `archi create` runs outside the
  container, where `/root/archi/configs/config.yaml` normally does not exist, so
  `os.path.samefile` would raise and a hard link or bind mount cannot be detected at all.
  `Path(...).resolve() == Path(LIVE_AGENT_CONFIG_PATH).resolve()` is what the host can
  honestly answer, and it catches the case #330 is about: an operator who copies the value
  out of `configuration.md`.
- **The seam, in the container**, keeps `_is_live_agent_config`
  (`src/interfaces/chat_app/evaluation_console.py:64`) with its `samefile` inode check. Only
  there do both files exist, and only there can an alias be seen.

The create-time check is therefore a strict subset of the runtime check. It is a fast
diagnostic, not a replacement, and the seam stays the authority. Deleting the runtime check
because the CLI now looks would be wrong: a deployment can be edited in Postgres after
create (this repo seeds the running config from `config.yaml` at deploy), so the seam must
still refuse what it is handed.

## D4. Rendering `null` is safe, and the behavior change is honest

Only `evaluation_console.py` reads `agent_config_path` out of the deployed configuration
(`grep -rn agent_config_path src/`; `src/evaluation/qa/console.py` receives it as a
constructor argument, not from config). `build_evaluation_service` returns early when
`enabled` is not exactly `True` (`:90`), so a disabled console never reads the key and
`null` reaches nothing. When the console is enabled, create has already refused a missing
key, so the render cannot produce `null` for an enabled console.

`| default(none, true) | tojson` is copied from the sibling `mcp_config_path` line
(`src/cli/templates/base-config.yaml:127`), which already renders `null` for an unset key
and is asserted as `None` by the existing test. Reusing that idiom keeps one rendering
convention in the block. Note that the quotes must be dropped from the template line: a
quoted `"{{ ... | tojson }}"` would render the string `"null"`, which the seam would accept
as a filename.

The change makes `archi create` fail for a config that used to succeed. That is intended
and is not a regression: the deployment it used to produce had a console that could never
start. The failure is loud, names the key, and happens before any teardown, so the operator
keeps their running deployment and gets a one-line fix.

## D5. Docs

`docs/docs/configuration.md:171` shows `agent_config_path: /root/archi/configs/config.yaml`
inside the worked example, and `:193-194` calls it the default. An operator who follows the
page verbatim gets the dead console. The example changes to a redacted copy — a path under
the same mounted config directory, for example `/root/archi/configs/config.eval.yaml` — and
the prose states that the key is required when `enabled` is `true`, that it has no default,
and that the live deployment config is refused because every run publishes a copy of the
named file into the served workspace.
