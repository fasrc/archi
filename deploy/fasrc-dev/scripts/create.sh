#!/usr/bin/env bash
# Create / bring up this host's archi deployment ($DEPLOYMENT from lib.sh).
# Safe to re-run: --force rebuilds and restarts without wiping data volumes.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

archi_deploy
