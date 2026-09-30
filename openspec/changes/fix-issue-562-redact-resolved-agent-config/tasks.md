## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-562-redact-resolved-agent-config` exists, cut from
      `origin/dev` at `e58a7ada`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-562-redact-resolved-agent-config --strict` passed on
      the host. The `openspec` CLI is **not usable in this container**. Do not run it, and do
      not add a task that runs it.
- [x] 0.3 The red is measured at `e58a7ada`: the probe in `## Commands` prints
      `['SENTINEL-APIKEY', 'SENTINEL-AUTH', 'SENTINEL-PG']`. After task 2 it must print `[]`.
      Baseline: `tests/unit/evaluation/qa/test_workflow.py` +
      `tests/unit/test_config_fingerprint.py` → **56 passed**.
- [x] 0.4 Every design decision is made in `design.md` (D1–D7): redact once at load and run
      from the redacted config, the segment rule and its two tables, what a secret value
      becomes, idempotence, the drift guard. Follow the design. Where the issue body and the
      design differ, the design wins.

## Rules that apply to EVERY task below

- Code goes in `src/evaluation/qa/redaction.py` (new) and in `src/evaluation/qa/workflow.py`
  (the one call site in `run`). Do not change `config_fingerprint.py`,
  `evaluation_console.py`, `runtime.py`, or any provider.
- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit, so
  a task that ends red can never be committed and the loop halts.
- Before you add a test, `grep -n 'def test_' <file>` and pick a name that is not used. The
  gate runs no linter. A duplicate `def test_...` silently replaces the older test.
- Never append tests at the end of an existing test file. In `test_workflow.py`, insert the
  marker line `# --- #562: redacted agent-config snapshot ---------------------------------`
  directly **above** `def test_run_and_score_do_not_decode_the_input_snapshot(`, and put the
  new tests between the marker and that def. In `test_live_workflow.py`, add the new method
  inside `class TestLiveWorkflow`, directly **above**
  `def test_high_cardinality_gate_persists_only_compact_attention_counts(`.
- After you insert a test, `git diff` the file and **read the trailing context line**. Make
  sure that the test above yours still ends with its own `assert`.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each commit,
  `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer.
- Do not write any file under `docs/` except the two edits in task 3. Write the PR body in
  `/tmp`, never in the repo. Do not add an entry to `docs/questions.md`.
- A module probe must import this checkout: run it with `PYTHONPATH=$PWD`, or it imports the
  installed package from another checkout.

## 1. The helper: key rule, value redaction, idempotence, drift guard

- [x] 1.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Create `tests/unit/evaluation/qa/test_redaction.py`. It imports
      `from src.evaluation.qa.redaction import REDACTED, is_secret_key, redact_agent_config`
      (this import alone is the red: the module does not exist). Add:
      - `test_secret_key_names_are_redacted`, parametrized over **every** name in the design
        D2 "Must be secret" table, asserting `is_secret_key(name) is True`;
      - `test_non_secret_key_names_are_kept`, parametrized over **every** name in the D2 "Must
        not be secret" table, asserting `is_secret_key(name) is False`;
      - `test_redaction_replaces_values_and_keeps_structure`: a nested fixture with
        `api_key: "s1"`, `max_tokens: 4096`, `extra_kwargs: {headers: {Authorization:
        "Bearer s2"}}`, `secrets: {PG_PASSWORD: "s3", nested: [ "s4", {x: "s5"} ]}`,
        `headers_list: [{name: "X-Api-Key", value: "s6"}, {name: "Accept", value: "json"}]`,
        `database_url: "postgresql://u:s7@db:5432/x"`, `dsn: "postgresql://u:s8@db/x"`,
        `api_token: null`, `password: ""`,
        `session: true`, and a non-string key `{1: "one"}`. Assert the exact expected output
        (every `sN` except `s7` → `REDACTED`; `dsn` is a secret key, so its whole value is
        `REDACTED`; `database_url` → `postgresql://u:redacted@db:5432/x`; `null`,
        `""`, `true`, `4096`, `"json"`, and `{1: "one"}` unchanged), that
        `list(out) == list(fixture)` at every mapping level you check (key order), and that
        the fixture itself is unchanged afterwards (compare with a `copy.deepcopy` taken before).
      - `test_url_password_is_redacted`, parametrized over **every** URL row in design D3
        (input string → exact output), each as the value of the non-secret key `endpoint`.
      - `test_redaction_is_idempotent`: for the same fixture,
        `redact_agent_config(redact_agent_config(x)) == redact_agent_config(x)`, and
        `yaml.safe_dump(..., sort_keys=False, allow_unicode=True)` of both is identical.
      - `test_rule_covers_chat_app_sensitive_hints`: import
        `from src.interfaces.chat_app.config_fingerprint import _SENSITIVE_HINTS`, assert it is
        non-empty, and for each hint assert `is_secret_key(hint)`,
        `is_secret_key("api_" + hint)`, `is_secret_key("private" + hint)`, and
        `is_secret_key("access" + hint)`.

      **Green.** Create `src/evaluation/qa/redaction.py` exactly per design D2 and D3. Put a
      module docstring that says why segment matching and not substring matching (`max_tokens`).
      Keep `SECRET_SEGMENTS`, `SECRET_SUFFIXES`, the prefix tuple, and the URL pattern as
      module constants. Use `urllib.parse.urlsplit` for the URL password, not a hand-written
      regex over the netloc. Run `PYTHONPATH=$PWD python -m pytest -q tests/unit/evaluation/qa/test_redaction.py`
      to green, then `bash scripts/gate.sh`, then commit
      (`redact secret-bearing keys in qa agent config (helper)`).

