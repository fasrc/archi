#!/usr/bin/env bash
# Create / bring up the archi 'dev' deployment.
# Safe to re-run: --force rebuilds and restarts without wiping data volumes.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

archi_deploy
