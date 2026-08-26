#!/usr/bin/env bash
# Shared config + helpers for managing this host's archi deployment (named by
# $DEPLOYMENT — default 'dev', per-host via host.env; see host.env.example).
# Sourced by create.sh / redeploy.sh / nuke.sh / status.sh — not run directly.
# Deploys provision the config/ checkout (fasrc/archi-config) at a pinned,
# SHA-verified ref via ensure_config — see the "config repo pin" section for
# the bump procedure, and test_ensure_config.sh for the executable contract.
set -euo pipefail

# Resolve the repo root from this file's location (deploy/scripts/),
# so the scripts work regardless of the current working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Optional per-host overrides (git-excluded; see host.env.example), read
# before the defaults below so a host can pin its own identity without editing
# this tracked file. host.env is DATA, not code: it is parsed, never sourced,
# so nothing in it can execute, and only KEY=VALUE lines for the allowlist
# below are accepted — the config pin (CONFIG_REF/CONFIG_SHA/...) stays
# overridable per-invocation only, never from a persistent git-excluded file.
# A value applies only when the variable is not already set, so an explicit
# environment variable on the command line always wins. Anything else in the
# file aborts EVERY consumer of lib.sh — status.sh and nuke.sh included: with
# an unparsable host.env the deployment identity is ambiguous, and a teardown
# on an ambiguous identity is how the wrong deployment dies. Fail closed; the
# error names the offending line. Contract pinned by test_host_env.sh.
_host_env_file="$SCRIPT_DIR/host.env"
if [ -f "$_host_env_file" ]; then
  while IFS= read -r _he_line || [ -n "$_he_line" ]; do
    _he_line="${_he_line%$'\r'}"                              # tolerate CRLF
    _he_line="${_he_line#"${_he_line%%[![:space:]]*}"}"       # trim leading ws
    _he_line="${_he_line%"${_he_line##*[![:space:]]}"}"       # trim trailing ws
    case "$_he_line" in
      ''|\#*) continue ;;
      DEPLOYMENT=*|CONFIG=*|GPU_IDS=*)
        _he_key="${_he_line%%=*}"
        _he_val="${_he_line#*=}"
        # A duplicate key makes the identity ambiguous (first-wins would let a
        # stale line silently shadow an appended correction) — fail closed.
        case " ${_he_seen:-} " in
          *" $_he_key "*)
            printf 'host.env: duplicate %s line — ambiguous, refusing: %s\n' \
              "$_he_key" "$_he_line" >&2
            exit 1 ;;
        esac
        _he_seen="${_he_seen:-} $_he_key"
        # An empty identity value is never valid — it would fall through to the
        # reserved default and silently retarget. (GPU_IDS= empty is the
        # documented explicit disable, so it is allowed.)
        if [ "$_he_key" != "GPU_IDS" ] && [ -z "$_he_val" ]; then
          printf 'host.env: empty %s is not a valid identity — set a value or remove the line\n' \
            "$_he_key" >&2
          exit 1
        fi
        # Identity keys: an EMPTY environment value counts as unset (an empty
        # name or path is never valid), so an ambient DEPLOYMENT='' cannot
        # bypass the host pin. GPU_IDS: set-but-empty IS meaningful, set wins.
        case "$_he_key" in
          DEPLOYMENT) [ -n "${DEPLOYMENT:-}" ] || DEPLOYMENT="$_he_val" ;;
          CONFIG)     [ -n "${CONFIG:-}" ]     || CONFIG="$_he_val" ;;
          GPU_IDS)    [ "${GPU_IDS+x}" ]       || GPU_IDS="$_he_val" ;;
        esac ;;
      *) printf 'host.env: unsupported line (allowed: DEPLOYMENT=, CONFIG=, GPU_IDS=, comments): %s\n' \
           "$_he_line" >&2
         exit 1 ;;
    esac
  done < "$_host_env_file"
fi
unset _host_env_file _he_line _he_key _he_val _he_seen

# --- deployment identity (single source of truth) ---------------------------
# `dev` is reserved for the GPU host; the no-GPU workstation deploys as `claw`
# via its host.env (issue #363).
DEPLOYMENT="${DEPLOYMENT:-dev}"               # archi deployment name -> archi-<name>
CONFIG="${CONFIG:-deploy/fasrc-dev/config.yaml}"  # repo-relative (git-excluded; copy from config.example.yaml)
# The name becomes ~/.archi/archi-<name>, and `archi create --force` (which
# every deploy here passes) rmtree's that directory — so a name carrying a
# path separator must never get that far: DEPLOYMENT=dev/../.. resolves the
# deployment dir to $HOME. One safe token, wherever the name came from
# (tracked default, host.env, or the environment). Pinned by test_host_env.sh.
case "$DEPLOYMENT" in
  *[!A-Za-z0-9_-]*)
    printf 'DEPLOYMENT %s is not a valid deployment name (allowed: letters, digits, - and _)\n' \
      "'$DEPLOYMENT'" >&2
    exit 1 ;;
