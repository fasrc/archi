#!/usr/bin/env bash
# Self-test for firewall.sh: exercises the rule-application contract against a
# fake iptables binary — no root, no network, never touches the real firewall.
#    1. missing rules are inserted
#    2. existing rules are NOT re-inserted (idempotent)
#    3. rules land ahead of the terminal REJECT, not at a hardcoded position
#    4. --dry-run mutates nothing
#    5. a chain with no terminal REJECT appends instead
#    6. multi-port entries render as -m multiport --dports
#    7. every rule carries a comment
#    8. a failing iptables call aborts non-zero
# Run: bash deploy/scripts/test_firewall.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIREWALL="$SCRIPT_DIR/firewall.sh"
PASS=0; FAIL=0
ok()    { printf 'ok - %s\n' "$1"; PASS=$((PASS + 1)); }
notok() { printf 'not ok - %s\n' "$1"; FAIL=$((FAIL + 1)); }

TESTROOT="$(mktemp -d)"
trap 'rm -rf "$TESTROOT"' EXIT

# A chain shaped like the real host: managed rules, then a terminal REJECT.
chain_with_reject() {
  cat <<'EOF'
Chain INPUT (policy ACCEPT)
num  target     prot opt source               destination
1    ACCEPT     icmp --  0.0.0.0/0            0.0.0.0/0            /* 0000 accept all icmp */
2    ACCEPT     all  --  0.0.0.0/0            0.0.0.0/0            /* 0001 accept all from lo interface */
3    REJECT     all  --  0.0.0.0/0            127.0.0.0/8          /* 0002 reject local traffic not on loopback interface */ reject-with icmp-port-unreachable
4    ACCEPT     all  --  0.0.0.0/0            0.0.0.0/0            state RELATED,ESTABLISHED /* 0003 accept established */
5    ACCEPT     all  --  10.255.12.0/26       0.0.0.0/0            state NEW /* 0010 HPRC VPN net */
6    REJECT     all  --  0.0.0.0/0            0.0.0.0/0            /* 8998 input reject all */ reject-with icmp-port-unreachable
EOF
}

# Same, minus the terminal REJECT.
chain_no_reject() {
  chain_with_reject | grep -v '8998 input reject all'
}

# Fake iptables. Serves canned -L output, records every call, and reports a rule
# as already-present (-C exit 0) only if its port+source appears in $STUB_EXISTING.
make_stub() { # $1 = sandbox, $2 = name of chain fixture function
  local sb="$1" chain_fn="$2"
  "$chain_fn" > "$sb/chain.txt"
  cat > "$sb/iptables" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$sb/calls"
case "\$1" in
  -L) cat "$sb/chain.txt"; exit 0 ;;
  -C) for tok in \$STUB_EXISTING; do
        [[ "\$*" == *"\$tok"* ]] && exit 0
      done
      exit 1 ;;
esac
exit \${STUB_MUTATE_RC:-0}
EOF
  chmod +x "$sb/iptables"
  : > "$sb/calls"
}

# Run firewall.sh with the stub wired in. Echoes the exit code; output in $sb/out.
run_fw() { # $1 = sandbox, rest = script args / env
  local sb="$1"; shift
  local ec=0
  env STUB_EXISTING="${STUB_EXISTING:-}" STUB_MUTATE_RC="${STUB_MUTATE_RC:-0}" \
      IPTABLES="$sb/iptables" \
      bash "$FIREWALL" "$@" > "$sb/out" 2>&1 || ec=$?
  echo "$ec"
}

# --- 1: missing rules are inserted --------------------------------------------
sb="$TESTROOT/insert"; mkdir -p "$sb"; make_stub "$sb" chain_with_reject
ec="$(STUB_EXISTING="" run_fw "$sb")"
if [ "$ec" = 0 ] \
   && grep -q -- "-I INPUT" "$sb/calls" \
   && grep -q -- "10.1.4.0/22" "$sb/calls" \
   && grep -q -- "10.1.16.0/22" "$sb/calls" \
   && grep -q -- "10.255.8.0/22" "$sb/calls"; then
  ok "1 missing rules: inserted for every configured source"
else
  notok "1 missing rules (ec=$ec)"; cat "$sb/out" || true
fi