## 2. Wire it into the run: sentinel, runtime, retry, continue

- [x] 2.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** In `tests/unit/evaluation/qa/test_workflow.py`, in the marked section
      (see Rules), add:
      - `test_run_persists_no_secret_and_runs_from_the_snapshot`: use the `agent_inputs`
        fixture pattern, but monkeypatch `workflow_module.load_agent_inputs` with a config
        that carries distinct sentinels under the places named in the spec scenario "No
        sentinel secret reaches the run directory", plus `extra_kwargs.max_tokens: 4096`. Use
        an agent factory that records the `config` it is called with. Run `composite()`.
        Put the URL sentinel under the non-secret key `database_url`
        (`postgresql://user:SENTINEL-URL@host/db`). Assert: no sentinel string in **any** file
        under the run dir (read every file as bytes); the snapshot parses to a mapping in
        which each key-based sentinel place equals `"[redacted]"`, `database_url` equals
        `postgresql://user:redacted@host/db`, and `max_tokens` is `4096`; the recorded config equals
        `yaml.safe_load` of the snapshot; `manifest["artifacts"]["agent_config.resolved.yaml"]`
        equals `sha256` of the snapshot bytes.
      - `test_retry_runs_from_the_redacted_snapshot`: first run as above with one forced
        execution failure (see how
        `test_retry_creates_complete_successor_and_invokes_only_failed_phases` uses
        `failures=`). For the retry, monkeypatch `load_agent_inputs` with a loader that returns
        `yaml.safe_load(Path(config_path).read_text())` as the config (same spec, spec text,
        and pipeline class), so the retry really reads the parent's snapshot. Assert: the retry
        completes, the retry runtime was called with a config that contains no sentinel (walk
        it, or `json.dumps` it and search), and the successor's snapshot bytes equal the
        parent's.

      In `tests/unit/evaluation/qa/test_live_workflow.py`, add
      `test_continue_keeps_the_redacted_snapshot_digest` (see Rules for the location). Build
      a paused run as `_paused_at_live_gate` does, but first monkeypatch `load_agent_inputs`
      with a loader that returns a sentinel-bearing config when the path is
      `tmp_path / "agent.yaml"` and `yaml.safe_load` of the file otherwise. Record the
      snapshot digest from the paused manifest. Continue the run with the snapshot path as
      the agent config (as `console.py:531` does), `overwrite=True`,
      `pause_on_live_mismatch=True`, and `authorize_staged_invalid=True`. Assert that the run
      phase completes, the digest is unchanged, and no sentinel is in the snapshot.

      Run the new tests: they must fail on the sentinel assertions (not on an import or a
      fixture error). Read the failure output to confirm.

      **Green.** In `QAWorkflow.run` (`src/evaluation/qa/workflow.py`), directly after the
      `config, spec, spec_text, pipeline_class = (...)` assignment (about line 348), add
      `config = redact_agent_config(config)` with a two-line comment that points to design
      D1 (the run, the continue, and the retry execute the same content). Import
      `redact_agent_config` from `.redaction`, and match the file's import style. Change
      nothing else in `run`. Run the probe in `## Commands`: it must print `[]`. Run both
      test files, then `bash scripts/gate.sh`, then commit
      (`run qa workflow from the redacted agent config`).

