#!/usr/bin/env bash
# Self-test for the per-host override contract in lib.sh — exercised against a
# COPY of lib.sh in a temporary fixture tree and a fake `archi` binary, so it
# reads no real config, writes nothing into the working tree, renders no
# compose, and starts no container.
#
# host.env is DATA, not code (adversarial-review round 1): only KEY=VALUE lines
# for DEPLOYMENT / CONFIG / GPU_IDS are accepted, a value applies only when the
# variable is not already set (command line always wins), and anything else
# aborts the deploy before it touches anything.
#    1. no host.env: DEPLOYMENT=dev, CONFIG=deploy/fasrc-dev/config.yaml
#    2. a missing host.env is not an error
#    3. host.env DEPLOYMENT=claw reaches `archi create --name`
#    4. host.env CONFIG=... reaches `--config`
#    5. a command-line environment variable beats host.env
#    6. host.env GPU_IDS= (empty) still passes no --gpu-ids (the no-colon
#       ${GPU_IDS-} keeps distinguishing unset from empty)
#    7. a key outside the allowlist (e.g. CONFIG_SHA) aborts every wrapper,
#       before archi is ever invoked (identity is ambiguous — fail closed)
#    8. a non-assignment line aborts AND is never executed
#    9. comments, indented comments, whitespace-only and blank lines are fine
#   10. CRLF line endings do not poison the value
#   11. leading/trailing whitespace around an assignment is ignored
#   12. an EMPTY command-line DEPLOYMENT does not bypass host.env (empty
#       counts as unset for the identity keys — an empty name is never valid)
#   13. same for CONFIG
#   14. an empty identity value IN host.env (DEPLOYMENT=) aborts — it must not
#       fall through to the reserved default
#   15. a duplicate identity key in host.env aborts — first-wins would let a
#       stale line silently shadow an appended correction
#   16. a DEPLOYMENT with a path separator aborts, from host.env — the name
#       becomes ~/.archi/archi-<name>, and `archi create --force` rmtree's
#       that directory, so `dev/../..` would resolve it to $HOME
#   17. same guard for an ambient environment DEPLOYMENT (no host.env)
#
# Run: bash deploy/scripts/test_host_env.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
ok()    { printf 'ok - %s\n' "$1"; PASS=$((PASS + 1)); }
notok() { printf 'not ok - %s\n' "$1"; FAIL=$((FAIL + 1)); }

TESTROOT="$(mktemp -d)"
trap 'rm -rf "$TESTROOT"' EXIT

# Fixture repo tree: lib.sh is COPIED in, so the SCRIPT_DIR it computes points
# at the fixture and the host.env this test writes never touches the real tree.
FIXSCRIPTS="$TESTROOT/repo/deploy/scripts"
mkdir -p "$FIXSCRIPTS"
cp "$SCRIPT_DIR/lib.sh" "$FIXSCRIPTS/lib.sh"

# Fake `archi` that records the argv it was called with and exits clean.
mkdir -p "$TESTROOT/bin"
cat > "$TESTROOT/bin/archi" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" > "$TESTROOT/argv"
exit 0
EOF
chmod +x "$TESTROOT/bin/archi"

# Invoke archi_deploy in a subshell with the fake archi on PATH and the
# preflight/provisioning steps stubbed out — this test is about identity only.
run_deploy() { # env assignments passed as "VAR=value" args
  : > "$TESTROOT/argv"
  # -u strips ambient identity vars so the suite tests its fixtures, not the
  # invoking shell; the case's own assignments (after -u) still apply.
  env -u DEPLOYMENT -u CONFIG -u GPU_IDS "$@" PATH="$TESTROOT/bin:$PATH" bash -c '
    source "'"$FIXSCRIPTS"'/lib.sh"
    require_files() { :; }
    ensure_config() { :; }
    check_llm()     { :; }
    archi_deploy >/dev/null 2>&1
  '
}

# --- 1 + 2: no host.env — reserved defaults, and no error --------------------
rm -f "$FIXSCRIPTS/host.env"
if run_deploy HOST_ENV_ABSENT=1; then
  ok "2 missing host.env is not an error"
else
  notok "2 missing host.env must not be an error"
fi
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name dev"* ]] && [[ "$argv" == *"--config deploy/fasrc-dev/config.yaml"* ]]; then
  ok "1 no host.env: DEPLOYMENT=dev, CONFIG=deploy/fasrc-dev/config.yaml"
else
  notok "1 no host.env should keep the reserved defaults, got: $argv"
fi

# --- 3: host.env renames the deployment --------------------------------------
printf 'DEPLOYMENT=claw\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name claw"* ]]; then
  ok "3 host.env DEPLOYMENT=claw reaches archi create --name"
else
  notok "3 host.env DEPLOYMENT should reach --name, got: $argv"
fi

# --- 4: host.env overrides CONFIG ---------------------------------------------
printf 'CONFIG=deploy/fasrc-dev/claw.yaml\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--config deploy/fasrc-dev/claw.yaml"* ]]; then
  ok "4 host.env CONFIG reaches --config"
