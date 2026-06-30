#!/usr/bin/env bash
# Shared config + helpers for managing the archi 'dev' deployment on localhost.
# Sourced by create.sh / redeploy.sh / nuke.sh / status.sh — not run directly.
set -euo pipefail

# Resolve the repo root from this file's location (deploy/fasrc-dev/scripts/),
# so the scripts work regardless of the current working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# --- deployment identity (single source of truth) ---------------------------
DEPLOYMENT="dev"                              # archi deployment name -> archi-dev
CONFIG="deploy/fasrc-dev/config.yaml"         # repo-relative (git-excluded; copy from config.example.yaml)
# Secrets env (PG_PASSWORD, HUIT_API_KEY, ...). Override with ARCHI_ENV_FILE;
# defaults to ~/.secrets/archi-secrets.env so the scripts aren't tied to one user.
ENV_FILE="${ARCHI_ENV_FILE:-$HOME/.secrets/archi-secrets.env}"
SERVICES="chatbot"                            # auto-pulls postgres + data-manager

# Resolve the secrets file: absolute path used as-is, relative path is repo-relative.
case "$ENV_FILE" in
  /*) ENV_FILE_ABS="$ENV_FILE" ;;
  *)  ENV_FILE_ABS="$REPO_ROOT/$ENV_FILE" ;;
esac
VERBOSITY="3"
CHAT_URL="http://localhost:7866"

# FASRC vLLM endpoint (scheme://host:port), read from the base_url line in the
# config so it never drifts. Matches both hostnames and IPs.
LLM_URL="$(grep -E '^[[:space:]]*base_url:' "$REPO_ROOT/$CONFIG" 2>/dev/null \
  | grep -oE 'http://[A-Za-z0-9_.-]+:[0-9]+' | head -1 || true)"

# --- logging ----------------------------------------------------------------
log()  { printf '\033[1;34m[archi-%s]\033[0m %s\n' "$DEPLOYMENT" "$*"; }
warn() { printf '\033[1;33m[archi-%s] WARN:\033[0m %s\n' "$DEPLOYMENT" "$*" >&2; }
die()  { printf '\033[1;31m[archi-%s] ERROR:\033[0m %s\n' "$DEPLOYMENT" "$*" >&2; exit 1; }

# --- preflight --------------------------------------------------------------
require_archi() { command -v archi >/dev/null 2>&1 || die "archi CLI not found on PATH"; }

require_files() {
  [ -f "$REPO_ROOT/$CONFIG" ] || die "config not found: $CONFIG"
  [ -f "$ENV_FILE_ABS" ]      || die "secrets not found: $ENV_FILE_ABS"
}

# Soft check: the LLM endpoint is VPN-only. Warn (don't block) if unreachable —
# the deploy will still come up, but chat won't work until the VPN is connected.
check_llm() {
  [ -n "$LLM_URL" ] || { warn "could not read LLM base_url from $CONFIG"; return 0; }
  if curl -sS -m 6 -o /dev/null "$LLM_URL/v1/models" 2>/dev/null; then
    log "LLM endpoint reachable: $LLM_URL"
  else
    warn "LLM endpoint $LLM_URL is unreachable — is the FASRC VPN up? Deploy will proceed; chat stays down until it is."
  fi
}

# The actual deploy. --force makes this idempotent: first run creates, repeat
# runs rebuild images + re-render config + restart. Volumes (DB + corpus) are
# preserved across create/--force; only nuke.sh removes them.
archi_deploy() {
  require_archi; require_files; check_llm
  cd "$REPO_ROOT"
  log "Deploying (hostmode, --force; data volumes preserved)…"
  archi create \
    --name "$DEPLOYMENT" \
    --config "$CONFIG" \
    --env-file "$ENV_FILE_ABS" \
    --services "$SERVICES" \
    --hostmode \
    --force \
    -v "$VERBOSITY"
  log "Up. Chat UI: $CHAT_URL"
}
