## MODIFIED Requirements

### Requirement: Evaluations console behind a config toggle

The chat app SHALL expose the ported `/evaluations` console only when `services.chat_app.evaluations.enabled` is strictly `true` in the deployed configuration. All console configuration parsing and request authorization SHALL live in a unit-tested seam module (`evaluation_console.py`); `app.py` SHALL contain thin call sites only. Every refusal the seam makes SHALL log one error and return `None`, so the console turns itself off while the chat app stays up — and a storage error raised while the console's own directories are created or swept SHALL be one of those refusals, not an exception that reaches `app.py`.

The storage case is the addition. The two config refusals — a missing `agent_config_path`
and the live-config identity — already fail closed. Building the service also touches disk
in three places: the catalog mkdirs five directories, the history mkdirs one, and the job
manager mkdirs one and then sweeps stale job files, writing to each one it finds. Any of
those raises `OSError` on a root the deployment cannot write: a read-only bind mount,
rootless-podman or NFS ownership on the auto-created host directory, or a typo in an
override.

`app.py` calls the seam bare during init, so such an `OSError` ends the process. The chat
app crash-loops for the sake of a console that is off by default, and the operator reads a
traceback about `mkdir` rather than a sentence about their config.

Construction raising nothing is not proof that the root is usable, so the seam SHALL also establish that the root accepts a write before it registers the console. Every disk touch during construction is a `mkdir(..., exist_ok=True)` or a sweep that writes only when it finds a stale job, so a read-only root that already holds the five catalog directories and no active job raises nothing at all — the shape an operator gets restoring a snapshot onto a read-only volume. Without that statement the console registers and the first dataset import answers 500, which is the failure this requirement exists to convert into a refusal. The probe SHALL leave the catalog tree as it found it.

The net is `OSError` and nothing wider, which is a requirement and not an implementation
note. A wider net would catch a programming error — a constructor called with a wrong
keyword, for one — and report it as a disabled console. That failure is silent where a crash
is loud, so the console must stay up-or-off on storage grounds only, and a genuine defect
must still surface.

A corrupt job file is already survivable and needs no new handling: the sweep skips a file
it cannot read as valid JSON, and an unreadable file reaches that same path. The scenario
below holds it in place, because the guarantee lives in code this requirement does not
otherwise constrain.

#### Scenario: Console off by default

- **WHEN** the deployed configuration omits the `evaluations` block or sets
  `enabled` to anything but boolean `true`
- **THEN** `/evaluations` is not registered and the index page shows no
  evaluations nav link

#### Scenario: Console refuses the live running config

- **WHEN** `evaluations.enabled` is `true` but `agent_config_path` is omitted or
  names the live running config (`/root/archi/configs/config.yaml`)
- **THEN** the service does not build, an error log names the reason, the chat
  app stays up, and the console stays off — each run snapshots the whole agent
  config into the host-mounted workspace, so the live config must never be the
  snapshot source

#### Scenario: An unwritable evaluations root disables the console instead of stopping chat

- **WHEN** `evaluations.enabled` is `true` with a valid `agent_config_path`, and creating the directories under `evaluations.root` raises an `OSError`
- **THEN** the service does not build, one error log names the configured root, no exception escapes to `app.py`, and the chat app stays up with the console off

An unwritable root is the ordinary way this happens, and the operator has to be told which
path to fix. A message that omits the root leaves them reading code for the default.

#### Scenario: A storage error late in construction is caught too

- **WHEN** every directory under `evaluations.root` is created, and the stale-job sweep then fails to write a job file it found
- **THEN** the service does not build, one error log names the configured root, and no exception escapes to `app.py`

The sweep runs after the last mkdir and writes to disk, so a read-only root fails there and
nowhere earlier. A guard that covered only directory creation would let this one through,
which is the whole reason the guard covers the construction rather than a first step of it.

#### Scenario: A pre-populated read-only root disables the console

- **WHEN** `evaluations.root` is read-only but already holds the five catalog directories and no stale job to sweep, so construction completes without raising
- **THEN** the service does not build, one error log names the configured root, and the catalog tree is left exactly as it was found

This is the read-only case construction cannot detect: every `mkdir` is satisfied by a
directory that already exists, and the sweep writes nothing because it finds nothing. An
operator restoring an evaluations snapshot onto a read-only volume gets exactly this shape,
and without a write statement the console registers and fails on the first import instead of
at start-up, where the message is.

#### Scenario: A corrupt job file leaves the console up

- **WHEN** `evaluations.root` is writable and its jobs directory holds a file that is not valid JSON
- **THEN** the service builds and the console is on

The sweep skips a file it cannot read, so corrupt state does not disable a console whose
storage is fine. Losing this would trade a crash for an outage that is harder to see.

#### Scenario: A programming error is not disguised as a disabled console

- **WHEN** building the service raises an error that is not an `OSError`
- **THEN** that error propagates

The seam refuses on storage grounds and on config grounds. It does not decide that every
failure means "no console", because a defect reported as a disabled feature is a defect
nobody goes looking for.

#### Scenario: Paused runs resume against frozen inputs

- **WHEN** a run paused as `attention_required` is continued after the deployed
  agent config or agent spec changed on disk
- **THEN** the continuation executes against the run's frozen
  `agent_config.resolved.yaml` and `agent_spec.resolved.md`, not the live files —
  the system under test cannot change without a new run

#### Scenario: Console on for the trial deployment

- **WHEN** the dev deployment sets `evaluations.enabled: true` with a staged
  `mcp_config_path` and is redeployed
- **THEN** `GET /evaluations` returns 200, the nav link renders, and a full console
  loop (import dataset → import profile → generate and approve atoms → run → score
  → history) completes
- **AND** the recorded runtime evidence shows the chatbot container's logs for the
  evaluation job (job start, MCP stdio subprocess launch, live pre/post checks,
  score completion) with no tracebacks, and the run's artifacts present under the
  host directory backing `/root/archi/evaluations` — HTTP and UI success alone
  never satisfy this scenario

#### Scenario: Auth-off deployments authorize all console requests

- **WHEN** the deployment runs with auth disabled (the FASRC dev configuration)
- **THEN** the seam's `authorize_request` allows console requests without a session