else
  notok "4 host.env CONFIG should reach --config, got: $argv"
fi

# --- 5: the command line always beats host.env --------------------------------
printf 'DEPLOYMENT=claw\n' > "$FIXSCRIPTS/host.env"
run_deploy DEPLOYMENT=other
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name other"* ]]; then
  ok "5 command-line DEPLOYMENT beats host.env"
else
  notok "5 command-line env must beat host.env, got: $argv"
fi

# --- 6: GPU_IDS= (empty) through host.env still disables the flag -------------
printf 'GPU_IDS=\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1
argv="$(cat "$TESTROOT/argv")"
if [ -n "$argv" ] && [[ "$argv" != *"--gpu-ids"* ]]; then
  ok "6 host.env GPU_IDS= (empty): no --gpu-ids"
else
  notok "6 host.env GPU_IDS= should pass no --gpu-ids, got: $argv"
fi

# --- 7: a key outside the allowlist aborts ------------------------------------
printf 'CONFIG_SHA=deadbeef\n' > "$FIXSCRIPTS/host.env"
if run_deploy HOST_ENV_SET=1 2>/dev/null; then
  notok "7 an unsupported key (CONFIG_SHA) must abort"
else
  if [ -s "$TESTROOT/argv" ]; then
    notok "7 abort must happen BEFORE archi is invoked, got: $(cat "$TESTROOT/argv")"
  else
    ok "7 unsupported key aborts before archi is invoked"
  fi
fi

# --- 8: a non-assignment line aborts and is never executed --------------------
CANARY="$TESTROOT/canary"
: > "$CANARY"
printf 'rm -f %s\n' "$CANARY" > "$FIXSCRIPTS/host.env"
if run_deploy HOST_ENV_SET=1 2>/dev/null; then
  notok "8 a non-assignment line must abort the deploy"
else
  if [ -f "$CANARY" ]; then
    ok "8 non-assignment line aborts and is NOT executed"
  else
    notok "8 host.env content was EXECUTED (canary deleted)"
  fi
fi

# --- 9: comments, indented comments, whitespace-only and blank lines are fine --
printf '# per-host identity\n\n  # indented comment\n   \nDEPLOYMENT=claw\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1 || true
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name claw --config"* ]]; then
  ok "9 comments, indented comments and whitespace-only lines are accepted"
else
  notok "9 comment/whitespace lines should parse, got: $argv"
fi

# --- 10: CRLF line endings do not poison the value -----------------------------
printf 'DEPLOYMENT=claw\r\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1 || true
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name claw --config"* ]]; then
  ok "10 CRLF host.env: value is clean (no trailing CR)"
else
  notok "10 CRLF host.env poisoned the value, got: $argv"
fi

# --- 11: whitespace around an assignment is ignored ----------------------------
printf '  DEPLOYMENT=claw  \n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1 || true
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name claw --config"* ]]; then
  ok "11 leading/trailing whitespace around an assignment is ignored"
else
  notok "11 whitespace-padded assignment should parse cleanly, got: $argv"
fi

# --- 12 + 13: empty identity env vars do not bypass host.env -------------------
printf 'DEPLOYMENT=claw\n' > "$FIXSCRIPTS/host.env"
run_deploy DEPLOYMENT= || true
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name claw --config"* ]]; then
  ok "12 empty command-line DEPLOYMENT does not bypass host.env"
else
  notok "12 empty DEPLOYMENT must not silently retarget, got: $argv"
fi

printf 'CONFIG=deploy/fasrc-dev/claw.yaml\n' > "$FIXSCRIPTS/host.env"
run_deploy CONFIG= || true
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--config deploy/fasrc-dev/claw.yaml"* ]]; then
  ok "13 empty command-line CONFIG does not bypass host.env"
else
  notok "13 empty CONFIG must not silently retarget, got: $argv"
fi

# --- 14: an empty identity value in host.env aborts ----------------------------
printf 'DEPLOYMENT=\n' > "$FIXSCRIPTS/host.env"
if run_deploy HOST_ENV_SET=1 2>/dev/null; then
  notok "14 DEPLOYMENT= (empty value) in host.env must abort, got: $(cat "$TESTROOT/argv")"
else
  if [ -s "$TESTROOT/argv" ]; then
    notok "14 abort must land before archi is invoked, got: $(cat "$TESTROOT/argv")"
  else
    ok "14 empty identity value in host.env aborts before archi is invoked"
  fi
fi

# --- 15: a duplicate identity key in host.env aborts ---------------------------
printf 'DEPLOYMENT=dev\nDEPLOYMENT=claw\n' > "$FIXSCRIPTS/host.env"
if run_deploy HOST_ENV_SET=1 2>/dev/null; then
  notok "15 duplicate DEPLOYMENT lines must abort, got: $(cat "$TESTROOT/argv")"
else
  if [ -s "$TESTROOT/argv" ]; then
    notok "15 abort must land before archi is invoked, got: $(cat "$TESTROOT/argv")"
  else
    ok "15 duplicate identity key in host.env aborts before archi is invoked"
  fi
