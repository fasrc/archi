#!/usr/bin/env bash
# Self-test for goldenset_report_cron.sh: exercises the wrapper's contract
# against a fake python — no network, no database, no crontab, nothing written
# outside a sandbox.
#    1. it invokes `goldenset_maintenance.py report` with the configured inputs
#    2. findings (exit 0) stay exit 0 — the cron must not alert on work-to-do
#    3. an operational failure (exit 1) propagates, so cron mails the operator
#    4. output is appended to the log, never truncated
#    5. the log directory is created if absent
#    6. a missing required setting fails before running anything
#    7. no model is passed unless GOLDENSET_MODEL is set
#    8. --min-pages is passed through when set, omitted when not
#    9-10. unattended: silent when healthy, stderr when broken (cron mails on
#          ANY output and ignores the exit status)
#   11-12. configuration comes from an env file, found by convention under HOME
#          (crontab has no line continuation, so the entry must carry no env)
#   13-15. the third state: findings exit ZERO, so notification cannot key on
#          the exit status — a concise digest, not the whole report
#   16-17. the append-only log is rotated once past a size cap
# Run: bash scripts/benchmarking/test_goldenset_report_cron.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON="$SCRIPT_DIR/goldenset_report_cron.sh"

# Hermetic against the developer's own cron config. Every knob below is one the
# wrapper honours, and it gives an ambient export precedence over the fake
# `python` on PATH and over these tests' defaults — so a shell that exported,
# say, GOLDENSET_PYTHON=/bin/false or a real GOLDENSET_ENV_FILE would make the
# gate source a real env file, run the real maintenance command, or just fail,
# none of which is this suite's contract. Clear them once so the sandbox is the
# only source of configuration; the env-file cases set GOLDENSET_ENV_FILE fresh.
unset GOLDENSET_ENV_FILE GOLDENSET_BANK GOLDENSET_PG_DSN GOLDENSET_CORPUS_JSON \
  GOLDENSET_SOURCES GOLDENSET_ALLOWED_HOSTS GOLDENSET_MIN_PAGES GOLDENSET_MAX_PAGES \
  GOLDENSET_LEDGER GOLDENSET_MODEL GOLDENSET_LOG_DIR GOLDENSET_PYTHON \
  GOLDENSET_LOG_MAX_BYTES

PASS=0; FAIL=0
ok()    { printf 'ok - %s\n' "$1"; PASS=$((PASS + 1)); }
notok() { printf 'not ok - %s\n' "$1"; FAIL=$((FAIL + 1)); }

TESTROOT="$(mktemp -d)"
trap 'rm -rf "$TESTROOT"' EXIT

# A fake `python` that records its argv and exits with $STUB_EXIT.
make_stub() { # $1 = sandbox
  local sb="$1"
  mkdir -p "$sb/bin"
  cat > "$sb/bin/python" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$sb/calls"
# Emulate \`report --summary-json\`: write whatever the test asked for.
prev=""
for a in "\$@"; do
  if [ "\$prev" = "--summary-json" ]; then
    printf '%s' "\${STUB_SUMMARY:-{\"gaps\":0,\"orphans\":0,\"drifted\":0,\"unchecked_sources\":0,\"failed_passes\":[],\"notify\":false}}" > "\$a"
  fi
  prev="\$a"
done
echo "fake report output"
if [ -n "\${STUB_BYTES:-}" ]; then head -c "\$STUB_BYTES" /dev/zero | tr '\\0' 'y'; echo; fi
exit \${STUB_EXIT:-0}
EOF
  chmod +x "$sb/bin/python"
}

# Run the wrapper in a sandbox with the fake python ahead of the real one.
run_cron() { # $1 = sandbox; remaining env comes from the caller
  local sb="$1"; shift
  PATH="$sb/bin:$PATH" \
  GOLDENSET_BANK="${GOLDENSET_BANK-$sb/bank.json}" \
  GOLDENSET_SOURCES="${GOLDENSET_SOURCES-$sb/sources.list}" \
  GOLDENSET_ALLOWED_HOSTS="${GOLDENSET_ALLOWED_HOSTS-docs.rc.fas.harvard.edu}" \
  GOLDENSET_LOG_DIR="${GOLDENSET_LOG_DIR-$sb/log}" \
    bash "$CRON" "$@"
}

new_sandbox() {
  local sb; sb="$(mktemp -d "$TESTROOT/sb.XXXXXX")"
  make_stub "$sb"
  : > "$sb/bank.json"
  : > "$sb/sources.list"
  echo "$sb"
}

