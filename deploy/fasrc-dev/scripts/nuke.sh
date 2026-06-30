#!/usr/bin/env bash
# Complete nuke of the archi 'dev' deployment — DESTRUCTIVE, IRREVERSIBLE.
# Removes: containers, volumes (Postgres DB + ingested corpus), per-deployment
# images, and the ~/.archi/archi-dev/ files. Your other deployments are NOT
# touched (the name is hard-wired to 'dev' in lib.sh).
#
# Usage: nuke.sh [-y|--yes]   (-y skips the interactive confirmation)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_archi
cd "$REPO_ROOT"

assume_yes=0
case "${1:-}" in
  -y|--yes) assume_yes=1 ;;
  "")       ;;
  *)        die "unknown argument: $1 (use -y/--yes or no argument)" ;;
esac

warn "This will PERMANENTLY destroy the '$DEPLOYMENT' deployment:"
printf '    - containers:  chatbot/data-manager/postgres for %s\n' "$DEPLOYMENT"
printf '    - volumes:     archi-*-%s  (Postgres DB + ingested corpus WIPED)\n' "$DEPLOYMENT"
printf '    - images:      per-deployment images (next create does a full rebuild)\n'
printf '    - files:       ~/.archi/archi-%s/\n' "$DEPLOYMENT"
printf '    Other deployments are NOT affected.\n'

if [ "$assume_yes" -ne 1 ]; then
  printf 'Type the deployment name (%s) to confirm: ' "$DEPLOYMENT"
  read -r reply
  [ "$reply" = "$DEPLOYMENT" ] || die "confirmation did not match ('$reply' != '$DEPLOYMENT'); aborted."
fi

log "Nuking '$DEPLOYMENT'…"
archi delete --name "$DEPLOYMENT" --rmi --rmv -v "$VERBOSITY"
log "Deployment '$DEPLOYMENT' destroyed."
