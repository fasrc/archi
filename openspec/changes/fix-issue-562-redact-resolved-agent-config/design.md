# Design — redact the persisted QA agent config (#562)

Anchors are at `origin/dev` `e58a7ada`.

## D1. Redact once, at load, and run from the redacted config

`QAWorkflow.run` gets `config` from `load_agent_inputs` (or from `_resolved_agent_inputs`,
`workflow.py:348-352`). Replace it with `redact_agent_config(config)` **before** the
`write_yaml` at `:367`, and use that redacted object for every later read in `run`
(`chat = config[...]` at `:371`, `LazyVectorstore(config)`, `corpus_provenance.start_readings`,
and the agent runtime construction).

Why not redact only the copy that is written, and run from the original: a continued run
re-reads the snapshot (`console.py:531` passes `run_dir / "agent_config.resolved.yaml"` as the
agent config), and a retry loads it (`workflow.py:953-956`). If the first run used the original
while later phases use the redacted snapshot, one run id would execute two different systems
under test. That is the exact defect the continue path's comment (`console.py:522-525`) and the
spec scenario "Paused runs resume against frozen inputs" forbid. With D1 the snapshot is, byte
for byte, what every phase ran.

Cost: a credential written inline under a secret-named key no longer reaches the runtime. The
runtime does not read such values today — providers build `ProviderConfig` from provider
defaults and read keys through `api_key_env` → `read_secret` (`src/archi/providers/__init__.py:277`,
`base.py:92-97`); Postgres reads `read_secret("PG_PASSWORD")` (`vectorstore_connector.py:17`).
If an operator config does rely on an inline credential, the run fails at the provider, loudly,
and the snapshot shows `[redacted]` in that place. A silent leak is the worse failure.

Do not mutate the input mapping: `_resolved_agent_inputs` can be shared with the caller.

## D2. The key rule: segment match, not substring match

`config_fingerprint._SENSITIVE_HINTS` is a **substring** list (`"key"`, `"token"`, `"secret"`,
`"password"`, `"authorization"`). It is fine for a log line. It is wrong here, because under D1
the runtime executes from the redacted config: substring `"token"` matches `max_tokens`, which
the Anthropic provider reads (`src/archi/providers/anthropic_provider.py:92`), and substring
`"key"` matches `keywords`. Redacting those changes the system under test.

`is_secret_key(name)`:

1. `name = str(name)`. Split camelCase: insert `_` between a lower-case letter or digit and an
   upper-case letter, and between an upper-case run and an upper-case letter that starts a
   lower-case word (`apiKey` → `api_Key`, `APIKey` → `API_Key`). Lower-case the result. Replace
   every character that is not `[a-z0-9]` with `_`. Split on `_` and drop empty segments.
2. The key is secret when **any** segment:
   - is in `SECRET_SEGMENTS = {"password", "passwd", "passphrase", "secret", "secrets",
     "token", "key", "keys", "apikey", "apikeys", "authorization", "authtoken", "bearer",
     "cookie", "cookies", "credential", "credentials", "session", "csrf", "xsrf", "dsn"}`, or
   - ends with one of `SECRET_SUFFIXES = ("password", "passwd", "secret", "token", "key",
     "keys", "apikey", "authorization", "credential", "credentials", "cookie", "authtokens",
     "accesstokens", "refreshtokens", "apitokens", "bearertokens")`, or
   - starts with one of `("password", "passwd", "secret")`.

Note: the suffix `key` matches glued names (`privatekey`, `accesskey`, `encryptionkey`). The
chat app's substring rule masks those names too, so this rule must match them. Note: `tokens` (plural) is **not** a secret segment, and it is not a
suffix except in the five glued credential forms listed above. It is the count and limit segment
(`max_tokens`, `prompt_tokens`). `keys` and `secrets` (plural) **are** secret segments.
Examples are `provider_api_keys` (a session key at `src/interfaces/chat_app/app.py:4563`)
and the `secrets:` block.

Tests pin the rule with these two tables. Every row is one parametrized case.

Must be secret: `api_key`, `apiKey`, `APIKey`, `API_KEY`, `x-api-key`, `X-Api-Key`,
`apikey`, `key`, `keys`, `provider_api_keys`, `secret`, `secrets`, `client_secret`,
`clientSecret`, `secret_key`, `secretkey`, `password`, `PG_PASSWORD`, `POSTGRES_PASSWORD`,
`PGPASSWORD`, `admin_password`, `db_passwd`, `token`, `access_token`, `accessToken`,
`accesstoken`, `auth_token`, `authtoken`, `hf_token`, `HUGGING_FACE_HUB_TOKEN`,
`Authorization`, `proxy-authorization`, `cookie`, `Set-Cookie`, `session`, `credentials`,
`encryption_key`, `private_key`, `csrf_token`, `dsn`, `bearer`, `privatekey`, `PRIVATEKEY`,
`accesskey`, `sessionkey`, `signingkey`, `encryptionkey`, `masterkey`, `authtokens`,
`accesstokens`, `refreshtokens`, `monkey` (an accepted over-match).

