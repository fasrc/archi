#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${ARCHI_TOKEN:-}" ]; then
    echo "No ARCHI_TOKEN set — generating one..."
    ARCHI_TOKEN=$(curl -s -X POST -H 'X-Client-ID: austin' http://localhost:7861/api/users/me/api-token | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
    echo "Generated token: $ARCHI_TOKEN"
    echo "Save this token — it will not be shown again."
fi

export ARCHI_TOKEN
docker compose -f "$SCRIPT_DIR/docker-compose.librechat.yaml" up -d
echo "LibreChat is running at http://localhost:3080"
