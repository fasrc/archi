# Disposition table: port-live-eval-trial

Candidate field: `git diff --name-status d1c29380 bebfbe56` — 220 files.
Eval scope: `git diff --name-only 9c9e1cb0 bebfbe56` (upstream main → pin) — 86 files.
Every candidate file carries exactly one disposition (design.md rules).

Totals: omitted-optional 3, port-hunks 22, port-verbatim 52, skip-dead-on-fork 26, skip-unrelated-upstream 117

| File | Δ | Eval scope | Disposition | Reason |
| --- | --- | --- | --- | --- |
| `.github/workflows/publish-base-images.yml` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `.gitignore` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `README.md` | M | yes | port-hunks | optional one-line mention; take ours otherwise |
| `configs/submit76/config.yaml` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `docs/docs/advanced_setup_deploy.md` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `docs/docs/api_reference.md` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `docs/docs/benchmarking.md` | M | yes | port-hunks | merge the eval cross-reference sections |
| `docs/docs/cli_reference.md` | M | yes | port-hunks | content-merge the eval sections (the one real docs merge) |
| `docs/docs/configuration.md` | M | yes | port-hunks | merge the eval config sections |
| `docs/docs/data_sources.md` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `docs/docs/evaluation.md` | A | yes | port-hunks | verbatim minus two upstream-only references: a helm deployment clause (fork has no helm) and the `ab_only` frontmatter sentence (fork agent-spec loader has no A/B support) |
| `docs/docs/helm_deployment.md` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `docs/docs/index.md` | M | yes | port-hunks | eval hunks only; fork-unmodified |
| `docs/docs/install.md` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `docs/docs/quickstart.md` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `docs/docs/services.md` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `docs/docs/troubleshooting.md` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `docs/docs/user_guide.md` | M | yes | port-hunks | eval hunks only; pin version carries unrelated Jira/playbook content |
| `docs/mkdocs.yml` | M | yes | port-hunks | nav entry Evaluation after Benchmarking |
| `examples/agents/cms-comp-ops.md` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `examples/agents/indico-assistant.md` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `examples/deployments/basic-agent/config.yaml` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `examples/deployments/basic-agent/indico_example.list` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `exec_plans/2026-08-13-live-state-qa-evaluation.md` | A | yes | skip-unrelated-upstream | upstream process artifact, not capability code |
| `package-lock.json` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `package.json` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `pyproject.toml` | M | yes | port-hunks | add mcp==1.27.2, ijson==3.5.1, pytest markers; keep fork deps |
| `requirements/requirements-base.txt` | M | no | port-hunks | fork-side dep pins (mcp==1.27.2, ijson==3.5.1) required by the ported eval code; upstream's own changes to this file not taken |
| `scripts/dev/build_docker_images.sh` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `scripts/dev/docker_common.sh` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `skills/indico.md` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/archi.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/pipelines/__init__.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/pipelines/agents/agent_spec.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/pipelines/agents/base_react.py` | M | yes | port-hunks | two hunks: callbacks pass-through + loaded_mcp_tools |
| `src/archi/pipelines/agents/cms_comp_ops_agent.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/pipelines/agents/playbook_mixin.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/pipelines/agents/tools/__init__.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/pipelines/agents/tools/indico_ingest.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/pipelines/agents/tools/ingest.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/pipelines/agents/tools/mcp.py` | M | yes | skip-unrelated-upstream | upstream-main skills/http-auth work; eval runtime does not import it |
| `src/archi/pipelines/agents/tools/playbook_tools.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/pipelines/agents/utils/mcp_utils.py` | M | yes | skip-unrelated-upstream | upstream-main skills/http-auth work; eval runtime does not import it |
| `src/archi/providers/local_provider.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/archi/utils/output_dataclass.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/bin/service_chat.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/bin/service_jira.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/bin/service_mailbox.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/bin/service_redmine.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/cli_main.py` | M | yes | port-hunks | register eval_cli; skip the helm install hunk (dead on fork) |
| `src/cli/managers/config_manager.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/managers/deployment_manager.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/managers/secrets_manager.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/managers/templates_manager.py` | M | yes | port-hunks | evaluation-config staging minus the helm branch |
| `src/cli/managers/volume_manager.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/qa_eval.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/cli/service_registry.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/source_registry.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/templates/base-compose.yaml` | M | yes | port-hunks | evaluations data mount + conditional evaluation_config:ro mount |
| `src/cli/templates/base-config.yaml` | M | yes | port-hunks | add the evaluations block under chat_app |
| `src/cli/templates/dockerfiles/Dockerfile-jira` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/templates/dockerfiles/base-helm-images/Dockerfile-chat-universal` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/templates/dockerfiles/base-helm-images/Dockerfile-data-manager-universal` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/templates/dockerfiles/base-helm-images/Dockerfile-grafana-universal` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/templates/dockerfiles/base-helm-images/Dockerfile-postgres-universal` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/templates/helm/Chart.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/_helpers.tpl` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/chatbot/configmap.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/chatbot/deployment.yaml` | A | yes | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/chatbot/evaluation-configmap.yaml` | A | yes | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/chatbot/pvc.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/chatbot/service.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/config-seed.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/data-manager/configmap.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/data-manager/deployment.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/data-manager/pvc.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/data-manager/service.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/grafana/configmap.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/grafana/deployment.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/grafana/service.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/postgres/configmap.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/postgres/deployment.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/postgres/pvc.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/postgres/service.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/pvc.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/templates/secrets.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/helm/values.yaml` | A | no | skip-dead-on-fork | fork has no helm tree and no install command |
| `src/cli/templates/init.sql` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/tools/config_seed.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/utils/helpers.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/cli/utils/service_builder.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/data_manager/collectors/localfile_manager.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/data_manager/collectors/localfile_resource.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/data_manager/collectors/scrapers/integrations/indico_scraper.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/data_manager/collectors/scrapers/scraper_manager.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/data_manager/collectors/utils/slide_converter.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/data_manager/vectorstore/manager.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/evaluation/__init__.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/__init__.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/artifacts.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/catalog.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/console.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/constants.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/dataset.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/history.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/jobs.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/live_checks.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/oracle.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/oracle_config.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/phases.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/preparation.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/profile.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/runtime.py` | A | yes | port-hunks | eval-capability file, absent on the fork, with one adaptation: the mcp guard in `_runtime_for_attempt` now calls `refresh_agent(force=True)` first, because the fork's `BaseReActAgent` loads MCP tools lazily and upstream's guard would fail every mcp-selected spec (design.md's recorded remedy for the `loaded_mcp_tools` lazy-build timing risk) |
| `src/evaluation/qa/schema.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/scoring.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/tool_traces.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/validation.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/worker.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/workflow.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/evaluation/qa/workspace.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/interfaces/chat_app/api.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/app.py` | M | yes | port-hunks | thin call sites only; logic in the tested seam module |
| `src/interfaces/chat_app/evaluation_routes.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/interfaces/chat_app/event_formatter.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/playbook_routes.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/static/chat.css` | M | no | skip-unrelated-upstream | verified in task 2.2: the pin's diff carries no eval hunk (console ships `evaluations.css`; the nav link reuses the existing `.header-tab` rules) |
| `src/interfaces/chat_app/static/chat.js` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/static/data.css` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/static/evaluations.css` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/interfaces/chat_app/static/evaluations.js` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/interfaces/chat_app/static/modules/ab-admin.js` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/static/modules/data-viewer.js` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/static/modules/database-viewer.js` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/static/upload.css` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/templates/ab_testing.html` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/templates/data.html` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/chat_app/templates/evaluations.html` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `src/interfaces/chat_app/templates/index.html` | M | yes | port-hunks | eval hunks only (nav link); fork-unmodified |
| `src/interfaces/jira.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/redmine_mailer_integration/mailbox.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/redmine_mailer_integration/redmine.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/interfaces/uploader_app/app.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/ab_agent_spec_service.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/ab_testing.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/config_service.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/conversation_service.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/env.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/jira.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/playbook_service.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/postgres_service_factory.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/rbac/permission_enum.py` | M | yes | port-hunks | eval hunks only (Evaluations permissions); fork-unmodified |
| `src/utils/rbac/permissions.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/sql.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `src/utils/user_service.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/smoke/init-test.sql` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/ui/data-viewer.spec.ts` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/ui/evaluation_test_server.py` | A | yes | omitted-optional | eval playwright fixture server; optional task 5.1 |
| `tests/ui/evaluation_test_worker.py` | A | yes | omitted-optional | eval playwright fixture worker; optional task 5.1 |
| `tests/ui/evaluations.spec.ts` | A | yes | omitted-optional | eval playwright spec; optional task 5.1, outside the gate |
| `tests/ui/fixtures.ts` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/ui/workflows/05-providers.spec.ts` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/ui/workflows/08-settings.spec.ts` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/ui/workflows/20-data-management.spec.ts` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/ui/workflows/21-ab-testing.spec.ts` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/archi/pipelines/agents/test_base_react.py` | A | yes | port-hunks | callbacks subset only, reshaped to one real-seam test |
| `tests/unit/archi/pipelines/agents/test_mcp_utils.py` | A | yes | skip-dead-on-fork | covers upstream mcp_utils.py hunks not ported |
| `tests/unit/conftest.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/evaluation/qa/fake_mcp_server.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_artifacts.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_catalog.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_cli.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_dataset_gateway.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_jobs_history.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_live_catalog.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_live_workflow.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_oracle_config.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_oracle_results.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_phases.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_preparation.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_profile.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_runtime.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_schema.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_scoring.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_tool_traces.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_validation.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_worker.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_workflow.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/test_workspace.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/worker_process.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/evaluation/qa/worker_support.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/test_ab_admin_page.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_ab_agent_spec_service.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_ab_pending_limit.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_ab_testing.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_base_compose_mcp_mounts.py` | A | yes | port-hunks | adapted: kept the evaluation-mount cases, dropped the `host_file_mounts` case (unported upstream MCP sidecar mounts) |
| `tests/unit/test_basic_auth_login.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_chat_app_authorization.py` | A | yes | skip-dead-on-fork | imports app.py / upstream authorize_request; fork uses the seam module |
| `tests/unit/test_chat_wrapper_playbooks.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_cli_restart_config_seed.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_config_seed.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_env.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_evaluation_config.py` | A | yes | port-hunks | adapted: imports the seam module, never app.py |
| `tests/unit/test_evaluation_config_staging.py` | A | yes | port-hunks | adapted: dropped the helm-configmap case with the helm branch (no helm tree on the fork) |
| `tests/unit/test_evaluation_routes.py` | A | yes | port-verbatim | eval-capability file; absent on the fork |
| `tests/unit/test_import_boundaries.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_jira.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_mcp.py` | A | yes | skip-dead-on-fork | covers upstream tools/mcp.py hunks not ported (eval runtime does not import them) |
| `tests/unit/test_persistence_service_size_bytes.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_playbook_ab_activity.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_playbook_activity_step.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_playbook_routes.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_playbook_service.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_playbook_tools.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_postgres_services.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_service_chat_bootstrap.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_service_jira.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_templates_manager_ab_agents.py` | A | no | skip-dead-on-fork | verdict (task 2.3): all three cases cover A/B agents staging and the A/B-era benchmarking-flag refactor; the fork has no `ab_testing`/`ab_agents_dir` machinery and none of it was ported. `evaluation_mcp_configured=False` in its fake context is scaffolding, not coverage |
| `tests/unit/test_templates_mcp_copy.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_utils_jira.py` | A | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
| `tests/unit/test_vectorstore_manager_batch_commit.py` | M | no | skip-unrelated-upstream | upstream-main work outside the eval scope |
