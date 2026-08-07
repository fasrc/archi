## 1. Directory & Compose

- [x] 1.1 Confirm `config/homer/` exists (it does, empty); create `config/homer/assets/`
- [x] 1.2 Write `config/homer/compose.yaml`: service `homer`, image `b4bz/homer:latest`, `container_name: homer`, `network_mode: host`, `volumes: ./assets:/www/assets:ro`, `restart: unless-stopped`, and an `environment: PORT=8082` (Homer reads `PORT` for its nginx listener)
- [x] 1.3 `docker compose -f config/homer/compose.yaml config` to syntax-check the file

## 2. Dashboard Config

- [x] 2.1 Write `config/homer/assets/config.yml` top matter: `title`, `subtitle` ("archi deployment"), `header: true`, a `columns: "auto"` layout, and a leading comment block explaining the localhost→FQDN swap
- [x] 2.2 Add the "Chat & Inference" section with tiles: archi chatbot (`http://localhost:7861`, "Primary archi RAG chatbot"), Open WebUI (`http://localhost:8081`, "Open WebUI chat front-end"), LibreChat (`http://localhost:3080`, "LibreChat multi-model chat UI"), vLLM `/v1` (`http://localhost:8000/v1/models`, "OpenAI-compatible inference endpoint") — each with a port comment
- [x] 2.3 Add the "Observability" section with the Grafana tile (`http://localhost:3000`, "Metrics & dashboards") + port comment
- [x] 2.4 Pick reasonable Homer icons (`fas fa-*` / built-in logos) and `tag`/`subtitle` fields per tile so the dashboard is readable

## 3. Documentation

- [x] 3.1 Write `config/homer/README.md`: what Homer is (link to https://github.com/bastienwirtz/homer), `up -d` / `down` commands, port 8082, how to edit `assets/config.yml` (live reload on browser refresh), how to swap `localhost` for an FQDN, and the `docker tag` rollback note for the `latest` image

## 4. Verify

- [x] 4.1 `docker compose -f config/homer/compose.yaml up -d` and confirm the container is `Up`
- [x] 4.2 `curl -s http://localhost:8082/ | grep -i homer` returns matches; spot-check the rendered page lists all five tiles in two sections
- [x] 4.3 Edit `assets/config.yml` (e.g., tweak a description), reload the browser, confirm the change shows without a container restart; revert the test edit
- [x] 4.4 `docker compose -f config/homer/compose.yaml down` leaves no dangling container/volume