Must not be secret: `max_tokens`, `preferred_max_tokens`, `prompt_tokens`, `input_tokens`,
`output_tokens`, `total_tokens`, `per_result_tokens`, `additional_special_tokens`,
`tokenizer`, `keywords`, `keyword`, `auth`, `auth_roles`, `auth_method`, `authorize_url`,
`agent_class`, `default_provider`, `default_model`, `base_url`, `embedding_name`,
`collection_name`, `primaryauthors`, `coauthors`, `tokens`, `maxtokens`.

Over-matching is accepted where the runtime does not read the key. For example,
`token_ttl_days` and `session_lifetime_days` are chat-app settings. A missed secret is a
persisted credential. An over-matched non-runtime key costs one value that nobody reads
from the snapshot.

## D3. What a secret value becomes

`redact_agent_config(value)` walks mappings and lists and returns new objects. The key order
stays the same (`write_yaml` dumps with `sort_keys=False`).

- A mapping entry whose key is secret (D2):
  - scalar `str` (non-empty), `int`, or `float` (not `bool`) → `REDACTED`;
  - `None`, `""`, and `bool` → unchanged. These hold no secret, and `None` must stay `None`
    so that "absent" keeps its meaning;
  - a mapping or a list → recurse in **secret mode**. In secret mode, every non-empty
    `str`, `int`, or `float` leaf becomes `REDACTED`, whatever its key. Keys stay.
- A mapping that has a `name` or `key` entry whose string value is secret per D2 (the header
  list form `{name: Authorization, value: "Bearer x"}`) → its `value` entry is redacted as if
  its own key were secret.
- Every string leaf outside secret mode: find **every** URL in the string with the unanchored
  pattern `[A-Za-z][A-Za-z0-9+.-]*://[^\s]+`, and parse each match with
  `urllib.parse.urlsplit`. When `.password` is not `None`, rebuild that match with the
  password replaced by `URL_REDACTED = "redacted"` (no brackets: `urlsplit` reads `[...]` in a
  netloc as an IPv6 literal and raises `ValueError`, so `[redacted]` would break the second,
  idempotent pass). When `urlsplit` raises `ValueError` on a match, replace the **whole** match
  with `REDACTED` (fail closed), and keep the scheme, user, host, port, path, query, and
  fragment unchanged. `urlsplit` splits the netloc at its **last** `@`, which is how clients
  read it, so a password that contains `@` is redacted whole. Test rows (each one exact):
  - `postgresql://u:s7@db:5432/x` → `postgresql://u:redacted@db:5432/x`;
  - `dsn=postgresql://u:pw@h/x` → `dsn=postgresql://u:redacted@h/x` (an embedded URL);
  - `https://u:p1@h one https://v:p2@h` → `https://u:redacted@h one https://v:redacted@h`;
  - `postgresql://u:p@ss@db/x` → `postgresql://u:redacted@db/x`;
  - `https://user@h/x` and `https://h/x` → unchanged (no password);
  - `https://u:pw@[::1` → `[redacted]` (unparseable, fail closed).
  Each row's output is unchanged by a second pass.
- Mapping keys that are not strings are matched as `str(key)` and stay unchanged in the output.

**Idempotence.** `redact(redact(x)) == redact(x)`, and `yaml.safe_dump` of both is byte
identical. The continue path verifies the snapshot digest (`workflow.py:318`), re-reads the
snapshot, redacts it again, and rewrites it. D1 plus idempotence keep that digest unchanged.

## D4. Hashing and comparisons

No new code. `config_hash = sha256_file(...)` at `:369` already hashes the written (now
redacted) file. `workspace.py:201`, the continue check, and the retry copy read that same file.
A test pins that the manifest digest equals `sha256` of the persisted redacted bytes, and that a
continue leaves the digest unchanged.

## D5. The drift guard

`tests/unit/evaluation/qa/test_redaction.py` imports
`src.interfaces.chat_app.config_fingerprint._SENSITIVE_HINTS` (already imported by
`tests/unit/test_config_fingerprint.py`) and asserts `is_secret_key(hint)` and
`is_secret_key(f"api_{hint}")`, and the glued forms `is_secret_key(f"private{hint}")` and
`is_secret_key(f"access{hint}")` for every hint. The glued forms fail the gate when the chat
app's substring rule masks a name that the segment rule misses. A hint added to the chat app's list that this
rule misses fails the gate.

The issue also names `src/utils/deployment_record.py`. At `e58a7ada` that file names no secret
keys (`grep -in 'secret\|password\|token' src/utils/deployment_record.py` prints nothing), so
there is nothing there to guard against.

## D6. Out of scope

- Run directories written before this change keep their unredacted snapshot, and a retry of
  such a parent copies it as is. Hiding run files from VIEW-only callers is #563. Deleting old
  run directories is an operator action.
- Staging the operator's file (#371), the viewer split (#563), and any change to
  `config_fingerprint.py`'s own masking.
- A password with an unencoded `/` (`postgresql://u:pa/ss@db/x`). That URL is invalid, and
  `urlsplit` and clients alike read `pa` as the host part, so no URL rule matches it.
- Secret values under keys whose names look harmless (for example a bearer string under
  `extra_kwargs.foo`). No key rule can catch these. URL user-info (D3) is the one value shape
  that is matched.

## D7. Order of work

Task 1 builds the helper and its tables in isolation. Task 2 wires it into `run` with the
sentinel, retry, and continue tests. The red for task 2 is the probe in `tasks.md`
`## Commands`. Task 3 changes the docs.
