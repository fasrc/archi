# Design — refuse the host stamp when the engine may be elsewhere

## Context

`metadata.host` exists to answer one question: did these two artifacts run on the same
machine? The field earns that answer from an inference, not from a measurement. `archi
create` samples the CLI host, and `host_captured_at`
(`src/bin/service_benchmark.py:475`) states that a container cannot move hosts, so every
run in the deployment ran there too.

The inference has one unstated premise: the container engine is on the machine that ran
the CLI. Podman and Docker both break that premise on request, through an environment
variable, with no visible sign in the artifact.

The premise is cheap to check and expensive to omit. A wrong host is worse than no host,
because a reader trusts a recorded value and gates a campaign on it.

## Goals / Non-Goals

**Goals**

- A recorded host is true. When the premise cannot be established, record nothing.
- The reader can tell a refusal from a failure, because they call for different actions.
- The check never raises and never fails a deploy.

**Non-Goals**

- Support for a remote container engine. The 2026-09-09 decision on #442 is that archi
  does not support one.
- Capture of the engine's own host name (`docker info`). That option costs a subprocess at
  deploy time and returns a name that a `socket.getfqdn()` on the other side cannot be
  compared with. It is reserved for the day remote engines become a feature.
- Any change to `host_captured_at`. Enforcement makes the existing prose true.

## Decisions

### 1. Classify by scheme, never by presence

A presence check on `DOCKER_HOST` would do real damage. The FASRC Podman instructions tell
an operator to run:

```bash
export DOCKER_HOST=unix:/$(podman info --format '{{.Host.RemoteSocket.Path}}')
```

That endpoint is a **local** socket. A presence check would refuse to stamp a host on
exactly the cluster machines this field exists to identify.

The classification table:

| Endpoint form | Verdict |
| --- | --- |
| `unix://…`, `unix:/…` | local |
| a bare filesystem path (`/run/user/1000/podman/podman.sock`) | local |
| empty or unset | no evidence of a remote engine — local |
| `tcp://`, `ssh://`, `http://`, `https://`, `npipe://` | not provably local |
| any other scheme | not provably local |

Note the single-slash form `unix:/…`. The FASRC command above produces it, and a parser
that only accepts `unix://` would refuse on the documented setup. This is the highest-cost
mistake available in this change, so it gets its own test.

`npipe://` is a Windows named pipe and is in fact local. It is classified as not provably
local because the issue's decision table says so and because archi does not run on
Windows, so the refusal costs nothing that exists.

### 2. Read both `DOCKER_HOST` and `CONTAINER_HOST`

Podman does not read Docker contexts. It reads `CONTAINER_HOST` and its own
`podman system connection` store. `archi` runs `podman compose` whenever `--podman` is
passed (`src/cli/cli_main.py:86`, documented at `docs/docs/cli_reference.md:44`), so a
Docker-only check would leave the Podman path — the one FASRC documents — unguarded.

### 3. Refuse if either variable names a remote endpoint

`collect_host_information()` does not know which engine the deploy will use.
`get_git_information()` takes no arguments, its one production caller is
`TemplateManager._stage_benchmarking` (`src/cli/managers/templates_manager.py:804`), and
`templates_manager.py` contains no reference to podman at all. Threading the `--podman`
flag from `cli_main.py` down to a provenance helper is a cross-cutting change that #442
does not ask for.

So the check is engine-agnostic: if **either** variable names a remote endpoint, no host
is stamped.

The cost is a false refusal — a Docker deploy on a machine where `CONTAINER_HOST` happens
to point somewhere else loses its host stamp. The cost of the opposite error is a false
recorded host, which a reader believes. A refusal is visible in the artifact and names its
own cause; a false host is invisible. If the false refusal is ever measured in practice,
threading the engine flag is the follow-up, and it is additive.

### 4. Resolve a Docker context by scanning, not by digesting its name

