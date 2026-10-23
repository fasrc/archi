## ADDED Requirements

### Requirement: The QA workflow SHALL persist an agent-config snapshot with every secret-bearing value redacted and SHALL run every phase from that snapshot's content

Before `QAWorkflow.run` writes `agent_config.resolved.yaml`, it SHALL replace the loaded
config with the output of `redact_agent_config` from `src/evaluation/qa/redaction.py`. That
function returns a new structure. It keeps every key and the key order, and it replaces each
non-empty string or numeric value under a secret-named key with the fixed marker
`[redacted]`. It does the same for every such leaf inside a mapping or list under a
secret-named key, for the `value` of a `{name|key: <secret name>, value: ...}` pair, and for
the password part of the user-info of every URL in any string (that
password becomes `redacted`, without brackets, so the URL stays parseable). A key is secret-named when a segment of its
normalized name matches the segment rule in the change's design (D2), which does not match
count or limit keys such as `max_tokens`. The function SHALL be idempotent. The first run, a
continued run, and a retry SHALL all execute from the redacted content. The manifest digest of
`agent_config.resolved.yaml` SHALL be the digest of the redacted bytes.

#### Scenario: No sentinel secret reaches the run directory

- **WHEN** a `composite()` run loads a config that carries distinct sentinel values under
  `api_key`, a `headers.Authorization` inside `extra_kwargs`, `services.postgres.password`,
  a `secrets:` mapping, and a `postgresql://user:SENTINEL@host/db` URL under the non-secret key
  `database_url`
- **THEN** no sentinel string appears in `agent_config.resolved.yaml` or in any other file in
  the run directory, each key-based place equals `[redacted]`, and `database_url` equals
  `postgresql://user:redacted@host/db`

#### Scenario: The runtime receives the redacted config

- **WHEN** the same run constructs the agent runtime
- **THEN** the config mapping that the runtime receives equals the parsed content of
  `agent_config.resolved.yaml`, and `max_tokens` and the other non-secret values are unchanged

#### Scenario: A retry runs from the redacted snapshot

- **WHEN** a retry of that run re-executes a failed attempt, with `load_agent_inputs`
  reading the parent's snapshot file
- **THEN** the retry completes, the config that its runtime receives contains no sentinel, and
  the successor's `agent_config.resolved.yaml` is byte-identical to the parent's

#### Scenario: A continue keeps the digest

- **WHEN** a run paused as `attention_required` is continued from its frozen snapshot
- **THEN** the continue succeeds and the recorded digest of `agent_config.resolved.yaml` is
  unchanged

#### Scenario: The rule covers the chat app's own secret hints

- **WHEN** the drift-guard test iterates over `_SENSITIVE_HINTS` in
  `src/interfaces/chat_app/config_fingerprint.py`
- **THEN** `is_secret_key(hint)`, `is_secret_key("api_" + hint)`, `is_secret_key("private" + hint)`,
  and `is_secret_key("access" + hint)` are true for every hint
