## ADDED Requirements

### Requirement: A relocated evaluations root is refused before the deployment is destroyed

`archi` SHALL refuse a deployment whose enabled evaluations console configures a `root` that is not the mounted container path or a path beneath it, and SHALL make that refusal before any destructive step, naming both the configured root and the mounted path.

The compose template bind-mounts the host evaluations directory at a fixed container path
and does not vary with the configured root. A root outside that path therefore stores
datasets, human approvals, job records and the whole run history in the container's overlay
filesystem, where `archi create --force` — the standard redeploy — erases them without
warning.

The runtime cannot detect this. An overlay root is writable, so the console builds, works,
and loses the catalog on the next redeploy. The refusal must happen while the deployment is
still being described.

Refusing before any destructive step is not a separate nicety but the point: a refusal that
arrives after a `--force` teardown costs the operator the running deployment it was meant to
protect.

The comparison SHALL be made on path components after lexical normalization, and SHALL NOT consult the filesystem.

A string-prefix test accepts a sibling directory that merely starts with the mounted path,
which is exactly the near-miss an operator makes. Resolving the value against the host's
filesystem is meaningless, because the value is a path inside the container.

The check SHALL apply only when the evaluations console is enabled.

With the toggle off, the console seam refuses before it creates any directory, so nothing is
ever written under the root and a leftover value is inert. Refusing it would break
deployments that carry one today and protect nothing.

#### Scenario: A root outside the mounted path refuses the deployment

- **WHEN** a configuration enables the evaluations console with a `root` outside the mounted
  container path, and `archi create --force` is invoked against an existing deployment
- **THEN** the command exits non-zero
- **AND** the error names both the configured root and the mounted path, and says a path
  beneath the mounted path is allowed
- **AND** the existing deployment is left intact, because the refusal happened before the
  teardown

#### Scenario: A sibling that shares the mounted path's prefix is refused

- **WHEN** the configured `root` is a sibling directory whose string begins with the mounted
  path, such as the mounted path with `-backup` appended
- **THEN** the deployment is refused

A prefix comparison accepts this root and loses the catalog. Only a component comparison
rejects it, so this scenario is what distinguishes the two implementations.

#### Scenario: A traversing or relative root is refused

- **WHEN** the configured `root` walks back out of the mounted path with `..`, or is a
  relative path
- **THEN** the deployment is refused

Normalization is what makes the traversal case fail; without it the raw string still looks
like it is under the mount. A relative root is refused because the compose file pins no
working directory for the container, so no absolute location can be proven.

#### Scenario: The default configuration deploys unchanged

- **WHEN** a configuration omits `evaluations.root`, or sets it to exactly the mounted path
- **THEN** the deployment is accepted
- **AND** the rendered compose file is byte-identical to the one rendered before this
  requirement existed

The knob's default is the mounted path, so every existing deployment must pass untouched. A
guard that changes a default render is a migration, not a guard.

#### Scenario: A root beneath the mounted path is accepted

- **WHEN** the configured `root` is a subdirectory of the mounted path
- **THEN** the deployment is accepted

The knob keeps its useful range. Two catalogs side by side on the same volume is the reason
the knob exists, and that use never leaves the mount.

#### Scenario: A disabled console is not refused for its root

- **WHEN** `evaluations.enabled` is anything but boolean `true` and `root` is outside the
  mounted path
- **THEN** the deployment is accepted

#### Scenario: Moving the mount fails a test rather than silently disarming the check

- **WHEN** the compose template renders the chatbot service's evaluations volume at a
  container path other than the one the validator guards
- **THEN** a unit test fails

The validator holds the mounted path as a constant, so template drift would otherwise leave
it guarding a path that no longer exists — a check that passes everything and protects
nothing.
