## ADDED Requirements

### Requirement: A host is recorded only when the container endpoint is provably local

`archi create` SHALL record a host only when the container endpoint it deploys to is provably local, and SHALL record no host otherwise.

The recorded host answers "did these artifacts run on the same machine?" from an
inference, not from a measurement: the CLI samples its own machine, and
`host_captured_at` states that a container cannot move hosts. The inference holds only
while the container engine is on the machine that ran the CLI. Podman and Docker both
break that premise on request, so the premise SHALL be checked before the sample is taken.

The check SHALL read `DOCKER_HOST` and `CONTAINER_HOST`, and SHALL classify each endpoint
by its URL scheme rather than by the variable being set. A `unix://` endpoint, a `unix:/`
endpoint, and a bare filesystem path are local. A `tcp://`, `ssh://`, `http://`,
`https://`, or `npipe://` endpoint is not provably local, and so is any scheme this rule
does not name. An unset or empty variable is not evidence of a remote engine.

A presence check SHALL NOT be used. The documented FASRC Podman setup exports
`DOCKER_HOST=unix:/$(podman info --format '{{.Host.RemoteSocket.Path}}')`, which is a
local socket, so keying on the variable being set would refuse a host on exactly the
cluster machines this field exists to identify.

Podman does not read Docker contexts, so both variables SHALL be classified.

`DOCKER_CONTEXT`, and `currentContext` in `~/.docker/config.json`, SHALL be resolved to an
endpoint and classified by the same rule. A context **name** is not itself evidence of a
remote engine. A set and non-empty `DOCKER_HOST` SHALL take precedence over both, because
the Docker CLI itself routes to the default context whenever that variable is set.

The check SHALL be best-effort and SHALL never raise, on the same terms as the capture it
guards. An unreadable or malformed Docker configuration means "no evidence of a remote
engine", never an abort and never a refusal.

The recorded value for a refusal SHALL be the same `null` that every other unrecorded host
uses. No second field and no placeholder string is written.

#### Scenario: A local unix socket still records the host

- **WHEN** `DOCKER_HOST` names a `unix://` endpoint and `archi create` captures the host
- **THEN** the `host` entry names the hostname of the machine running the CLI

#### Scenario: The documented FASRC single-slash socket still records the host

- **WHEN** `DOCKER_HOST` is the single-slash form `unix:/run/user/1000/podman/podman.sock`
- **THEN** the `host` entry names the hostname of the machine running the CLI

#### Scenario: A bare filesystem path still records the host

- **WHEN** `DOCKER_HOST` is a bare path with no scheme, such as `/var/run/docker.sock`
- **THEN** the `host` entry names the hostname of the machine running the CLI

#### Scenario: A TCP endpoint records no host

- **WHEN** `DOCKER_HOST` names a `tcp://` endpoint and `archi create` captures the host
- **THEN** the `host` entry is `null`
- **AND** no hostname and no processor model is sampled

#### Scenario: A Podman SSH endpoint records no host

- **WHEN** `CONTAINER_HOST` names an `ssh://` endpoint
- **THEN** the `host` entry is `null`

#### Scenario: A non-default but local Docker context still records the host

- **WHEN** `DOCKER_CONTEXT` names a context whose stored endpoint is a `unix://` socket
- **THEN** the `host` entry names the hostname of the machine running the CLI

#### Scenario: A remote Docker context records no host

- **WHEN** `DOCKER_CONTEXT` names a context whose stored endpoint is a `tcp://` address
- **THEN** the `host` entry is `null`

#### Scenario: A set DOCKER_HOST outranks a remote context

- **WHEN** `DOCKER_HOST` names a `unix://` socket and `DOCKER_CONTEXT` names a context whose stored endpoint is a `tcp://` address
- **THEN** the `host` entry names the hostname of the machine running the CLI

#### Scenario: An unreadable Docker configuration is not evidence of a remote engine

- **WHEN** `~/.docker/config.json` cannot be read or holds malformed JSON, and no endpoint variable is set
- **THEN** the check returns normally and raises nothing
- **AND** the `host` entry names the hostname of the machine running the CLI

### Requirement: The null-host text names the refusal as its own cause

Both report formats SHALL name the not-provably-local refusal as a fourth distinct cause in the text they render for a `null` host.

A `null` host now has exactly four causes: the deploy predates the field, capture ran and
the hostname was unreadable, the harness could not read `git_info.yaml` at all, and the
container endpoint was not provably local. The field alone separates none of them, so the
text SHALL name all four. Naming a subset states a positive, false claim about the ones it
omits.

The refusal SHALL NOT be folded into the failed-capture cause. A refusal is a decision the
tool made, and a failure is a fault to diagnose. Calling a refusal a failure sends an
operator to debug a hostname lookup on a machine where nothing failed.

#### Scenario: The null host text names the refusal

- **WHEN** an artifact's `metadata.host` is `null` and each report renders its null text
- **THEN** each text names the not-provably-local container endpoint as a cause
- **AND** each text still names the older-deploy cause, the failed-capture cause, and the unreadable-metadata cause

#### Scenario: The refusal is not described as a failure

- **WHEN** each report renders its null text
- **THEN** the refusal cause is worded as a refusal to record, not as a capture that failed
