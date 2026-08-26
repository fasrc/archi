#!/usr/bin/env bash
# Idempotently apply the archi service firewall rules to the INPUT chain.
#
# WHY THIS EXISTS: the host's base ruleset is config-managed (puppet — see the
# numeric `0000`/`0010` comment prefixes on rules it owns). The archi service
# ports are NOT in that managed set, so they are hand-added and can be lost to a
# host rebuild, or purged by a puppet run if the firewall class runs with
# `purge => true`. This script is the reproducible record of what to re-add.
#
# It is NOT wired into create/redeploy: opening ports is a privileged, host-wide
# action that must stay an explicit human decision, not a deploy side effect.
#
# The durable fix is to have these ports added to the puppet-managed set by
# FASRC ops; until then, re-run this after any rebuild and verify with --list.
#
# Usage:
#   deploy/scripts/firewall.sh --dry-run   # show what would change
#   deploy/scripts/firewall.sh             # apply (needs sudo)
#   deploy/scripts/firewall.sh --list      # show archi rules in place
#
# Rules are runtime-only until persisted (e.g. `service iptables save`).
set -euo pipefail

IPTABLES="${IPTABLES:-sudo iptables}"

# VPN source networks. Named so the rule table reads as intent, not CIDRs.
readonly VPN_HARVARD_GENCOM_A="10.1.4.0/22"
readonly VPN_HARVARD_GENCOM_B="10.1.16.0/22"
readonly VPN_FASRC="10.255.8.0/22"
readonly VPN_RC_ADMIN="10.255.13.96/27"

# port(s) | source CIDR | comment
# Comma-separated ports render as a single multiport rule.
FW_RULES=(
  # Chat UI — open to the broad user VPNs.
  "7861|$VPN_HARVARD_GENCOM_A|archi-chatbot 7861 from Harvard gencom VPN"
  "7861|$VPN_HARVARD_GENCOM_B|archi-chatbot 7861 from Harvard gencom VPN"
  "7861|$VPN_FASRC|archi-chatbot 7861 from FASRC VPN"
  # Everything else stays admin-VPN only.
  "7861|$VPN_RC_ADMIN|archi-chatbot from admin VPN"
  "7866|$VPN_RC_ADMIN|archi-chatbot 7866 from admin VPN"
  "8001|$VPN_RC_ADMIN|archi-vllm from admin VPN"
  "6900|$VPN_RC_ADMIN|argilla from admin VPN"
  "80|$VPN_RC_ADMIN|homer dashboard from admin VPN"
  # 7881 is the RAGAS benchmark host-side mapping (see service_benchmark.py).
  "3000,3080,7881,8000,8080,9100,9400,11434|$VPN_RC_ADMIN|0014 archi services from admin VPN"
)
# DELIBERATELY NOT OPENED: data_manager (7889). It listens on 0.0.0.0 with
# `auth.enabled: false`, so the terminal REJECT is the only thing keeping an
# unauthenticated ingestion API off the network. Opening it is a security
# decision that needs auth in front of it first — not a transcription fix.
# This table records the host's intended posture; it is not derived from
# config.yaml on purpose, so that editing app config can never silently
# widen the firewall.

log() { printf '%s\n' "$*"; }

# Line number of the chain-terminating `REJECT ... 0.0.0.0/0 0.0.0.0/0`, so new
# rules land ahead of it. Prints nothing when the chain has no such rule.
# Reads the listing on stdin. The earlier REJECT to 127.0.0.0/8 is not terminal
# and must not match — hence the destination check.
terminal_reject_line() {
  awk '$2 == "REJECT" && $5 == "0.0.0.0/0" && $6 == "0.0.0.0/0" { print $1; exit }'
}

# Render the match part of a rule: single --dport, or multiport for a list.
port_match() { # $1 = port spec
  if [[ "$1" == *,* ]]; then
    printf -- '-m multiport --dports %s' "$1"
    return
  fi
  printf -- '--dport %s' "$1"
}

main() {
  local mode="apply"
  case "${1:-}" in
    --dry-run) mode="dry-run" ;;
    --list)    mode="list" ;;
    "")        ;;
    *) log "usage: $(basename "$0") [--dry-run|--list]"; exit 2 ;;
  esac

  if [ "$mode" = "list" ]; then
    $IPTABLES -L INPUT -n --line-numbers | grep -E "archi|argilla|homer" || log "(no archi rules present)"
    return 0
  fi

  local chain insert_at flag
  chain="$($IPTABLES -L INPUT -n --line-numbers)"
  insert_at="$(printf '%s\n' "$chain" | terminal_reject_line)"
  if [ -n "$insert_at" ]; then
    flag="-I INPUT $insert_at"
    log "# inserting ahead of the terminal REJECT at line $insert_at"
  else
    # No terminal REJECT to get ahead of — order is irrelevant, so append.
    flag="-A INPUT"
    log "# no terminal REJECT found; appending"
  fi

  local added=0 present=0
  for rule in "${FW_RULES[@]}"; do
    local ports src comment match
    IFS='|' read -r ports src comment <<< "$rule"
    match="$(port_match "$ports")"

    # shellcheck disable=SC2086  # $match is a deliberate multi-word fragment
    if $IPTABLES -C INPUT -p tcp -s "$src" $match -j ACCEPT \
         -m comment --comment "$comment" >/dev/null 2>&1; then
      present=$((present + 1))
      continue
    fi

    if [ "$mode" = "dry-run" ]; then
      log "would add: $ports <- $src  ($comment)"
      added=$((added + 1))
      continue
    fi

    log "adding: $ports <- $src  ($comment)"
    # shellcheck disable=SC2086  # $flag and $match are deliberate fragments
    $IPTABLES $flag -p tcp -s "$src" $match -j ACCEPT \
      -m comment --comment "$comment"
    added=$((added + 1))
  done

  log ""
  if [ "$mode" = "dry-run" ]; then
    log "$present already present, $added would be added (nothing changed)"
  else
    log "$present already present, $added added"
    [ "$added" -gt 0 ] && log "NOTE: runtime-only until persisted (e.g. 'service iptables save')"
  fi
  return 0
}

main "$@"
