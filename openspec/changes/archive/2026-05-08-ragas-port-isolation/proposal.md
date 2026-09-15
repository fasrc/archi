## Why

A primary archi deployment and a `archi evaluate` (RAGAS) deployment cannot run side-by-side today. Both publish their `data_manager` on host port 7871 because the benchmarking config inherits the default `external_port: 7871` from `base-config.yaml`. Whichever container starts first wins the bind; the other silently loses its host listener even though `docker ps` shows both `Up`. When the primary deployment runs in `--hostmode`, `chatbot` (7861) and `postgres` (5432) collide too. The RAGAS workflow effectively shoulders the primary chat app off the host, blocking concurrent dev + eval — exactly the workflow we need on this branch (RAGAS judging the same tree the chatbot is serving from).

## What Changes

- Add explicit, non-default `external_port` overrides for benchmarking-only services in `config/benchmarking/ragas.yaml`, so the RAGAS deployment publishes on host ports that don't collide with the primary deployment.
- Update `secrets.env.example` only if a new env var is introduced (none currently expected).
- Document the convention (a small port range reserved for benchmarking deploys) inline in the YAML so future evals don't drift back into the default range.
- No code changes to the compose template, port resolver, or CLI: the existing `_resolve_ports_from_config` and `_check_ports_available` already honor per-deployment `external_port` overrides — we just have to use them.

## Capabilities

### New Capabilities

### Modified Capabilities
- `benchmarking-deployment-config`: Tighten the benchmarking config so an `archi evaluate` run can coexist with a running primary deployment by binding non-default host ports for any service it publishes externally.

## Impact

- **Config**: `config/benchmarking/ragas.yaml` gains a `services.data_manager.external_port` override (and any other externally-published services if they get enabled in the future). Existing RAGAS-only runs are unaffected — they just bind on a different host port.
- **CLI / template / Python**: No code changes. The existing port-override path in `templates_manager.py` already wires `external_port` from config into the rendered compose.
- **Operator workflow**: Operators running both deployments at once stop hitting silent port races; the existing `_check_ports_available` pre-flight will surface a clear error if they ever do collide again.
- **Out of scope**: Dynamic port allocation, a `port_offset` global, or restructuring benchmarking to skip publishing data-manager entirely. Those are larger changes; this proposal is the minimum to unblock the openwebui-compat-mode branch's RAGAS workflow.
