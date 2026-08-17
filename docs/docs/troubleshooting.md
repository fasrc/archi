# Troubleshooting

Common issues and their resolutions when running Archi.

---

## Port Conflicts

**Symptom**: Container fails to start with "port already in use" or "address already allocated."

**Fix**: Change the conflicting port in your `config.yaml`:

```yaml
services:
  chat_app:
    port: 7862        # default: 7861
  postgres:
    port: 5433         # default: 5432
  data_manager:
    port: 7872         # default: 7871
```

Check what's using a port:
```bash
lsof -i :7861
# or
ss -tlnp | grep 7861
```

---

## GPU / CUDA Errors

**Symptom**: `RuntimeError: CUDA out of memory` or model fails to load on GPU.

**Fixes**:

1. Use `--gpu-ids` to restrict which GPUs are used:
   ```bash
   archi create -n myapp -c config.yaml --gpu-ids 0
   ```

2. Switch to a smaller model or use CPU-only mode (omit `--gpu-ids`).

3. Check GPU memory:
   ```bash
   nvidia-smi
   ```

---

## Container Debugging

View logs for a specific service:

```bash
docker logs archi-<deployment>-<service>
# Example:
docker logs archi-myapp-chat
docker logs archi-myapp-data-manager
```

Enter a running container:

```bash
docker exec -it archi-myapp-chat /bin/bash
```

Check all containers for a deployment:

```bash
docker ps --filter "name=archi-myapp"
```

---

## Data Manager Not Ingesting

**Symptom**: Data sources aren't being indexed or the data viewer shows no documents.

**Checks**:

1. Verify the data manager container is running:
   ```bash
   docker ps --filter "name=data-manager"
   ```

2. Check data manager logs:
   ```bash
   docker logs archi-myapp-data-manager
   ```

3. Verify your data source configuration is correct in `config.yaml` under `data_manager.sources`.

4. Check the ingestion status endpoint:
   ```bash
   curl http://localhost:7871/api/ingestion/status
   ```

---

## Chat Returns Empty or Generic Responses

**Possible causes**:

1. **No data ingested**: Check the data viewer to verify documents exist.
2. **Wrong provider config**: Verify your API key is set and the provider/model names are correct.
3. **Retrieval issues**: Check `data_manager.retrievers.hybrid_retriever` settings — `num_documents_to_retrieve` may be too low, or `bm25_weight`/`semantic_weight` may need tuning.

Enable verbose logging by checking container logs:
```bash
docker logs -f archi-myapp-chat
```

---

## Authentication Issues

**Symptom**: Login fails or "unauthorized" errors.

**Checks**:

1. Ensure `FLASK_UPLOADER_APP_SECRET_KEY` is set in your `.secrets.env` for stable session behavior.
2. Verify postgres is running and accessible.
3. Check that auth tables were initialized — the chat service creates them on first startup.

---

## Docker Build Failures

**Symptom**: `archi create` fails during image build.

**Fixes**:

1. Ensure Docker is running and accessible:
   ```bash
   docker info
   ```

2. Check disk space — Docker builds can require significant space:
   ```bash
   docker system df
   docker system prune  # clean up unused images/containers
   ```

3. For network issues during build, check your Docker daemon's DNS and proxy settings.

---

## Multiple Deployments

You can run multiple Archi deployments simultaneously as long as ports don't conflict. Each deployment is isolated under `~/.archi/archi-<name>/`.

List all deployments:
```bash
archi list-deployments
```

Check services for a specific deployment:
```bash
archi list-services
```

---

## Stack will not start: `db-migrate` failed

When Postgres is enabled, a one-shot `db-migrate` container applies pending schema
migrations before anything else starts. `config-seed` and the data manager wait for it to
complete successfully, so a failure here keeps the whole stack down by design — see
[Schema migrations run automatically at startup](advanced_setup_deploy.md#schema-migrations-run-automatically-at-startup).

Symptom: services sit in `Created` or `waiting` and never come up.

Check the container's logs first — it names the file it was applying when it stopped:

```bash
docker logs <deployment-name>-db-migrate
```

Common causes:

| Log line | Cause | Fix |
|---|---|---|
| `fe_sendauth: no password supplied` or `password authentication failed` | `PG_PASSWORD` is missing or empty in the deployment's `.env` | Set `PG_PASSWORD` in `.env` and redeploy. The container passes it to psql as `PGPASSWORD`. |
| `could not connect to server` | Postgres was not healthy in time | Check the `postgres` container's logs and healthcheck. |
| A psql `ERROR:` naming a table or column | A genuine problem in a migration file, applied against your schema | Read the named `.sql` file under your deployment's `migrations/` directory to see what ran. Fix it in `src/cli/templates/migrations/` — **not** in the deployment directory, which is rendered output: each redeploy overwrites the packaged files there and removes any `.sql` no longer packaged. |

The container does not restart on failure (`restart: "no"`), so its logs stay available
until the next deployment.

---

## Getting Help

- **GitHub Issues**: [archi-physics/archi](https://github.com/archi-physics/archi/issues)
- **Verbose Logging**: Check container logs with `docker logs -f <container-name>`
- **Configuration Reference**: See the [Configuration](configuration.md) page for all available settings