# 1. the report subcommand is invoked with the configured inputs
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1 || true
calls="$(cat "$sb/calls" 2>/dev/null || true)"
case "$calls" in
  *"report"*) ok "invokes the report subcommand" ;;
  *) notok "invokes the report subcommand (got: $calls)" ;;
esac
case "$calls" in
  *"--pg-dsn postgresql://x"*) ok "passes the configured corpus DSN" ;;
  *) notok "passes the configured corpus DSN (got: $calls)" ;;
esac
case "$calls" in
  *"--allowed-hosts docs.rc.fas.harvard.edu"*) ok "passes the allowlist" ;;
  *) notok "passes the allowlist (got: $calls)" ;;
esac

# 2. findings must not alert: report exits 0, so the wrapper does too
sb="$(new_sandbox)"
if ( STUB_EXIT=0 GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1; then
  ok "a run with findings exits zero"
else
  notok "a run with findings exits zero"
fi

# 3. an operational failure propagates, so cron mails the operator
sb="$(new_sandbox)"
if ( STUB_EXIT=1 GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1; then
  notok "an operational failure exits non-zero"
else
  ok "an operational failure exits non-zero"
fi

# 4. the log is appended to, never truncated
sb="$(new_sandbox)"
mkdir -p "$sb/log"
echo "PREVIOUS RUN" > "$sb/log/goldenset-report.log"
( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1 || true
if grep -q "PREVIOUS RUN" "$sb/log/goldenset-report.log" \
   && grep -q "fake report output" "$sb/log/goldenset-report.log"; then
  ok "appends to the log rather than truncating it"
else
  notok "appends to the log rather than truncating it"
fi

# 5. a missing log directory is created rather than losing the run
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_DIR="$sb/deep/log" run_cron "$sb" ) \
  >/dev/null 2>&1 || true
if [ -f "$sb/deep/log/goldenset-report.log" ]; then
  ok "creates a missing log directory"
else
  notok "creates a missing log directory"
fi

# 6. a missing required setting fails loudly, before invoking anything
sb="$(new_sandbox)"
if ( run_cron "$sb" ) >/dev/null 2>&1; then
  notok "refuses to run without a corpus setting"
else
  if [ -s "$sb/calls" ]; then
    notok "refuses BEFORE invoking the tool"
  else
    ok "refuses to run without a corpus setting, before invoking the tool"
  fi
fi

# 7. no provider call unless a model is explicitly configured
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1 || true
case "$(cat "$sb/calls")" in
  *"--model"*) notok "omits --model by default" ;;
  *) ok "omits --model by default" ;;
esac
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_MODEL="anthropic/x" run_cron "$sb" ) \
  >/dev/null 2>&1 || true
case "$(cat "$sb/calls")" in
  *"--model anthropic/x"*) ok "passes --model when configured" ;;
  *) notok "passes --model when configured" ;;
esac

# 8. the sitemap floor is passed through only when set
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_MIN_PAGES=150 run_cron "$sb" ) \
  >/dev/null 2>&1 || true
case "$(cat "$sb/calls")" in
  *"--min-pages 150"*) ok "passes --min-pages when configured" ;;
  *) notok "passes --min-pages when configured" ;;
esac
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1 || true
case "$(cat "$sb/calls")" in
  *"--min-pages"*) notok "omits --min-pages when unset" ;;
  *) ok "omits --min-pages when unset" ;;
esac

# 9. cron mails on ANY output, not on exit status — so a healthy unattended run
#    has to be completely silent or the nightly job mails its own report every
#    night and the operator learns to filter it.
sb="$(new_sandbox)"
out="$( ( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) 2>"$sb/err" || true )"
if [ -z "$out" ] && [ ! -s "$sb/err" ]; then
  ok "a healthy unattended run is silent"
else
  notok "a healthy unattended run is silent (stdout: $out; stderr: $(cat "$sb/err"))"
fi

# 10. a failure must still reach the operator, which is what cron mail is for
sb="$(new_sandbox)"
( STUB_EXIT=1 GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) \
  >/dev/null 2>"$sb/err" || true
if grep -q "fake report output" "$sb/err"; then
  ok "a failing unattended run reports on stderr"
else
  notok "a failing unattended run reports on stderr (got: $(cat "$sb/err"))"
fi

