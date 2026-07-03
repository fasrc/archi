#!/usr/bin/env bash
# Re-deploy: rebuild images, re-render config, restart services — picking up
# any edits to config.yaml / secrets.env / source code. Data volumes (Postgres
# DB + ingested corpus) are PRESERVED.
#
# Mechanically this is the same `archi create --force` as create.sh (archi has
# no separate redeploy verb); it's provided as a distinct, clearly-named action.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "Re-deploying (rebuild + restart, data preserved)…"
archi_deploy

# Guarantee the config change actually reaches the running chat process.
# `archi create` re-seeds Postgres static_config via the one-shot `config-seed`
# container and its default flags force-recreate chatbot — but that guarantee
# evaporates if the ambient ARCHI_COMPOSE_UP_FLAGS drops --force-recreate (as CI
# does) or if an operator later runs a bare `docker restart chatbot-dev` (which
# re-reads Postgres but does NOT re-run the one-shot seed, so it can serve stale
# config). This explicit, targeted recreate closes both holes deterministically:
# re-run config-seed (pushes the new YAML into static_config), then recreate
# chatbot (re-reads it at boot). Compose honors the config-seed -> chatbot
# dependency ordering. Scoped to just these two services — postgres and
# data-manager are NOT named and --always-recreate-deps is NOT passed, so the
# DB/corpus containers (and their volumes) are never bounced.
COMPOSE_FILE="$HOME/.archi/archi-$DEPLOYMENT/compose.yaml"
log "Forcing re-seed + chat restart (config-seed, chatbot)…"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE_ABS" \
  up -d --force-recreate config-seed chatbot
