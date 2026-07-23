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
# Run: bash scripts/benchmarking/test_goldenset_report_cron.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON="$SCRIPT_DIR/goldenset_report_cron.sh"
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
echo "fake report output"
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

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
