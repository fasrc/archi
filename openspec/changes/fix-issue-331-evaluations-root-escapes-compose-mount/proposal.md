# Refuse a relocated evaluations root before the deployment is destroyed

## Why

`services.chat_app.evaluations.root` is a configurable knob
(`src/cli/templates/base-config.yaml:125`), and the compose template bind-mounts host
`./data/evaluations` at the fixed container path `/root/archi/evaluations`
(`src/cli/templates/base-compose.yaml:255`) no matter what that knob says. Nothing in the
tree reconciles the two. `build_evaluation_service` reads the configured root and hands it
straight to the service (`src/interfaces/chat_app/evaluation_console.py:112`), and
`_stage_evaluation_config` validates `mcp_config_path` only
(`src/cli/managers/templates_manager.py:603-627`).

So a root outside the mounted path — `evaluations.root: /data/evaluations`, a typo, an
override copied between hosts — puts datasets, human approvals, job records and the whole
run history in the container's overlay filesystem. `archi create --force`, which is the
standard redeploy (`deploy/fasrc-dev` runs it), recreates the container and erases every one
of them. Nothing warns at create time and nothing warns at runtime.

Runtime cannot catch this. The storage probe added for #328 refuses a root the container
cannot write; an overlay root **is** writable, so the console comes up, works, and loses the
catalog on the next redeploy. The only place the mistake is still cheap is before the
teardown.

The refusal therefore belongs in `ConfigurationManager._validate_chat_app_config`
(`src/cli/managers/config_manager.py:179`), which `create` calls at
`src/cli/cli_main.py:224` — before any destructive step, as
`openspec/specs/cli-create-preflight/spec.md` requires. Validating in the template staging
instead would refuse *after* the `--force` teardown had already destroyed the deployment,
which is the exact defect #287 closed.

## What Changes

- A new seam module `src/utils/evaluations_root.py` holds the mounted container path as
  `EVALUATIONS_MOUNT_PATH` and one pure function, `validate_evaluations_root(chat_app_config)`,
  that raises `ValueError` naming both the configured root and the mount.
- `ConfigurationManager._validate_chat_app_config` calls it. That is a two-line call site
  plus an import; the logic and all of its coverage live in the new module.
- The comparison is lexical and by path component, never by string prefix and never against
  the host filesystem. `/root/archi/evaluations-backup` starts with the mount string and is
  outside the mount; `/root/archi/evaluations/../elsewhere` normalizes to a path outside it;
  and `Path.resolve()` is wrong here because the value is a *container* path
  that would be resolved against the host's symlinks.
- The check is gated on `evaluations.enabled is True`, matching the gate the console seam
  itself applies (`src/interfaces/chat_app/evaluation_console.py:89`). A disabled console
  writes nothing, so a leftover root is inert and refusing it would break deployments that
  carry one today for no data-loss benefit.
- A regression test proves a default configuration renders a byte-identical compose file and
  is accepted, and a second test proves `EVALUATIONS_MOUNT_PATH` still equals the mount
  target the compose template renders, so moving the mount cannot leave the validator
  guarding a path that no longer exists.
- `docs/docs/configuration.md` states the constraint where the knob is documented.

The mount is **not** moved to follow the configured root. That alternative and the reason it
lost are in `design.md` (D1).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `qa-evaluation-trial`: gains one requirement, "A relocated evaluations root is refused
  before the deployment is destroyed". The capability is archived under
  `openspec/specs/qa-evaluation-trial/`, so this delta ADDS a requirement to it rather than
  modifying an existing one. The existing "Evaluations console behind a config toggle"
  requirement is untouched: it governs what the runtime seam does with a root it is given,
  and this one governs whether that root ever reaches a deployment.

## Impact

- `src/utils/evaluations_root.py` — new, about 40 lines, fully covered by its own tests.
- `src/cli/managers/config_manager.py` — one import and one call inside
  `_validate_chat_app_config`. The file is black-clean at `origin/dev` (verified
  2026-08-27), so the edit does not reflow untouched lines.
- `tests/unit/test_evaluations_root_validation.py` — new.
- `tests/unit/test_evaluation_config.py` — one added render-drift test. Existing tests are
  unmodified.
- `docs/docs/configuration.md` — one note.
- Three commands validate configs, so all three refuse: `create`
  (`src/cli/cli_main.py:224`), `restart` (`:596`) and `evaluate` (`:873`). A `restart` after
  a hand-edited config is refused too, which is wanted — `restart` re-renders as well.
- **Not** edited: `src/cli/templates/base-compose.yaml` and
  `src/cli/templates/base-config.yaml` (the contract is preserved on purpose),
  `src/interfaces/chat_app/evaluation_console.py` (the runtime seam cannot detect an overlay
  root), and `src/cli/managers/templates_manager.py` (it runs after the teardown).
- **Merge note.** PR #367 (issue #330) adds `src/utils/evaluations_config.py` on its own
  branch off the same `origin/dev`. This change deliberately uses a different module name so
  the two nightly branches do not collide add/add. Both wire into
  `_validate_chat_app_config`; whichever merges second resolves a small, obvious conflict at
  the call site.
- **Upstream parity, measured.** At the ported pin `bebfbe56` upstream has the identical
  split — `base-config.yaml:134` configurable root against a fixed `base-compose.yaml:174`
  mount — and its helm chart repeats it, mounting the chatbot PVC `subPath: evaluations` at
  the same fixed `/root/archi/evaluations`
  (`src/cli/templates/helm/templates/chatbot/deployment.yaml:71-73` at that pin). The fork
  carries no helm templates, so the fork fix is compose-scoped. Reporting this on
  archi-physics/archi PR #608 is a human action outside this change; the finding is recorded
  here and in `design.md` (D7) so it survives the merge.
