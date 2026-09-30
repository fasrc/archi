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

**Confirmed at runtime**, because the CLI reference's environment-variable table states the
opposite — that `DOCKER_CONTEXT` "overrides DOCKER_HOST env var and default context" — and
review read it that way. Measured on Docker **29.7.2** in a throwaway `DOCKER_CONFIG`
holding a real context `remotebox` at `tcp://127.0.0.1:19999`:

| Environment | Endpoint dialed |
| --- | --- |
| `DOCKER_CONTEXT=remotebox` | `tcp://127.0.0.1:19999` — the context |
| `DOCKER_CONTEXT=remotebox` and `DOCKER_HOST=tcp://127.0.0.1:9` | `tcp://127.0.0.1:9` — `DOCKER_HOST` |
| `currentContext: remotebox` and `DOCKER_HOST=tcp://127.0.0.1:9` | `tcp://127.0.0.1:9` — `DOCKER_HOST` |

A *nonexistent* `DOCKER_CONTEXT` does not even produce `context not found` while
`DOCKER_HOST` is set, so the name is never resolved — matching the `cli.go` early return
above. `docker context ls` describes the default context as "Current DOCKER_HOST based
configuration", the same fact from the other side.

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

### 8. Podman's stored default connection is not read

`podman system connection default` does set a default destination, and the store does
carry it — measured on Podman **6.1.0**, an isolated `XDG_CONFIG_HOME` yields
`podman-connections.json` =
`{"Connection": {"Default": "remotebox", "Connections": {…}}, "Farm": {}}`. Review asked
for `Connection.Default` to be resolved and classified when no environment variable names
a connection. It is deliberately not.

archi deploys with `podman compose`
(`src/cli/managers/deployment_manager.py:29`), and `podman compose` ignores the stored
default. With `remotebox` = `ssh://user@remote.example.com/run/podman/podman.sock` set as
the default and neither `CONTAINER_CONNECTION` nor `CONTAINER_HOST` set:

```
$ podman --log-level=debug compose version
… Executing compose provider (…/docker-compose version) with additional env
  DOCKER_HOST=unix:///run/user/1000/podman/podman.sock …
```

The local socket, not the SSH destination. `podman --remote compose` does honour the
stored default — it fails trying to reach `remote.example.com` — but archi never passes
`--remote`, and `podman info` in local mode likewise reports the local host.

Classifying the stored default would therefore refuse deployments that `podman compose`
routes locally: the same false-refusal error decision 3 accepted a cost to avoid, and the
one the `CONTAINER_CONNECTION`-outranks-`CONTAINER_HOST` rule exists to prevent. If archi
grows a `--remote` podman path, or ships on a remote-only podman client, the stored default
becomes reachable and must then be classified. It is reachable through no code path in the
tree today.

### 9. A stale context entry must not hide the selected one

`_resolve_context_endpoint` globs every `contexts/meta/*/meta.json` and matches on `Name`
(decision 4). Valid JSON is not necessarily an object: a stale `meta.json` holding `[]`
parses cleanly, and the `.get("Name")` that follows raises `AttributeError` on a list. That
escaped the per-file handler, reached the function's outer handler, and returned `None` for
the **whole store** — which the caller reads as "no context configured", so a selected
remote context went unseen and the CLI machine was stamped. Found in review; measured
before the fix, with the stale entry visited first:

```
_resolve_context_endpoint()            -> None
container_endpoint_is_provably_local() -> True     # remote context, stamped anyway
```

`pathlib.Path.glob` yields `os.scandir` order and does not sort, so whether the stale entry
is visited before the selected one is filesystem luck — the defect appears and disappears
by machine, which is worse than failing every time. Each parsed value is now checked with
`isinstance(..., dict)` and an unreadable entry is skipped rather than ending the scan;
`Endpoints` and `Endpoints.docker` get the same check, because they were read with `.get`
too and had the identical shape problem one level down.

### 10. A scheme-less `DOCKER_HOST` is not a filesystem path

Decision 1's table said a value with no scheme is a bare path and therefore local.
Measured on Docker **29.7.2**, that is wrong — Docker's host parser prepends `tcp://`
to every scheme-less value:

| `DOCKER_HOST` | Docker dials |
| --- | --- |
| `127.0.0.1:19999` | `tcp://127.0.0.1:19999` |
| `[::1]:19999` | `tcp://[::1]:19999` |
| `somehost.example.edu:2375` | `tcp://somehost.example.edu:2375` (with a DNS lookup) |
| `/var/run/docker.sock` | `tcp://localhost:2375/var/run/docker.sock` |

So `engine.example.edu:2376` in `DOCKER_HOST` names a **remote daemon**, and the old
rule classified it as a local socket path — a fail-open of exactly the kind this change
exists to close. Found in review.