# --- 2: existing rules are not re-inserted -------------------------------------
sb="$TESTROOT/idempotent"; mkdir -p "$sb"; make_stub "$sb" chain_with_reject
ec="$(STUB_EXISTING="10.1.4.0/22" run_fw "$sb")"
if [ "$ec" = 0 ] \
   && ! grep -- "-I INPUT" "$sb/calls" | grep -q -- "10.1.4.0/22" \
   && grep -- "-I INPUT" "$sb/calls" | grep -q -- "10.1.16.0/22"; then
  ok "2 existing rule: skipped, others still inserted"
else
  notok "2 idempotency (ec=$ec)"; cat "$sb/out" || true
fi

# --- 3: insert position is the terminal REJECT's line, not a constant ----------
# The fixture's terminal REJECT is line 6, so inserts must target 6 (not 12).
sb="$TESTROOT/position"; mkdir -p "$sb"; make_stub "$sb" chain_with_reject
ec="$(STUB_EXISTING="" run_fw "$sb")"
if [ "$ec" = 0 ] \
   && grep -q -- "-I INPUT 6 " "$sb/calls" \
   && ! grep -q -- "-I INPUT 12 " "$sb/calls"; then
  ok "3 insert position: derived from the terminal REJECT line"
else
  notok "3 insert position (ec=$ec)"; grep -- "-I INPUT" "$sb/calls" || true
fi

# --- 4: --dry-run mutates nothing ----------------------------------------------
sb="$TESTROOT/dryrun"; mkdir -p "$sb"; make_stub "$sb" chain_with_reject
ec="$(STUB_EXISTING="" run_fw "$sb" --dry-run)"
if [ "$ec" = 0 ] \
   && ! grep -q -- "-I INPUT" "$sb/calls" \
   && grep -q -- "10.255.8.0/22" "$sb/out"; then
  ok "4 --dry-run: prints planned rules, runs no -I"
else
  notok "4 --dry-run (ec=$ec)"; cat "$sb/out" || true
fi

# --- 5: no terminal REJECT -> append -------------------------------------------
sb="$TESTROOT/noreject"; mkdir -p "$sb"; make_stub "$sb" chain_no_reject
ec="$(STUB_EXISTING="" run_fw "$sb")"
if [ "$ec" = 0 ] \
   && grep -q -- "-A INPUT" "$sb/calls" \
   && ! grep -q -- "-I INPUT" "$sb/calls"; then
  ok "5 no terminal REJECT: appends instead of inserting"
else
  notok "5 no terminal REJECT (ec=$ec)"; cat "$sb/out" || true
fi

# --- 6: multi-port entries use multiport ---------------------------------------
sb="$TESTROOT/multiport"; mkdir -p "$sb"; make_stub "$sb" chain_with_reject
ec="$(STUB_EXISTING="" run_fw "$sb")"
if [ "$ec" = 0 ] && grep -q -- "-m multiport --dports" "$sb/calls"; then
  ok "6 multi-port entry: rendered with -m multiport --dports"
else
  notok "6 multiport (ec=$ec)"; grep -- "-I INPUT" "$sb/calls" || true
fi

# --- 7: every inserted rule carries a comment -----------------------------------
sb="$TESTROOT/comments"; mkdir -p "$sb"; make_stub "$sb" chain_with_reject
ec="$(STUB_EXISTING="" run_fw "$sb")"
uncommented=0
while IFS= read -r line; do
  [[ "$line" == *"--comment"* ]] || uncommented=$((uncommented + 1))
done < <(grep -- "-I INPUT" "$sb/calls")
if [ "$ec" = 0 ] && [ "$uncommented" = 0 ]; then
  ok "7 comments: every inserted rule is labelled"
else
  notok "7 comments ($uncommented uncommented, ec=$ec)"
fi

# --- 8: a failing iptables call aborts ------------------------------------------
sb="$TESTROOT/failure"; mkdir -p "$sb"; make_stub "$sb" chain_with_reject
ec="$(STUB_EXISTING="" STUB_MUTATE_RC=1 run_fw "$sb")"
if [ "$ec" != 0 ] && grep -q -- "-I INPUT" "$sb/calls"; then
  ok "8 iptables failure: aborts non-zero"
else
  notok "8 iptables failure (ec=$ec, insert attempted: $(grep -c -- '-I INPUT' "$sb/calls"))"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