# 11. an env file supplies the configuration, so the crontab line stays short
#     (crontab has no line continuation — the entry ends at the newline)
sb="$(new_sandbox)"
cat > "$sb/report.env" <<EOF
GOLDENSET_PG_DSN=postgresql://from-file
GOLDENSET_MIN_PAGES=150
EOF
( GOLDENSET_ENV_FILE="$sb/report.env" run_cron "$sb" ) >/dev/null 2>&1 || true
case "$(cat "$sb/calls" 2>/dev/null || true)" in
  *"--pg-dsn postgresql://from-file"*"--min-pages 150"*)
    ok "reads configuration from an env file" ;;
  *) notok "reads configuration from an env file (got: $(cat "$sb/calls" 2>/dev/null))" ;;
esac

# 12. and it is found by convention, so the cron entry needs no environment at all
sb="$(new_sandbox)"
mkdir -p "$sb/home/.ralph"
cat > "$sb/home/.ralph/goldenset-report.env" <<EOF
GOLDENSET_PG_DSN=postgresql://by-convention
EOF
( HOME="$sb/home" GOLDENSET_PG_DSN="" run_cron "$sb" ) >/dev/null 2>&1 || true
case "$(cat "$sb/calls" 2>/dev/null || true)" in
  *"--pg-dsn postgresql://by-convention"*) ok "finds the default env file under HOME" ;;
  *) notok "finds the default env file under HOME (got: $(cat "$sb/calls" 2>/dev/null))" ;;
esac

# 13. a genuinely CLEAN unattended run stays silent
sb="$(new_sandbox)"
out="$( ( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) 2>"$sb/err" || true )"
if [ -z "$out" ] && [ ! -s "$sb/err" ]; then
  ok "a clean unattended run is silent"
else
  notok "a clean unattended run is silent (stdout: $out; stderr: $(cat "$sb/err"))"
fi

# 14. findings exit ZERO by design, so keying notification on the exit status
#     would hide every actionable result. The wrapper reads the summary instead.
sb="$(new_sandbox)"
FINDINGS='{"gaps":2,"orphans":1,"drifted":3,"unchecked_sources":0,"failed_passes":[],"notify":true}'
out="$( ( STUB_SUMMARY="$FINDINGS" GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) \
  2>"$sb/err" || true )"
case "$out" in
  *"2 gap"*"1 orphan"*"3 drifted"*) ok "findings notify even though the run exits zero" ;;
  *) notok "findings notify even though the run exits zero (got: $out)" ;;
esac

# 15. ...but concisely. Mailing the whole report nightly is the alert fatigue
#     that made routine output log-only in the first place.
case "$out" in
  *"fake report output"*) notok "the findings notice is a digest, not the report" ;;
  *) ok "the findings notice is a digest, not the report" ;;
esac
case "$out" in
  *goldenset-report.log*) ok "the findings notice points at the log" ;;
  *) notok "the findings notice points at the log (got: $out)" ;;
esac

# 16. an unbounded nightly append eventually fills the filesystem
sb="$(new_sandbox)"
mkdir -p "$sb/log"
head -c 4096 /dev/zero | tr '\0' 'x' > "$sb/log/goldenset-report.log"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_MAX_BYTES=1024 run_cron "$sb" ) \
  >/dev/null 2>&1 || true
if [ -f "$sb/log/goldenset-report.log.1" ] \
   && [ "$(wc -c < "$sb/log/goldenset-report.log")" -lt 4096 ]; then
  ok "an oversized log is rotated before appending"
else
  notok "an oversized log is rotated before appending"
fi

# 17. and a log under the cap is left alone
sb="$(new_sandbox)"
mkdir -p "$sb/log"
echo "PREVIOUS RUN" > "$sb/log/goldenset-report.log"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_MAX_BYTES=1048576 run_cron "$sb" ) \
  >/dev/null 2>&1 || true
if [ ! -f "$sb/log/goldenset-report.log.1" ] \
   && grep -q "PREVIOUS RUN" "$sb/log/goldenset-report.log"; then
  ok "a log under the cap is not rotated"
else
  notok "a log under the cap is not rotated"
fi

# 18. drift "succeeds" as long as ONE source was readable, so a run that checked
#     1 of 50 pages exits zero with drifted=0. Notification keys on the report's
#     own `notify` flag, not on the drifted count, or that degraded run is
#     indistinguishable from a clean bank.
sb="$(new_sandbox)"
DEGRADED='{"gaps":0,"orphans":0,"drifted":0,"unchecked_sources":49,"failed_passes":[],"notify":true}'
out="$( ( STUB_SUMMARY="$DEGRADED" GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) \
  2>/dev/null || true )"
