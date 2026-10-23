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

`DOCKER_CONTEXT`, and `currentContext` in the Docker configuration directory, SHALL be
resolved to an endpoint and classified by the same rule. A context **name** is not itself
evidence of a remote engine. A set and non-empty `DOCKER_HOST` SHALL take precedence over
both, because the Docker CLI itself routes to the default context whenever that variable
carries a value. An empty `DOCKER_HOST` SHALL NOT take that precedence: Docker treats it as
absent and honors the context. Measured on Docker 29.7.2 — with a remote `DOCKER_CONTEXT`
set, `docker context show` prints `default` for a non-empty `DOCKER_HOST` and prints the
context name for an empty one.

The Docker configuration directory SHALL be `DOCKER_CONFIG` when that variable is set and
non-empty, and `~/.docker` otherwise. `DOCKER_CONFIG` relocates `config.json` **and** the
`contexts/meta` store together, so reading `~/.docker` unconditionally would classify a
relocated remote context as local.

`CONTAINER_CONNECTION` SHALL be resolved through the Podman connection store and classified
by the same rule. Setting it puts Podman in remote mode against the named destination, and it
outranks `CONTAINER_HOST`. Measured on Podman 6.1.0 — `CONTAINER_CONNECTION` naming an
`ssh://` connection routes there even when `CONTAINER_HOST` names a local socket.

Because it outranks `CONTAINER_HOST`, it SHALL be classified **instead of** that variable and
not in addition to it. A stale remote `CONTAINER_HOST` left in the environment alongside a
named local connection describes an endpoint Podman does not use, and refusing on it would
drop the host of a deployment that is genuinely local. `CONTAINER_HOST` SHALL still be
classified whenever no connection is named.

A `CONTAINER_CONNECTION` that cannot be resolved to a URI SHALL be treated as not provably
local. This is the opposite of the Docker rule above, and the asymmetry follows the default
each tool falls back to: Docker with no readable context uses its local default socket,
while Podman with a named connection is already pointed somewhere else. The store read is
`$XDG_CONFIG_HOME/containers/podman-connections.json`, falling back to
`~/.config/containers/podman-connections.json`; a connection recorded only in the legacy
`containers.conf` destinations is therefore unresolvable, and refusing a host is the honest
answer for it.

The check SHALL be best-effort and SHALL never raise, on the same terms as the capture it
guards. An unreadable or malformed Docker configuration means "no evidence of a remote
engine", never an abort and never a refusal.

A stored endpoint that is not a string SHALL be classified as not provably local. `{"Host":
1}` is valid JSON, so a resolver can return a non-string and the classifier must answer for
it rather than raise: an abort here would fail an otherwise valid deployment over optional
provenance, which the never-raise rule above exists to prevent. This is a refusal rather
than "no evidence" because a malformed endpoint is a resolved value the check cannot read,
not a missing one.

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

#### Scenario: An empty DOCKER_HOST does not outrank a remote context

- **WHEN** `DOCKER_HOST` is set to the empty string and `DOCKER_CONTEXT` names a context whose stored endpoint is a `tcp://` address
- **THEN** the `host` entry is `null`

#### Scenario: A relocated Docker configuration directory is read

- **WHEN** `DOCKER_CONFIG` names a directory whose context store holds a `tcp://` endpoint for the selected context
- **THEN** the `host` entry is `null`

#### Scenario: A remote Podman connection records no host

- **WHEN** `CONTAINER_CONNECTION` names a stored connection whose URI is an `ssh://` address
- **THEN** the `host` entry is `null`

#### Scenario: A local Podman connection still records the host

- **WHEN** `CONTAINER_CONNECTION` names a stored connection whose URI is a `unix://` socket
- **THEN** the `host` entry names the hostname of the machine running the CLI

#### Scenario: An unresolvable Podman connection records no host

- **WHEN** `CONTAINER_CONNECTION` names a connection that the connection store does not hold
- **THEN** the `host` entry is `null`

#### Scenario: A named local connection outranks a stale remote CONTAINER_HOST

- **WHEN** `CONTAINER_CONNECTION` names a stored connection whose URI is a `unix://` socket and `CONTAINER_HOST` names an `ssh://` endpoint
- **THEN** the `host` entry names the hostname of the machine running the CLI

#### Scenario: A malformed stored endpoint records no host and raises nothing

- **WHEN** the selected Docker context's stored `Host` is a number rather than a string
- **THEN** the check returns normally and raises nothing
- **AND** the `host` entry is `null`

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
