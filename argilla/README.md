# Argilla deployment for archi benchmark human-grading

Self-hosted [Argilla](https://argilla.io/) 2.x stack used to collect human
grades on archi benchmark outputs. Runs alongside (not inside) the archi
`docker-compose` deployment.

## One-time setup

```bash
# 1. Generate Argilla secrets (32-byte hex each).
python scripts/bootstrap_argilla.py --generate-secrets

# 2. Ensure the ES data dir exists and is owned by the elasticsearch UID (1000).
sudo mkdir -p /scratch/docker/volumes/argilla-es
sudo chown -R 1000:1000 /scratch/docker/volumes/argilla-es

# 3. Bring the stack up.
docker compose -f argilla/docker-compose.yaml up -d

# 4. Wait for both containers to report "healthy" (~60s on first start).
docker compose -f argilla/docker-compose.yaml ps

# 5. Bootstrap the "archi" workspace via SDK.
python scripts/bootstrap_argilla.py --create-workspace

# 6. Open tcp/6900 from the staff source range (mirrors port 7861's rule).
sudo iptables -I INPUT 12 -p tcp --dport 6900 -s <STAFF_RANGE> -j ACCEPT
sudo /sbin/service iptables save   # adjust to host's persistence mechanism
```

## Network access

- Internal (on this host): `http://localhost:6900/`
- Staff (after iptables): `http://archi.rc.fas.harvard.edu:6900/`

Argilla does not terminate TLS. If TLS is required, front it with the
existing reverse-proxy (nginx / Caddy) on the host and forward to
`localhost:6900`.

## Operator credentials

- Username: `owner`
- Password: `~/.archi/secrets/argilla_owner_password.txt`
- API key: `~/.archi/secrets/argilla_api_key.txt`

Distribute individual evaluator accounts via the bootstrap script
(`python scripts/bootstrap_argilla.py --create-users names.txt`) or the
Argilla UI's user-management page.

## Data persistence

| Component | Location | Notes |
|---|---|---|
| Argilla DB (users, workspaces, settings) | named volume `argilla-data` → `/var/lib/argilla` inside the container | SQLite; survives `down`; lost on `docker volume rm argilla-data` |
| ES indices (records, responses) | host bind `/scratch/docker/volumes/argilla-es/` | Survives `down`; lost on manual `rm -rf` |
| Redis (task queue) | in-memory only | Pure broker — no persistence; safe to restart |

## Reset

To wipe everything and start over:

```bash
docker compose -f argilla/docker-compose.yaml down -v
sudo rm -rf /scratch/docker/volumes/argilla-es
```