## 3. Docs, then prove the whole thing

- [ ] 3.1 Edit, gate, commit — all in this one task.
      - `docs/docs/evaluation.md` near line 451 (`Archi snapshots the resolved file as
        agent_config.resolved.yaml`): add one to three sentences. Values under secret-named
        keys (API keys, tokens, passwords, `Authorization` headers, and the password part of
        a URL) are replaced with `[redacted]` before the write. Every phase, including
        continue and retry, runs from that redacted content. Credentials must come from the
        environment or secrets, not from inline config values.
      - The artifact table row near line 1134 (`Exact tested Archi config`): change it to say
        the exact tested config with secret values redacted.
      - `docs/docs/cli_reference.md:416`: the same short qualifier.
      Do not claim more than the code does. Grep `redaction.py` for each behavior that you
      name. Run `bash scripts/gate.sh` and commit (`document qa snapshot redaction`).

## 4. Publish

- [ ] 4.1 Check, then push and open the PR (no code change in this task). First run the full
      `bash scripts/gate.sh` on the tip. Confirm that `git diff origin/dev --stat` shows only
      the files named in this change (plus this change's `openspec/` directory). Confirm that
      `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` prints `0`, and that
      `git status --porcelain` is empty. Then push the branch: `git push -u origin fix/issue-562-redact-resolved-agent-config`.
      Open the PR against `fasrc/archi` `dev`. Write the body in `/tmp/pr-562.md`. The body
      must contain the line `Closes #562` (the body, not the title), a summary of D1–D3, the
      measured red (the probe printed three sentinels at `e58a7ada`) and the green (it prints
      `[]`), and the out-of-scope list from design D6.
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-562-redact-resolved-agent-config --title "qa eval: redact secret-bearing keys in the persisted agent config (#562)" --body-file /tmp/pr-562.md`.

## Commands

```bash
# The red/green probe (prints the sentinels that reach the snapshot; green is [])
cat > /tmp/probe562.py <<'EOF'
import sys, tempfile
from pathlib import Path
from types import SimpleNamespace
import src.evaluation.qa.workflow as wm
from tests.unit.evaluation.qa import test_workflow as tw
tmp = Path(tempfile.mkdtemp())
config = {"services": {"chat_app": {"agent_class": "FakeAgent", "default_provider": "fake",
  "default_model": "fake-model", "providers": {"fake": {"api_key": "SENTINEL-APIKEY",
  "extra_kwargs": {"max_tokens": 5, "headers": {"Authorization": "Bearer SENTINEL-AUTH"}}}}},
  "postgres": {"password": "SENTINEL-PG"}}}
spec = SimpleNamespace(tools=["fake"])
wm.load_agent_inputs = lambda c, s: (config, spec, "---\nname: Fake\ntools: [fake]\n---\nPrompt\n", object)
wm.ArchiAgentRuntime = tw._AgentFactory()
wm.LangChainEvaluatorRuntime = tw._EvaluatorFactory()
ds = tmp / "d.json"; tw._dataset(ds)
wm.QAWorkflow().composite(ds, tmp / "agent.yaml", tmp / "agent.md", tmp / "run", run_workers=1, score_workers=1)
text = (tmp / "run" / "agent_config.resolved.yaml").read_text()
print([s for s in ("SENTINEL-APIKEY", "SENTINEL-AUTH", "SENTINEL-PG") if s in text])
EOF
PYTHONPATH=$PWD python /tmp/probe562.py

PYTHONPATH=$PWD python -m pytest -q tests/unit/evaluation/qa/test_redaction.py \
  tests/unit/evaluation/qa/test_workflow.py tests/unit/evaluation/qa/test_live_workflow.py
bash scripts/gate.sh
```