fi

# --- 16 + 17: a path-separator DEPLOYMENT aborts, from either source -----------
printf 'DEPLOYMENT=dev/../..\n' > "$FIXSCRIPTS/host.env"
if run_deploy HOST_ENV_SET=1 2>/dev/null; then
  notok "16 DEPLOYMENT=dev/../.. from host.env must abort, got: $(cat "$TESTROOT/argv")"
else
  if [ -s "$TESTROOT/argv" ]; then
    notok "16 abort must land before archi is invoked, got: $(cat "$TESTROOT/argv")"
  else
    ok "16 path-separator DEPLOYMENT from host.env aborts before archi"
  fi
fi

rm -f "$FIXSCRIPTS/host.env"
if run_deploy DEPLOYMENT=../evil 2>/dev/null; then
  notok "17 ambient DEPLOYMENT=../evil must abort, got: $(cat "$TESTROOT/argv")"
else
  if [ -s "$TESTROOT/argv" ]; then
    notok "17 abort must land before archi is invoked, got: $(cat "$TESTROOT/argv")"
  else
    ok "17 path-separator DEPLOYMENT from the environment aborts before archi"
  fi
fi

# --- 18: repo-root depth ------------------------------------------------------
# The scripts live at deploy/scripts/, so lib.sh must climb exactly TWO levels to
# reach the repo root. Nothing else in this suite catches a wrong depth:
# require_files is stubbed out, and CHAT_PORT/LLM_URL both fall back silently
# when "$REPO_ROOT/$CONFIG" does not resolve. So a lib.sh that climbs three
# levels passes every other case here and then fails on a real deploy with
# "config not found". Pin the depth directly.
rm -f "$FIXSCRIPTS/host.env"
expected_root="$(cd "$TESTROOT/repo" && pwd)"
resolved_root="$(env -u DEPLOYMENT -u CONFIG -u GPU_IDS bash -c '
  source "'"$FIXSCRIPTS"'/lib.sh" >/dev/null 2>&1
  printf "%s" "$REPO_ROOT"
')"
if [ "$resolved_root" = "$expected_root" ]; then
  ok "18 REPO_ROOT climbs two levels from deploy/scripts to the repo root"
else
  notok "18 REPO_ROOT should be $expected_root, got: $resolved_root"
fi

# --- 19 + 20: legacy host.env migration guard --------------------------------
# host.env is git-ignored, so `git pull` cannot move it when the scripts relocate:
# it stays at deploy/fasrc-dev/scripts/host.env while the moved lib.sh reads only
# $SCRIPT_DIR/host.env. The identity would then fall through to the reserved `dev`
# WITHOUT a word, because CONFIG also falls back to deploy/fasrc-dev/config.yaml,
# which exists on exactly the hosts that have the stale file. Fail closed.
LEGACY="$TESTROOT/repo/deploy/fasrc-dev/scripts"
mkdir -p "$LEGACY"
rm -f "$FIXSCRIPTS/host.env"
printf 'DEPLOYMENT=claw\n' > "$LEGACY/host.env"
if run_deploy LEGACY_ONLY=1 2>/dev/null; then
  notok "19 a legacy-only host.env must abort, got: $(cat "$TESTROOT/argv")"
else
  if [ -s "$TESTROOT/argv" ]; then
    notok "19 abort must land before archi is invoked, got: $(cat "$TESTROOT/argv")"
  else
    ok "19 a legacy-only host.env aborts before archi, not a silent fallback to dev"
  fi
fi

# Migrated host: the new file governs and the leftover is not fatal, only noisy.
printf 'DEPLOYMENT=claw\n' > "$FIXSCRIPTS/host.env"
printf 'DEPLOYMENT=stale\n' > "$LEGACY/host.env"
if run_deploy MIGRATED=1; then
  argv="$(cat "$TESTROOT/argv")"
  if [[ "$argv" == *"--name claw"* ]]; then
    ok "20 with both files present the new host.env governs"
  else
    notok "20 the new host.env should win, got: $argv"
  fi
else
  notok "20 a leftover legacy file must not abort a migrated host"
fi
rm -rf "$TESTROOT/repo/deploy/fasrc-dev"
rm -f "$FIXSCRIPTS/host.env"

# --- 21: a NON-empty GPU_IDS from host.env reaches --gpu-ids ------------------
# Case 6 pins only the empty disable. docs/docs/ingest_performance_cpu_vs_gpu.md
# now tells operators to enable GPU embedding with GPU_IDS=0 in host.env rather
# than in the shared tracked lib.sh, so the path that instruction depends on has
# to be pinned too.
printf 'DEPLOYMENT=claw\nGPU_IDS=0\n' > "$FIXSCRIPTS/host.env"
run_deploy GPU_ON=1
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--gpu-ids 0"* ]]; then
  ok "21 a non-empty GPU_IDS from host.env reaches --gpu-ids"
else
  notok "21 host.env GPU_IDS=0 should reach --gpu-ids 0, got: $argv"
fi
rm -f "$FIXSCRIPTS/host.env"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
