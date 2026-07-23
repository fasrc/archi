#!/usr/bin/env bash
# Nightly read-only maintenance report over the RAGAS golden-set bank.
#
# Runs `goldenset_maintenance.py report` — coverage + orphans + drift — and
# appends the output to a log. Writes nothing else: not the bank, not the
# corpus, not the decision ledger.
#
# Exit status follows the cron contract, so what lands in the operator's mail is
# only ever a broken run:
#   0  the report ran, whatever it found (gaps and drift are work, not failure)
#   1  a pass could not run — unreadable corpus/bank/source list, or a live
#      inventory too incomplete to judge orphans against
#   2  this wrapper is misconfigured (checked before anything is invoked)
#
# Configure with an environment file, not flags, because crontab has no line
# continuation — an entry ends at the newline, and a trailing backslash does not
# join the next line. So the settings live in a file a human can read and the
# crontab entry stays a single short line with no environment on it at all:
#
#   GOLDENSET_ENV_FILE      env file to source (default: ~/.ralph/goldenset-report.env)
#
# Values in that file win over anything already in the environment, the way
# systemd's EnvironmentFile= behaves. Recognized settings:
#
#   GOLDENSET_BANK          bank JSON       (default: examples/benchmarking/…)
#   GOLDENSET_PG_DSN        live catalog DSN     ) exactly one
#   GOLDENSET_CORPUS_JSON   or a corpus dump     ) of these two
#   GOLDENSET_SOURCES       source list the KB ingests from   (required)
#   GOLDENSET_ALLOWED_HOSTS space-separated hosts to contact  (required)
#   GOLDENSET_MIN_PAGES     sitemap floor — match the deployment (FASRC: 150)
#   GOLDENSET_MAX_PAGES     sitemap cap  — match the deployment
#   GOLDENSET_LEDGER        decision ledger, so declined pages stay suppressed
#   GOLDENSET_MODEL         OPTIONAL provider/model for the advisory drift diff.
#                           Unset by default: an unattended job should not spend
#                           a provider call per drifted row without being asked.
#   GOLDENSET_LOG_DIR       where to append   (default: ~/.ralph/log)
#   GOLDENSET_PYTHON        interpreter       (default: python)
#
# Install and rollback are documented in docs/docs/benchmarking.md.
# Self-test: bash scripts/benchmarking/test_goldenset_report_cron.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Sourced before anything is read, so the crontab entry can carry no environment.
ENV_FILE="${GOLDENSET_ENV_FILE:-$HOME/.ralph/goldenset-report.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

BANK="${GOLDENSET_BANK:-$REPO_ROOT/examples/benchmarking/fasrc_ragas_queries.json}"
LOG_DIR="${GOLDENSET_LOG_DIR:-$HOME/.ralph/log}"
LOG="$LOG_DIR/goldenset-report.log"
PYTHON="${GOLDENSET_PYTHON:-python}"

die() { printf 'goldenset-report: %s\n' "$1" >&2; exit 2; }

# Validate BEFORE invoking anything. A misconfigured cron that half-runs is
# worse than one that refuses: the report would be silently partial, and a
# partial report reads exactly like a clean one.
[ -n "${GOLDENSET_SOURCES:-}" ] || die "GOLDENSET_SOURCES is required"
[ -n "${GOLDENSET_ALLOWED_HOSTS:-}" ] || die "GOLDENSET_ALLOWED_HOSTS is required"
if [ -n "${GOLDENSET_PG_DSN:-}" ] && [ -n "${GOLDENSET_CORPUS_JSON:-}" ]; then
  die "set GOLDENSET_PG_DSN or GOLDENSET_CORPUS_JSON, not both"
fi

args=("$REPO_ROOT/scripts/benchmarking/goldenset_maintenance.py" report
      --bank "$BANK" --sources "$GOLDENSET_SOURCES")

if [ -n "${GOLDENSET_PG_DSN:-}" ]; then
  args+=(--pg-dsn "$GOLDENSET_PG_DSN")
elif [ -n "${GOLDENSET_CORPUS_JSON:-}" ]; then
  args+=(--corpus-json "$GOLDENSET_CORPUS_JSON")
else
  die "GOLDENSET_PG_DSN or GOLDENSET_CORPUS_JSON is required"
fi

# Deliberately unquoted: the allowlist is a space-separated list and argparse
# takes it as nargs="+".
# shellcheck disable=SC2206
args+=(--allowed-hosts ${GOLDENSET_ALLOWED_HOSTS})

[ -n "${GOLDENSET_MIN_PAGES:-}" ] && args+=(--min-pages "$GOLDENSET_MIN_PAGES")
[ -n "${GOLDENSET_MAX_PAGES:-}" ] && args+=(--max-pages "$GOLDENSET_MAX_PAGES")
[ -n "${GOLDENSET_LEDGER:-}" ] && args+=(--ledger "$GOLDENSET_LEDGER")
[ -n "${GOLDENSET_MODEL:-}" ] && args+=(--model "$GOLDENSET_MODEL")

mkdir -p "$LOG_DIR"

{
  printf '\n===== goldenset report %s =====\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} >> "$LOG"

# Appended, never truncated: the value of this log is the history, which is how
# a slow drift (a page edited a little each month) becomes visible at all.
#
# What reaches stdout depends on whether a human is watching, because cron mails
# on ANY output and pays no attention to the exit status. A nightly job that
# prints its report every night mails its report every night, and an operator
# who gets mail on every healthy run stops reading the mail — which costs
# exactly the failure the job exists to surface.
set +e
if [ -t 1 ]; then
  # Interactive: stream, so a slow drift pass is visible while it runs.
  "$PYTHON" "${args[@]}" 2>&1 | tee -a "$LOG"
  status=${PIPESTATUS[0]}
else
  # Unattended: silent when healthy, loud when not.
  run_out="$(mktemp)"
  "$PYTHON" "${args[@]}" > "$run_out" 2>&1
  status=$?
  cat "$run_out" >> "$LOG"
  [ "$status" -ne 0 ] && cat "$run_out" >&2
  rm -f "$run_out"
fi
set -e

printf '===== exit %d =====\n' "$status" >> "$LOG"
exit "$status"
