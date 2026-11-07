# Redact secret-bearing keys before the QA workflow persists the agent config

## Why

Every QA evaluation run copies the named agent config **verbatim** into its run directory.
`src/evaluation/qa/workflow.py:367` does `write_yaml(run_dir / "agent_config.resolved.yaml",
config)`, and `:369` hashes that file. A retry copies the file into its successor directory
(`workflow.py:995-1003`, `copied_artifacts`), a console "continue" re-reads it (`src/evaluation/qa/console.py:531`),
and the console serves run directories (`history.get_run`, see #563). The run directory sits on
a host mount. A secret in the named config is therefore written to disk once per run, copied
once per retry, and readable by any console caller who can read runs.

Measured at `e58a7ada` (this branch's base): a `composite()` run whose config carries
`providers.fake.api_key: SENTINEL-APIKEY`, a `headers.Authorization: Bearer SENTINEL-AUTH`
inside `extra_kwargs`, and `services.postgres.password: SENTINEL-PG` persists **all three**
sentinels in `agent_config.resolved.yaml`. They must persist **none**.

The fork guard in `src/interfaces/chat_app/evaluation_console.py:40` (`_is_live_agent_config`)
refuses only the live deployment config path. It does not redact the file that is named.

This is issue #562, child 2 of #320 (split with the operator on 2026-09-27). The decision on
the issue is: **redact at the write, with a tested key rule in a helper module**; the validator
and the `_is_live_agent_config` refusal stay. #371 stages an already-redacted operator file;
this change is defense in depth for whatever file is named.

## What Changes

- New helper module `src/evaluation/qa/redaction.py`: a key rule `is_secret_key(name)`, a
  fixed marker `REDACTED = "[redacted]"`, and `redact_agent_config(config) -> dict`. The
  function returns a new structure (the input is not mutated), keeps every key and the key
  order, and replaces secret values with the marker. It is idempotent.
- `QAWorkflow.run` redacts the loaded config **once**, right after it is loaded, and uses the
  redacted config for everything after that: the snapshot write, the hash, the agent runtime,
  the vector store, and the corpus readings. A first run, a continued run, and a retry
  therefore all execute from the same content that the snapshot records (design D1).
- The snapshot hash is the hash of the redacted file. No new comparison rule is needed: every
  existing comparison (`workspace.py:201`, the continue check at `workflow.py:318`, the retry
  copy) reads the file that was written.
- A drift-guard test pins that the rule covers every hint in the chat app's own secret list
  (`src/interfaces/chat_app/config_fingerprint.py:23`, `_SENSITIVE_HINTS`).
- Docs: `docs/docs/evaluation.md` and `docs/docs/cli_reference.md` say the snapshot is
  redacted.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `qa-evaluation-trial`: adds one requirement (ADDED): the persisted agent-config snapshot
  carries no secret-bearing value, and every run phase executes from that snapshot's content.

## Impact

- Code: `src/evaluation/qa/redaction.py` (new), `src/evaluation/qa/workflow.py` (one call
  site in `run`).
- Tests: `tests/unit/evaluation/qa/test_redaction.py` (new),
  `tests/unit/evaluation/qa/test_workflow.py`, `tests/unit/evaluation/qa/test_live_workflow.py`.
- Docs: `docs/docs/evaluation.md`, `docs/docs/cli_reference.md`.
- Runtime: the agent runtime already reads its credentials from the environment
  (`read_secret` in `src/archi/providers/base.py:97` and
  `src/archi/utils/vectorstore_connector.py:17`), not from the config mapping. A config that
  puts a credential inline under a secret-named key will now run without it. That fails
  loudly at the provider, which is the intended trade (design D1).
- No change to `config_fingerprint.py`, to the validator, or to `_is_live_agent_config`.