`DOCKER_CONTEXT`, and `currentContext` in `~/.docker/config.json`, name a context rather
than an endpoint. The Docker CLI stores each context at
`~/.docker/contexts/meta/<64-hex>/meta.json`, holding `Name` and
`Endpoints.docker.Host` (confirmed against the `docker context inspect` reference output,
which prints both the `MetadataPath` and the `Host`).

The directory name is a digest of the context name. This change does **not** compute that
digest. It globs `~/.docker/contexts/meta/*/meta.json`, parses each file, and takes the
one whose `Name` matches. Matching on a field the file states itself cannot be wrong about
the digest algorithm, and the store holds a handful of entries, so the scan costs nothing.

A context named `default`, or no context name at all, resolves to no endpoint. A
non-default context **name** is not by itself evidence of a remote engine — Docker Desktop
and rootless setups both use a named local context.

### 5. `DOCKER_HOST` outranks any context

The Docker CLI resolves the context name as follows (`cli/command/cli.go`):

```go
if os.Getenv(client.EnvOverrideHost) != "" {
    return DefaultContextName
}
```

A set `DOCKER_HOST` sends the CLI to the default context, which reads its address from
`DOCKER_HOST`. So the context is not consulted at all. This change copies that
precedence: when `DOCKER_HOST` is set and non-empty, the Docker endpoint is `DOCKER_HOST`,
and `DOCKER_CONTEXT` and `currentContext` are not read.

Without this rule a stale non-default context would refuse a deploy that Docker itself
routes to a local socket.

`CONTAINER_HOST` is Podman's own variable and is classified independently. Podman does not
consult Docker contexts, so no precedence question arises between them.

### 6. Every failure means "no evidence of a remote engine"

The capture clause is absolute: it never raises and never fails a deploy. So the check
catches everything and returns "local" on any fault — an unreadable `~/.docker/config.json`,
malformed JSON, a permissions error, a missing home directory.

This is deliberately the *less* conservative default for the failure path, and it is the
right one. A fault in reading a Docker config file is not evidence that an engine is
remote, and treating it as evidence would silently drop the host stamp on every machine
with an odd home directory.

### 7. The null-host causes are now exactly four

`metadata.host` is `null` when, and only when:

1. the deploy predates the field (`git_info.yaml` carries no `host` key);
2. capture ran and the hostname was unreadable or blank;
3. the harness could not read `git_info.yaml` at all — `add_metadata` catches `OSError`
   and carries on (`src/bin/service_benchmark.py:456-460`);
4. **new** — the container endpoint was not provably local, so capture refused to run.

The reports name three today (`src/utils/generate_benchmark_report.py:77` and `:86`), and
the sentinel comment at `:60-74` says "THREE". Both are corrected together.

Cause 4 is not folded into cause 2. "Capture failed" sends an operator to debug
`socket.getfqdn()` on a machine where nothing failed. The refusal is a decision the tool
made, and the honest text says so.

## Risks / Trade-offs

- **A false refusal on a local `DOCKER_HOST` form this table does not know.** Mitigated by
  accepting a bare path and both `unix:` forms, and by testing the documented FASRC
  command's exact output shape.
- **The refusal is silent at deploy time.** The operator sees it only in the artifact. A
  deploy-time log line is tempting but out of scope here; the capture helper has no logger
  and `get_git_information` is called from a staging path that already logs nothing about
  provenance.
- **An existing test reads the ambient environment.**
  `test_git_info_yaml_carries_the_host_block`
  (`tests/unit/test_benchmark_host_provenance.py:13`) calls `get_git_information()` with no
  environment isolation. After this change, a developer or a CI runner with `DOCKER_HOST`
  exported would see it fail for a correct reason. The test gains an explicit `delenv` for
  all three variables, which pins the premise rather than hiding it.

## Migration

None. No artifact is rewritten and no field changes shape. An artifact written before this
change keeps whatever host it recorded; the fourth cause in the null text describes a state
that only new deploys can produce, and it is accurate about old artifacts too, because
they could not have refused.
