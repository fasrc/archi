# Argilla deployment for archi benchmark human-grading

Self-hosted [Argilla](https://argilla.io/) 2.x stack used to collect human
grades on archi benchmark outputs. Runs alongside (not inside) the archi
`docker-compose` deployment.

## One-time setup

```bash
# 1. Generate Argilla secrets (32-byte hex each).
python scripts/bootstrap_argilla.py --generate-secrets

# 2. Materialize the env file docker-compose sources. Argilla's entrypoint
#    reads raw USERNAME/PASSWORD/API_KEY env vars; Docker's `_FILE` suffix
#    pattern is NOT supported by their bootstrap script, so we inline.
python scripts/bootstrap_argilla.py --export-env

# 3. Ensure the ES data dir exists and is owned by the elasticsearch UID (1000).
sudo mkdir -p /scratch/docker/volumes/argilla-es
sudo chown -R 1000:1000 /scratch/docker/volumes/argilla-es

# 4. Bring the stack up.
docker compose -f argilla/docker-compose.yaml up -d

# 5. Wait for all three containers (argilla-server, elasticsearch, redis) to
#    report "healthy". First start takes ~60s for argilla-server.
docker compose -f argilla/docker-compose.yaml ps

# 6. Bootstrap the "archi" workspace via SDK.
python scripts/bootstrap_argilla.py --create-workspace

# 7. Confirm tcp/3080 is in the host's INPUT chain for the admin VPN
#    source range (it usually already is — check first):
sudo iptables -L INPUT -n --line-numbers | grep -E "3080|multiport"
# If 3080 isn't allowed yet, mirror the existing 7861 rule:
sudo iptables -I INPUT 12 -p tcp --dport 3080 -s <STAFF_RANGE> -j ACCEPT
sudo /sbin/service iptables save   # adjust to host's persistence mechanism
```

## Network access

- Internal (on this host): `http://localhost:3080/`
- Staff (admin VPN, perimeter-allowed): `http://archi.rc.fas.harvard.edu:3080/`

**Port 3080 is a TEMPORARY stopgap.** The FASRC perimeter firewall allows
tcp/3080 inbound from the admin VPN (`10.255.13.96/27`) but not tcp/6900.
A network-change ticket is pending with FASRC to open tcp/6900 inbound
to this host from the same source range, mirroring the existing 7861
(archi-chatbot) allowance. Once that ticket lands:

1. Edit `argilla/docker-compose.yaml`: change `"3080:6900"` back to `"6900:6900"`
2. Edit `scripts/bootstrap_argilla.py` default `ARGILLA_API_URL` to `http://localhost:6900`
3. `docker compose -f argilla/docker-compose.yaml up -d --force-recreate argilla`
4. Update any external bookmarks / docs that referenced `:3080`

Argilla does not terminate TLS. If TLS is required, front it with the
existing reverse-proxy (nginx / Caddy) on the host and forward to
`localhost:3080`.

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
