# Design — refuse a relocated evaluations root

## D1. Validate the root; do not move the mount

Issue #331 offers two mechanisms. (a) refuse a root that is not under the fixed mount.
(b) derive the compose mount target from the configured root.

(a) wins, and it is what this change implements.

(b) changes the template contract in three places that currently agree on the literal
`/root/archi/evaluations`: the compose mount (`src/cli/templates/base-compose.yaml:255`),
the rendered default root (`src/cli/templates/base-config.yaml:125`) and the runtime default
`DEFAULT_EVALUATION_ROOT` (`src/interfaces/chat_app/evaluation_console.py:34`). The QA-eval
fixtures hard-code the same path for the evaluator MCP server
(`tests/unit/test_qa_eval_fixtures.py:66`). Moving the mount needs a migration note for
every deployment that already has a catalog under the old path, and it does not remove the
failure — a root that is not a usable absolute container path still breaks, only later and
more quietly.

(a) preserves every one of those agreements and converts a silent redeploy-time data loss
into a create-time sentence. The knob keeps its useful range: any path *beneath* the mount
still works, which covers the real use ("keep two catalogs side by side") without leaving
the volume.

## D2. The check belongs before the teardown, so it goes in the config validator

`openspec/specs/cli-create-preflight/spec.md` requires that every step capable of refusing a
deployment run before any destructive action. The seams available here sit on opposite sides
of that line:

- `ConfigurationManager.validate_configs` runs at `src/cli/cli_main.py:224`, above the
  `--force` teardown. **Before.**
- `TemplateManager._stage_evaluation_config` (`src/cli/managers/templates_manager.py:603`)
  runs inside `prepare_deployment_files()`, below the teardown — that whole stage is the
  subject of the open issue #294. **After.**
- `build_evaluation_service` (`src/interfaces/chat_app/evaluation_console.py:103`) runs in
  the container, long after the deployment exists. **After, and in the wrong process.**

Only the first can refuse without cost. Putting the check in the staging step would mean the
operator loses a running deployment and then reads a message about a typo, which is the
defect #287 closed and this change must not reopen.

The concrete site is `_validate_chat_app_config` (`src/cli/managers/config_manager.py:179`),
which already returns early unless `chatbot` is among the enabled services — the right gate,
since a deployment with no chat app has no console.

## D3. Compare path components, never a string prefix

`"/root/archi/evaluations-backup".startswith("/root/archi/evaluations")` is `True`, and that
root is outside the mount. A prefix test therefore accepts exactly the kind of near-miss an
operator makes.

The implementation normalizes lexically with `posixpath.normpath`, wraps the result in
`PurePosixPath`, and accepts only when the candidate equals the mount or lists the mount
among its `.parents`. That is a component comparison, so `evaluations-backup` is refused,
`evaluations/trial-a` is accepted, a trailing slash is harmless, and
`/root/archi/evaluations/../elsewhere` normalizes to `/root/archi/elsewhere` and is refused.

Two things it deliberately does not do:

- **No `Path.resolve()`, and no filesystem access at all.** The value is a path *inside the
  container*. Resolving it on the host would follow the host's symlinks and test the host's
  directories, both of which are unrelated to where the container will write. The check must
  be a pure function of the string.
- **No `PurePosixPath.is_relative_to()` alone.** It gives the same answer here, but
  `normpath` first is what makes traversal safe; without it, `is_relative_to` on an
  unnormalized path is a string-shaped comparison again.

A relative root is refused for the same reason it cannot be proven safe: the compose file
pins no working directory for the chatbot container, so `evaluations/` resolves against
whatever the image's `WORKDIR` happens to be, and that is not a guarantee this validator can
make.

## D4. Only when the console is enabled

The check runs only when `services.chat_app.evaluations.enabled` is exactly `True`.

The console seam applies the same gate before it does anything else
(`src/interfaces/chat_app/evaluation_console.py:89`): with the toggle off, no catalog, no
history and no job directory is ever created, so a stale or wrong root writes nothing and
loses nothing. Refusing it would fail `archi create` for deployments that carry a leftover
root today with the console off — a new outage in exchange for no protection.

The gate holds at the moment that matters. An operator who flips `enabled: true` must
redeploy for it to take effect (the running config lives in Postgres, seeded at deploy), and
that redeploy runs this check.

## D5. Runtime cannot substitute for this check

Change fix-issue-328 made the console fail closed when its storage is unusable, probing that
each catalog directory accepts a write. That guard is real and it does not help here: a root
in the container's overlay filesystem is perfectly writable. The console registers, imports
datasets, records approvals and run history, and every one of them disappears when
`archi create --force` recreates the container. There is no signal at runtime to catch,
which is why the refusal has to happen while the deployment is still being described rather
than while it is running.

## D6. The message names both paths

The refusal states the configured root, the mounted path, and the consequence. An operator
who reads only the configured root has to go find the mount in the compose template to know
what to change it to; one who reads only "invalid root" has to read code. The message also
says that a path beneath the mount is allowed, because that is the fix in almost every case.

## D7. Guarding a constant that could drift, and the upstream report

`EVALUATIONS_MOUNT_PATH` in the new module is a fourth copy of `/root/archi/evaluations`.
Importing the existing `DEFAULT_EVALUATION_ROOT` from
`src/interfaces/chat_app/evaluation_console.py` would avoid the copy at an unacceptable
price: that module pulls the chat app's import chain into `archi create`, a CLI that must
not depend on Flask to validate a YAML file.

The copy is held honest by a test that renders `base-compose.yaml` and asserts the chatbot
service's evaluations volume ends in `EVALUATIONS_MOUNT_PATH`. If someone moves the mount,
that test fails rather than leaving the validator quietly guarding a path that no longer
exists.

**Upstream.** At pin `bebfbe56` upstream carries the same split, in both renderers:
`base-config.yaml:134` exposes the configurable root, `base-compose.yaml:174` fixes the
compose mount, and `helm/templates/chatbot/deployment.yaml:71-73` mounts the chatbot PVC
`subPath: evaluations` at the same fixed `/root/archi/evaluations`. The fork has no helm
templates, so this fix covers the fork completely; an upstream fix would need the helm path
too. Posting that on archi-physics/archi PR #608 is a human action and is not part of this
change — it is recorded here so it survives the merge.