case "$out" in
  *"49 unchecked"*) ok "a degraded run notifies even with nothing drifted" ;;
  *) notok "a degraded run notifies even with nothing drifted (got: $out)" ;;
esac

# 18b. `notify` can fire on a slug near-miss alone (needs_reconciliation), a
#      bucket the digest first omitted — so the mail paged with an all-zeros
#      line and no named cause, forcing the operator to open the log to learn
#      why it spoke. The digest must name the reconciliation count.
sb="$(new_sandbox)"
RECON='{"gaps":0,"orphans":0,"drifted":0,"unchecked_sources":0,"needs_reconciliation":2,"orphans_needs_reconciliation":1,"refused_sources":0,"failed_passes":[],"notify":true}'
out="$( ( STUB_SUMMARY="$RECON" GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) \
  2>/dev/null || true )"
case "$out" in
  *"3 reconcile"*) ok "a reconciliation-only notify names the reconcile bucket" ;;
  *) notok "a reconciliation-only notify names the reconcile bucket (got: $out)" ;;
esac

# 19. the flag is authoritative — the wrapper does not re-derive the policy from
#     the counters. A deliberately contradictory summary (counts set, notify
#     false) pins that: which buckets deserve to wake someone is decided in
#     `report`, where it has tests, not in shell string-matching.
sb="$(new_sandbox)"
QUIET='{"gaps":0,"orphans":0,"drifted":0,"unchecked_sources":0,"refused_sources":7,"failed_passes":[],"notify":false}'
out="$( ( STUB_SUMMARY="$QUIET" GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) \
  2>/dev/null || true )"
if [ -z "$out" ]; then
  ok "notify=false stays silent even with non-zero counters"
else
  notok "notify=false stays silent even with non-zero counters (got: $out)"
fi

# 20. the env file is SOURCED, so a multi-value setting must be quoted in it.
#     Unquoted, bash reads `VAR=a b` as "run command b with VAR=a" — which is
#     how the allowlist, the one setting that is normally a list, breaks.
sb="$(new_sandbox)"
cat > "$sb/report.env" <<'EOF'
GOLDENSET_PG_DSN=postgresql://x
GOLDENSET_ALLOWED_HOSTS="docs.rc.fas.harvard.edu slurm.schedmd.com"
EOF
( GOLDENSET_ENV_FILE="$sb/report.env" run_cron "$sb" ) >/dev/null 2>&1 || true
case "$(cat "$sb/calls" 2>/dev/null || true)" in
  *"--allowed-hosts docs.rc.fas.harvard.edu slurm.schedmd.com"*)
    ok "a quoted multi-host allowlist survives the env file" ;;
  *) notok "a quoted multi-host allowlist survives the env file (got: $(cat "$sb/calls" 2>/dev/null))" ;;
esac

# 21. rotation before the run does not bound anything on its own: coverage
#     prints every gap and drift can span the whole bank, so ONE run can append
#     far more than the cap and fill the disk before the next rotation.
sb="$(new_sandbox)"
( STUB_BYTES=20000 GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_MAX_BYTES=2048 \
  run_cron "$sb" ) >/dev/null 2>&1 || true
size="$(wc -c < "$sb/log/goldenset-report.log")"
if [ "$size" -le 4096 ]; then
  ok "a single oversized run cannot blow past the cap (log is $size bytes)"
else
  notok "a single oversized run cannot blow past the cap (log is $size bytes)"
fi

# 22. and the truncation is announced, so nobody reads a cut log as a whole one
sb="$(new_sandbox)"
( STUB_BYTES=20000 GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_MAX_BYTES=2048 \
  run_cron "$sb" ) >/dev/null 2>&1 || true
if grep -q "truncated" "$sb/log/goldenset-report.log"; then
  ok "a truncated log says so"
else
  notok "a truncated log says so"
fi

# 23. a normal-sized run is not truncated
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_MAX_BYTES=1048576 run_cron "$sb" ) \
  >/dev/null 2>&1 || true
if grep -q "fake report output" "$sb/log/goldenset-report.log" \
   && ! grep -q "truncated" "$sb/log/goldenset-report.log"; then
  ok "a normal run is written whole"
else
  notok "a normal run is written whole"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