esac
# Secrets env (PG_PASSWORD, HUIT_API_KEY, ...). Override with ARCHI_ENV_FILE;
# defaults to ~/.secrets/archi-secrets.env so the scripts aren't tied to one user.
ENV_FILE="${ARCHI_ENV_FILE:-$HOME/.secrets/archi-secrets.env}"
SERVICES="chatbot"                            # auto-pulls postgres + data-manager
# GPU(s) to reserve for the data-manager embedding pass. Default OFF — and the
# reason is the containers, not the host: both are deliberately configured
# `device: cpu` (the chatbot ships CPU-only torch and would crash-loop on
# `cuda:0`), and the models are served by a remote vLLM endpoint. On the GPU
# host the GPUs exist but are owned by the vLLM servers, which pre-reserve
# their KV pool at startup. On a host WITHOUT the nvidia container runtime a
# non-empty value is worse than useless: it renders `driver: nvidia, count: all`
# into the compose file, and the deploy fails with "could not select device
# driver nvidia" *after* recreating chatbot — taking the deployment down
# instead of failing before it touches anything. That is why OFF is the safe
# shared default. Set GPU_IDS=0 (or a list) only on a host that has GPUs, the
# nvidia runtime, and containers configured to use them. Contract pinned by
# test_gpu_flag.sh.
GPU_IDS="${GPU_IDS-}"

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
CONFIG_REF="${CONFIG_REF:-deploy-pin-2026-08a}"
CONFIG_SHA="${CONFIG_SHA:-889008390d4d3ca30b52282b83bb07fe5716c377}"
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
# live-edit agent prompts and keep untracked local files (benchmarking banks)
# here — a blind checkout would destroy work, so a dirty tree is warned about
# (with pin drift) and deployed as-is unless CONFIG_FORCE=1, which stashes
# (recoverable) and never uses reset/clean.
# Agents-dir note (verified 2026-07-16): on THIS host the deployment stages
# deploy/fasrc-dev/agents/ into the rendered dir (~/.archi/archi-dev/data/agents,
# bind-mounted rw) — config/agents/ is the config-repo copy that OTHER hosts
# bind-mount live (issue #99's capture); it is not consumed by this deployment.
ensure_config() {
  if [ ! -e "$CONFIG_DIR/.git" ]; then
    log "config/ absent — cloning $CONFIG_REPO…"
    git clone --quiet "$CONFIG_REPO" "$CONFIG_DIR" \
      || die "could not clone $CONFIG_REPO into $CONFIG_DIR"
  fi

  # Verify the REMOTE tag object first: a re-pointed remote tag must abort even
  # though `git fetch --tags` would refuse to clobber our (correct) local tag.
  # ls-remote failing entirely = offline; that's tolerated — the local SHA
  # check below still guards content.
  local remote_commit=""
  if remote_commit="$(git -C "$CONFIG_DIR" ls-remote origin \
        "refs/tags/$CONFIG_REF^{}" "refs/tags/$CONFIG_REF" 2>/dev/null \
        | tail -1 | cut -f1)"; then
    if [ -n "$remote_commit" ] && [ "$remote_commit" != "$CONFIG_SHA" ]; then
      die "config pin mismatch on REMOTE: $CONFIG_REF points at $remote_commit, expected $CONFIG_SHA — re-pointed tag? Refusing to deploy."
    fi
  else
    warn "config: could not reach origin (offline?); verifying local refs only"
  fi
  # Pick up new tags (e.g. a bumped pin). Rejection of a moved same-name tag is
  # non-fatal — the ls-remote check above already adjudicated tampering.
  git -C "$CONFIG_DIR" fetch --tags --quiet origin 2>/dev/null \
    || warn "config: fetch from origin failed; using local refs"

  local resolved
  resolved="$(git -C "$CONFIG_DIR" rev-parse "$CONFIG_REF^{commit}" 2>/dev/null)" \
    || die "config ref '$CONFIG_REF' does not resolve in $CONFIG_DIR (bad ref, or fetch needed?)"
  [ "$resolved" = "$CONFIG_SHA" ] \
    || die "config pin mismatch: $CONFIG_REF resolves to $resolved, expected $CONFIG_SHA — re-pointed tag? Refusing to deploy."

  local dirty tracked_dirty head drift stashed=""
  dirty="$(git -C "$CONFIG_DIR" status --porcelain)"
  tracked_dirty="$(printf '%s\n' "$dirty" | grep -v '^??' | grep -v '^$' || true)"
  head="$(git -C "$CONFIG_DIR" rev-parse HEAD)"
  # Symmetric drift: "ahead A, behind B" relative to the pin (P3 review fix —
  # `HEAD..pin` alone reports 0 for ahead/diverged checkouts).
  drift="$(git -C "$CONFIG_DIR" rev-list --left-right --count "HEAD...$CONFIG_SHA" 2>/dev/null \
    | awk '{print "ahead " $1 ", behind " $2}' || echo 'drift unknown')"

  if [ "$head" = "$CONFIG_SHA" ] && [ -n "$tracked_dirty" ]; then
    # Live edits ON the pin — the designed workflow. Never touch them.
    warn "config: tracked edits on top of the pin — leaving them; deploying the ON-DISK config. Dirty paths:"
    printf '%s\n' "$dirty" >&2
    warn "config: reconverge with CONFIG_FORCE=1 (stashes edits) or commit-and-sync them."
  elif [ "$head" != "$CONFIG_SHA" ]; then
    if [ -z "$tracked_dirty" ]; then
      # Clean or untracked-only: convergence is safe — git checkout never
      # touches untracked files (and aborts itself on a path collision).
      log "config: converging to $CONFIG_REF ($CONFIG_SHA) [$drift]"
      git -C "$CONFIG_DIR" checkout --quiet "$CONFIG_SHA" \
        || die "config: checkout of $CONFIG_SHA refused (untracked file collision?) — resolve manually or CONFIG_FORCE=1"
      head="$CONFIG_SHA"
    elif [ "${CONFIG_FORCE:-0}" = "1" ]; then
      warn "config: dirty tree off the pin — CONFIG_FORCE=1: stashing edits, then converging."
      warn "config: recover them with: git -C $CONFIG_DIR stash pop"
      git -C "$CONFIG_DIR" stash push --include-untracked \
        -m "ensure_config forced update $(date +%F)" >/dev/null
      stashed="$dirty"
      git -C "$CONFIG_DIR" checkout --quiet "$CONFIG_SHA"
      head="$CONFIG_SHA"
      dirty="$(git -C "$CONFIG_DIR" status --porcelain)"
    else
      # Tracked edits on a NON-pin base: deploying would silently run an old
      # base under local edits. Ambiguous — refuse (adversarial-review fix).
      warn "config: tracked edits on a checkout that is OFF the pin ($drift vs $CONFIG_REF). Dirty paths:"
      printf '%s\n' "$tracked_dirty" >&2
      die "config: refusing to deploy an off-pin base with local edits — commit-and-sync them, or CONFIG_FORCE=1 to stash and converge."
    fi
  fi

  # Provenance: every deploy records the exact config state it ran with.
  local match=no
  [ "$head" = "$CONFIG_SHA" ] && match=yes
  log "config provenance: HEAD=$head pin=$CONFIG_REF@$CONFIG_SHA match=$match"
  if [ -n "$stashed" ]; then
    log "config provenance: stashed pre-deploy edits (NOT in the deployed config):"
    printf '%s\n' "$stashed"
  elif [ -n "$dirty" ]; then
    log "config provenance: dirty paths (name-status):"
    printf '%s\n' "$dirty"
  fi

  # A bad ref/partial checkout must die before `archi create` bakes it in.
  # Type-strict (P3 review fix): a file named `agents` must not pass.
  [ -f "$CONFIG_DIR/lists/sources.list" ] \
    || die "config checkout is missing expected file: lists/sources.list (wrong ref content?)"
  [ -f "$CONFIG_DIR/environments/dev.yaml" ] \
    || die "config checkout is missing expected file: environments/dev.yaml (wrong ref content?)"
  [ -d "$CONFIG_DIR/agents" ] \
    || die "config checkout is missing expected directory: agents (wrong ref content?)"
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
  local -a gpu_flag=()
  [ -n "$GPU_IDS" ] && gpu_flag=(--gpu-ids "$GPU_IDS")
  archi create \
    --name "$DEPLOYMENT" \
    --config "$CONFIG" \
    --env-file "$ENV_FILE_ABS" \
    --services "$SERVICES" \
    --hostmode \
    --force \
    "${gpu_flag[@]}" \
    -v "$VERBOSITY"
  log "Up. Chat UI: $CHAT_URL"
}
