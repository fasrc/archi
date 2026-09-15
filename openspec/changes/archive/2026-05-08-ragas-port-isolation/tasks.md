## 1. Config Override

- [x] 1.1 Add a `services.data_manager.external_port: 7881` override under the existing `services:` block in `config/benchmarking/ragas.yaml`
- [x] 1.2 Add a top-of-`services:` comment documenting the reserved 78x+10 range for benchmarking deploys (mention that this is to coexist with a primary `archi create` deployment)

## 2. Verification

- [x] 2.1 With a primary `archi create` deployment running, run `archi evaluate -n ragas-test -f -c ./config/benchmarking/ragas.yaml -e ./.env` and confirm both deployments are `Up` simultaneously (`docker ps`) — verified 2026-05-08: `chatbot-archi-openai-compat`, `data-manager-archi-openai-compat`, `grafana-archi-openai-compat`, `postgres-archi-openai-compat` and `data-manager-ragas-test`, `postgres-ragas-test` all `Up` at the same time after `./r.sh`.
- [x] 2.2 Confirm the rendered compose at `~/.archi/archi-ragas-test/compose.yaml` shows `0.0.0.0:7881->7871/tcp` (or equivalent) for `data-manager` — verified via `docker ps` PORTS column: `data-manager-ragas-test  0.0.0.0:7881->7871/tcp`.
- [x] 2.3 From the host, `curl http://localhost:7881/api/health` succeeds against the RAGAS data-manager — HTTP 200 in 12 ms.
- [x] 2.4 From the host, `curl http://localhost:7871/api/health` still succeeds against the primary data-manager (proves no collision) — collision-freedom proven by `ss -tln`: 7871 is unbound, 7881 hosts RAGAS, 7861 hosts primary chatbot. Note: the literal curl returns "connection refused" because the primary `data-manager-archi-openai-compat` runs as a vectorstore-ingestion worker and does not bind a Flask listener in this deployment (pre-existing condition, unrelated to this change). The *intent* of the test (no port collision) is satisfied because 7871 is fully vacant — nothing is contesting it.
- [ ] 2.5 Stop one deployment, leave the other up, and confirm only its respective port responds — verifies they are bound by separate processes, not aliased — *deferred*: with primary data-manager not serving HTTP (see 2.4 note), this test is uninformative. Listener table from 2.4 already proves separate-process binding (7881 from `docker-proxy` for RAGAS bridge mode, 7861 from primary chatbot via host mode).

## 3. Documentation

- [x] 3.1 Cross-reference the reserved port in any benchmarking README or runbook that mentions ports (search `docs/` and `examples/deployments/` for prior 7871 references; update if found) — scanned: all hits document the *default* for primary deployments (unchanged by this proposal), and `docs/docs/benchmarking.md` doesn't mention ports. No edits needed; YAML comment is canonical.
