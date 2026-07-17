#!/usr/bin/env bash
# Shared config + helpers for managing the archi 'dev' deployment on localhost.
# Sourced by create.sh / redeploy.sh / nuke.sh / status.sh — not run directly.
# Deploys provision the config/ checkout (fasrc/archi-config) at a pinned,
# SHA-verified ref via ensure_config — see the "config repo pin" section for
# the bump procedure, and test_ensure_config.sh for the executable contract.
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

# --- config repo pin (fasrc/archi-config) ------------------------------------
# The config/ checkout (source lists, environments, agent prompts) is provisioned
# by ensure_config on every deploy at a pinned, content-addressed ref: CONFIG_REF
# names the tag, CONFIG_SHA is the commit it must resolve to (a re-pointed tag
# aborts the deploy instead of being believed).
# Pin bump procedure: create a NEW annotated tag in fasrc/archi-config (never
# move an existing one — `git fetch --tags` refuses to clobber a moved tag), then
# update CONFIG_REF + CONFIG_SHA here in the same PR. After bumping, deploy with
# a clean config/ tree (or CONFIG_FORCE=1) so the checkout actually converges.
# One-off override: CONFIG_REF=... CONFIG_SHA=... ./redeploy.sh
# CONFIG_REPO/CONFIG_DIR are overridable so test_ensure_config.sh can run
# against a local fixture instead of the real remote/checkout.
CONFIG_REPO="${CONFIG_REPO:-git@github.com:fasrc/archi-config.git}"
CONFIG_REF="${CONFIG_REF:-deploy-pin-2026-07}"
CONFIG_SHA="${CONFIG_SHA:-990c54c761ce83bce311d00a2576d58fd8af517b}"
CONFIG_DIR="${CONFIG_DIR:-$REPO_ROOT/config}"

# Resolve the secrets file: absolute path used as-is, relative path is repo-relative.
case "$ENV_FILE" in
  /*) ENV_FILE_ABS="$ENV_FILE" ;;
  *)  ENV_FILE_ABS="$REPO_ROOT/$ENV_FILE" ;;
esac
VERBOSITY="3"

# Chat UI port, read from the chat_app external_port in the config so it never
# drifts from the rendered compose (per-host: 7861 here, 7866 in the example).
# Scoped to the chat_app block (service keys are 2-space indented, nested keys
# 4-space): reset when the next service key appears so a chat_app that omits
# external_port does NOT leak the next service's port (e.g. data_manager 7889) —
# it falls through to 7861, matching the base-config default (base-config.yaml).
# Also the fallback when the git-excluded config.yaml is absent.
CHAT_PORT="$(awk '
  /^  chat_app:/ {f=1; next}
  f && /^  [^ ]/ {f=0}
  f && /external_port:/ {print $2; exit}
' "$REPO_ROOT/$CONFIG" 2>/dev/null | grep -oE '[0-9]+' || true)"
CHAT_URL="http://localhost:${CHAT_PORT:-7861}"

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

# --- config repo provisioning -------------------------------------------------
# Provision $CONFIG_DIR at the pin. Dirty trees WIN by default: operators
# live-edit agent prompts (bind-mounted into containers) and keep untracked
# local files (benchmarking banks) here — a blind checkout would destroy work,
# so a dirty tree is warned about (with pin drift) and deployed as-is unless
# CONFIG_FORCE=1, which stashes (recoverable) and never uses reset/clean.
ensure_config() {
  if [ ! -e "$CONFIG_DIR/.git" ]; then
    log "config/ absent — cloning $CONFIG_REPO…"
    git clone --quiet "$CONFIG_REPO" "$CONFIG_DIR" \
      || die "could not clone $CONFIG_REPO into $CONFIG_DIR"
  else
    # Tolerate a failed fetch (transient network): the SHA check below still
    # verifies whatever we have locally; only an unresolvable ref is fatal.
    git -C "$CONFIG_DIR" fetch --tags --quiet origin 2>/dev/null \
      || warn "config: fetch from origin failed (offline?); using local refs"
  fi

  local resolved
  resolved="$(git -C "$CONFIG_DIR" rev-parse "$CONFIG_REF^{commit}" 2>/dev/null)" \
    || die "config ref '$CONFIG_REF' does not resolve in $CONFIG_DIR (bad ref, or fetch needed?)"
  [ "$resolved" = "$CONFIG_SHA" ] \
    || die "config pin mismatch: $CONFIG_REF resolves to $resolved, expected $CONFIG_SHA — re-pointed tag? Refusing to deploy."

  local dirty head
  dirty="$(git -C "$CONFIG_DIR" status --porcelain)"
  head="$(git -C "$CONFIG_DIR" rev-parse HEAD)"

  if [ -z "$dirty" ]; then
    if [ "$head" != "$CONFIG_SHA" ]; then
      log "config: clean tree — converging to $CONFIG_REF ($CONFIG_SHA)"
      git -C "$CONFIG_DIR" checkout --quiet "$CONFIG_SHA"
      head="$CONFIG_SHA"
    fi
  elif [ "${CONFIG_FORCE:-0}" = "1" ]; then
    warn "config: dirty tree — CONFIG_FORCE=1: stashing live edits before converging."
    warn "config: recover them with: git -C $CONFIG_DIR stash pop"
    git -C "$CONFIG_DIR" stash push --include-untracked \
      -m "ensure_config forced update $(date +%F)" >/dev/null
    git -C "$CONFIG_DIR" checkout --quiet "$CONFIG_SHA"
    head="$CONFIG_SHA"
  else
    warn "config: dirty tree — leaving it untouched; deploying with the ON-DISK config, not the pin. Dirty paths:"
    printf '%s\n' "$dirty" >&2
    local behind
    behind="$(git -C "$CONFIG_DIR" rev-list --count "HEAD..$CONFIG_SHA" 2>/dev/null || echo '?')"
    warn "config: pin drift: HEAD is $behind commit(s) behind $CONFIG_REF; diffstat vs pin:"
    git -C "$CONFIG_DIR" diff --stat "$CONFIG_SHA" >&2 || true
    warn "config: reconverge with CONFIG_FORCE=1 (stashes edits) or commit-and-sync them."
  fi

  # Provenance: every deploy records the exact config state it ran with.
  local match=no
  [ "$head" = "$CONFIG_SHA" ] && match=yes
  log "config provenance: HEAD=$head pin=$CONFIG_REF@$CONFIG_SHA match=$match"
  if [ -n "$dirty" ]; then
    log "config provenance: dirty paths (name-status):"
    printf '%s\n' "$dirty"
  fi

  # A bad ref/partial checkout must die before `archi create` bakes it in.
  local f
  for f in lists/sources.list environments/dev.yaml agents; do
    [ -e "$CONFIG_DIR/$f" ] \
      || die "config checkout is missing expected path: $f (wrong ref content?)"
  done
}

# The actual deploy. --force makes this idempotent: first run creates, repeat
# runs rebuild images + re-render config + restart. Volumes (DB + corpus) are
# preserved across create/--force; only nuke.sh removes them.
archi_deploy() {
  require_archi; require_files
  ensure_config   # provision config/ at the pin BEFORE rendering the deployment
  check_llm
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