The fallback is now narrowed rather than removed: a scheme-less value is local only when
it is path-shaped (`/`, `./`, `../`, `~`), and anything else — a bare hostname, a
host-and-port, a bracketed IPv6 address — is not provably local. The last row above shows
Docker prepends `tcp://` to real paths too, but `tcp://localhost:2375/...` is on this
machine, so "local" remains the right answer to the question this module asks. Path shapes
are kept because Podman's `CONTAINER_HOST` does accept a socket path, and because the FASRC
command produces `unix:/...`, which the scheme branch already handles — so decision 1's
highest-cost case was never relying on this fallback in the first place.

Anything unrecognised now fails **closed**. This helper decides whether it is safe to stamp
this machine's name onto a deployment; an address it cannot classify is not evidence that
the engine is here.

### 11. Conflicting duplicate context entries refuse

Decision 4 matches on the `Name` a `meta.json` states, and took the first hit. Review asked
what happens when two entries claim the selected name and disagree: the answer was
"whichever `os.scandir` listed first", so a stale duplicate naming a local socket could hide
the real remote entry. Same fail-open as decision 9, reached by a different route, and with
the same order-dependence — it would bite on some machines and not others.

The scan now collects every match. One distinct endpoint resolves as before; more than one
returns `_AMBIGUOUS`, which the caller reads as not provably local. Ambiguity is not
evidence of a local engine. Agreeing duplicates are not ambiguous and still classify, so a
harmless double entry does not cost a host stamp.

`_AMBIGUOUS` is a sentinel distinct from `None` on purpose: `None` means "no context
configured", which decision 6 defines as no evidence of a remote engine, and collapsing the
two would make a conflict silently permissive.

### 12. `host_captured_at` must not assert a machine it does not have

`ResultHandler.add_metadata()` wrote `host_captured_at` unconditionally, saying the capture
happened "on the machine this stack runs on — a container cannot move hosts, so a --rerun
ran here too". When this change's guard refuses, `host` is `null` and that sentence sits
next to it, still asserting the same machine. Found in review, and it is the change's own
premise inverted: the refusal exists to stop the artifact making a claim it cannot support.

It went unnoticed because both report renderers guard their host line on `host` being
truthy, so the sentence never appears in the HTML or Markdown output. It survives in the raw
JSON artifact — which is what a later reader, and every downstream consumer, actually parses.

The field is now conditional. With no host it reads "no host recorded — either this
deployment predates the field, or `archi create` refused to capture one because the container
engine was not provably local", which keeps the field informative: a reader can still tell an
old deploy from a refusal, which an empty string or a missing key would not.

### 13. Two defects the previous round's own fixes introduced

Review round 5 found both, and both are worth recording because they are the cost of
fixing things: a guard can open a hole beside itself.

**Decision 11's duplicate check could not deduplicate an unhashable `Host`.** It built a
`set` of the matches, and a stale same-name entry holding `"Host": []` or `"Host": {}` is
valid JSON that raises `TypeError` on hashing. That escaped to the outer handler, which
returned `None`, and the caller read a `None` endpoint as "no context configured" — so a
valid remote sibling went unseen and the CLI machine was stamped. Exactly the fail-open
decision 11 was written to close, reintroduced by the line that closed it.

The two guards had a hole between them: decision 9's `isinstance` check covers the *shape
of the metadata*, and the earlier non-string `Host` test used a hashable `1`, which reaches
`endpoint_is_local` and is refused there. A list or dict passed the shape check, never
reached the classifier, and broke the dedupe instead. Non-string matches now refuse before
the set is built. Five tests, parameterised over `[]`, `{}`, a populated list and a
populated dict, plus the lone-entry case.

**Decision 12's replacement text named two of the four null-host causes.** It read "either
this deployment predates the field, or `archi create` refused to capture", which is false
on the other two paths from decision 7 — an unreadable or blank hostname, and an unreadable
`git_info.yaml`. So the fix for one false exhaustive claim shipped another, narrower one.

`add_metadata()` cannot tell the four apart: it reads a `git_info.yaml` that does not record
which one applied. So the text names all four and asserts none of them, and the test checks
for each cause by name plus the absence of "either", so a later edit cannot quietly narrow
it again.

**And a third, found the round after.** Decision 9's malformed-metadata guards returned
`None` from *inside* the loop. That discarded any match already collected and stopped the
remaining entries from being inspected — so a stale same-name entry with an unreadable
`Endpoints` visited first hid a valid remote sibling, and the caller read "no context
configured". The third instance of the same mistake in three rounds: an early exit taken
for a per-entry fault, where the loop should have carried on.

Unreadable entries are now collected as a sentinel and the scan runs to the end, then:

- **all matches unreadable** → `None`. Docker cannot resolve the context either, so no
  deployment happens; decision 6's rule applies, and the existing single-malformed-entry
  tests keep their answer.
- **readable and unreadable both present** → `_AMBIGUOUS`. Docker resolves one and this
  cannot tell which, so trusting the half that happens to parse is trusting a coin flip —
  and the readable half may be the stale local one, which is the test with the local
  sibling.

Three tests, both visit orders, both malformed shapes.

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
