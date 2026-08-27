# Refuse an enabled evaluations console at create time instead of rendering a dead one

## Why

`base-config.yaml` renders the one `agent_config_path` value the runtime seam refuses.
The template line is

```
agent_config_path: "{{ services.chat_app.evaluations.agent_config_path | default('/root/archi/configs/config.yaml', true) }}"
```

(`src/cli/templates/base-config.yaml:126`), and that default string is exactly
`LIVE_AGENT_CONFIG_PATH` (`src/interfaces/chat_app/evaluation_console.py:35`).
`build_evaluation_service` refuses the live config by file identity
(`src/interfaces/chat_app/evaluation_console.py:101`, via `_is_live_agent_config` at `:64`).
In the container the two paths are the same file, so `samefile` always matches and the
refusal always fires.

The result is a dead console by recipe. An operator sets
`services.chat_app.evaluations.enabled: true` and nothing else. `archi create` exits 0,
every container starts, and the console never appears — no `/evaluations` route, no nav
link. The only evidence is one error line in the chatbot container log. There is no
rendered default that ever works.

Three statements in the tree disagree with the template:

- The seam docstring says "`agent_config_path` has no default"
  (`src/interfaces/chat_app/evaluation_console.py:67`).
- `docs/docs/configuration.md:171` uses the refused path as the working example.
- `docs/docs/configuration.md:193-194` documents it as the default.

A test also pins the defect: `test_generated_evaluation_console_requires_explicit_enablement`
asserts the rendered value is `/root/archi/configs/config.yaml`
(`tests/unit/test_evaluation_config.py:55`).

The refusal itself is correct fork policy and stays. Each run snapshots the named config
into a host-mounted workspace the console then serves, so the live config must never be
the snapshot source. The defect is the template default, which guarantees the refusal.

## What Changes

Issue #330 plan step 1 asks for one of two mechanisms. This change takes **(b), validate at
create time**, and also removes the template default, because (a) alone cannot satisfy the
issue's own acceptance criteria. See `design.md` D1.

- **New shared module `src/utils/evaluations_config.py`.** It owns `LIVE_AGENT_CONFIG_PATH`
  and one function, `validate_evaluations_config(chat_app_config)`, which raises `ValueError`
  naming `services.chat_app.evaluations.agent_config_path` when `enabled` is exactly `True`
  and the path is missing, blank, or names the live deployment config. The module imports
  `pathlib` only, so both the CLI and the chat app can import it.
- **`src/interfaces/chat_app/evaluation_console.py`** imports `LIVE_AGENT_CONFIG_PATH` from
  the new module instead of defining it, so one constant serves both sides. Its own
  inode-level `_is_live_agent_config` check stays: only the container can ask that question.
- **`src/cli/managers/config_manager.py`** calls the new validator from
  `_validate_chat_app_config` (`:179`). That seam runs inside
  `config_manager.validate_configs()` at `src/cli/cli_main.py:224` — above the `--force`
  teardown at `:295`. A refusal there costs the operator nothing.
- **`src/cli/templates/base-config.yaml:126`** drops the default and renders the key the way
  the sibling `mcp_config_path` renders: `| default(none, true) | tojson`, so an unset key
  becomes `null`. The docstring's "no default" then describes the template too.
- **`tests/unit/test_evaluation_config.py:55`** expects `None` for `agent_config_path`.
- **`docs/docs/configuration.md`** shows a redacted-copy path in the example and states that
  the live deployment config is refused and that the key has no default.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `qa-evaluation-trial`: gains one requirement covering create-time refusal and the rendered
  default. The capability is archived under `openspec/specs/qa-evaluation-trial/`
  (from `openspec/changes/archive/2026-08-21-port-live-eval-trial/`). The existing
  requirement "Evaluations console behind a config toggle" is **not** modified here — the
  unarchived change `fix-issue-328-eval-console-storage-fail-closed` already carries a
  `MODIFIED` delta for it, and a second one would conflict at archive time. The new ground
  is create-time and render-time behavior, which that requirement does not cover, so this
  delta adds a requirement instead.

## Impact

- `src/utils/evaluations_config.py` — new, about 40 lines, fully unit tested.
- `src/cli/managers/config_manager.py` — one call site in an existing method. The file is
  black-clean at `origin/dev`, so an in-place edit does not reflow it.
- `src/interfaces/chat_app/evaluation_console.py` — one import replaces one constant
  assignment, plus a docstring line. Also black-clean.
- `src/cli/templates/base-config.yaml` — one line.
- `src/interfaces/chat_app/app.py` is **not** edited. The unit suite does not import it.
- `src/evaluation/qa/**` is **not** edited.
- Behavior change for existing deployments: a config that sets `enabled: true` and relies on
  the default now fails `archi create` with a message instead of deploying a dead console.
  That console never worked, so no working deployment breaks. `design.md` D4 records the
  reasoning.
- Coordinates with #320, whose plan step 2 also edits `base-config.yaml` (RBAC role
  mapping) and whose enable-on-dev step needs this diagnostic to be honest. The two hunks
  are in different blocks of the template.
