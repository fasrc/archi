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
