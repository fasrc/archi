#!/usr/bin/env bash
# Read-only status of the archi 'dev' deployment: containers, volumes, UI, LLM.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "Containers:"
docker ps -a --filter "name=$DEPLOYMENT" \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true

log "Volumes:"
docker volume ls --filter "name=archi-" --format '{{.Name}}' \
  | grep -E -- "-$DEPLOYMENT\$|^archi-$DEPLOYMENT\$" || echo "  (none)"

log "Chat UI:"
curl -sS -m 6 -o /dev/null -w "  $CHAT_URL -> HTTP %{http_code}\n" "$CHAT_URL" \
  || echo "  $CHAT_URL -> unreachable"

check_llm
