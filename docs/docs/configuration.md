# Configuration Reference

Archi deployments are configured via YAML files passed to the CLI with `--config`. Any fields not specified are populated from the base template at `src/cli/templates/base-config.yaml`.

> **Tip:** Start from one of the example configs in `examples/deployments/` and customize from there.

---

## Top-Level Fields

### `name`

**Type:** string (required)

Name of your deployment. Used for container naming and directory structure.

```yaml
name: my_deployment
```

---

## `global`

Global settings shared across all services.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `DATA_PATH` | string | `/root/data/` | Path for persisted data inside containers |
| `ACCOUNTS_PATH` | string | `/root/.accounts/` | Path for uploader/grader account data |
| `ACCEPTED_FILES` | list | See below | File extensions allowed for manual uploads |
| `LOGGING.input_output_filename` | string | `chain_input_output.log` | Pipeline I/O log filename |
| `verbosity` | int | `3` | Default logging level for services (0-4) |

Default accepted files: `.pdf`, `.md`, `.txt`, `.docx`, `.html`, `.htm`, `.json`, `.yaml`, `.yml`, `.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.java`, `.go`, `.rs`, `.c`, `.cpp`, `.h`, `.sh`

---

## `services`

Configuration for containerized services. Each service has its own subsection.

### Service ports

Each service that is reachable over the network takes two port keys, and which one supplies
the host-side mapping depends on the deployment mode.

| Mode | Host-side port | Container-side port |
|------|----------------|---------------------|
| Container mode (default) | `external_port` | `port` |
| Host mode (`--hostmode`) | `external_port` when it is set, otherwise `port` | same value as the host-side port |

In host mode the two sides are always the same number, because the process binds the port
directly on the host instead of being mapped into a container. Setting `external_port` in host
mode therefore moves *both* sides; the per-service tables below describe `external_port` as the
host-mapped port, which is its container-mode role.

`archi create` validates the effective host-side port of every enabled service during
preflight, before it tears down an existing deployment. The run is refused when a port:

- is not a whole number between 1 and 65535, or
- is assigned to two enabled services at once.

The diagnostic names the exact key it validated — `services.<service>.external_port` in host
mode when that key is set, and `services.<service>.port` otherwise — so the key the message
names is always the key to edit.


### `services.chat_app`

The main chat interface.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `agent_class` | string | `CMSCompOpsAgent` | Pipeline class to run |
| `agents_dir` | string | — | Path to agent markdown files |
| `default_provider` | string | `local` | Default LLM provider |
| `default_model` | string | `llama3.2` | Default model |
| `client_timeout_seconds` | number | `600` | Chat request/stream timeout in seconds (sent to frontend as ms) |
| `tools` | dict | `{}` | Agent-class-specific tool settings (for example `tools.monit.url`) |
| `trained_on` | string | — | Description shown in the chat UI |
| `hostname` | string | `localhost` | Public hostname for the chat interface |
| `port` | int | `7861` | Internal container port |
| `external_port` | int | `7861` | Host-mapped port |
| `host` | string | `0.0.0.0` | Network binding |
| `num_responses_until_feedback` | int | `3` | Responses before prompting for feedback |
| `auth.enabled` | bool | `false` | Enable authentication |
| `alerts.managers` | list | `[]` | Usernames allowed to create and delete alerts |

#### `services.chat_app.auth`

With `auth.enabled: false` (the default) every route runs for everyone and none of the rules
below apply. With it `true`, each guarded route rejects a caller who is not logged in — and
*how* it rejects depends on what the caller can act on. A browser gets a page it can render;
a program gets a status code and a JSON body it can parse.

Rules, evaluated in order:

| Condition | Response |
|-----------|----------|
| SSO is on and `allow_anonymous` is false | **302** redirect to `/login` (an `anonymous_redirect` audit event is recorded) |
| The request path starts with `/api/`, **or** the request carries an `application/json` content type | **401** `{"error": "Unauthorized", "message": "Authentication required"}` |
| Anything else — a browser opening `/chat`, `/terms`, `/data`, `/upload` or `/admin/database` | **302** redirect to `/login` |

Because the first rule is checked first, an enforced-SSO deployment redirects `/api/` callers
too, rather than answering them with a `401`.

A JSON content type counts as a programmatic caller even on a page route, so a script posting
JSON to `/chat` gets the `401` rather than HTML it cannot use. The `Accept` request header is
**not** consulted: a caller that only sends `Accept: application/json` is treated as a browser
and redirected. Every JSON surface lives under `/api/`, which is already covered — see
[issue #189](https://github.com/fasrc/archi/issues/189) if you need negotiation on a page
route.

Logging in is only half the check on the permission-guarded routes (`/data`, `/upload`,
`/admin/database` and their `/api/` equivalents). A logged-in user whose roles lack the
required permission gets **403** `{"error": "Forbidden", ...}` naming the missing permission —
for every caller, browser included. Authorization failures are not redirected; only
authentication failures are.

#### `services.chat_app.alerts`

Controls access to the [Service Status Board & Alert Banners](services.md#service-status-board--alert-banners).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `alerts.managers` | list of strings | `[]` | Usernames granted alert manager access |

Access rules (evaluated in order):

1. **Auth disabled** → all users may manage alerts.
2. **Auth enabled** → a user is an alert manager if **either**:
    - their username is in the `alerts.managers` list, **or**
    - their session roles grant the `alerts:manage` permission.
3. **Auth enabled, no username match, no `alerts:manage` permission** → nobody may manage (safe default).

```yaml
# Option 1: explicit username list
services:
  chat_app:
    alerts:
      managers:
        - alice
        - bob

# Option 2: role-based via RBAC (can be combined with Option 1)
services:
  chat_app:
    auth:
      auth_roles:
        roles:
          ops-team:
            permissions:
              - alerts:manage
```

#### Chat-app evaluation configuration

The evaluation console is opt-in. Both `archi create` and the chat runtime
disable it when `enabled` is omitted or `false`. Set `enabled: true` explicitly
to expose the console and its APIs. When enabled, it persists catalogs,
atom-review drafts, jobs, and run artifacts.

```yaml
services:
  chat_app:
    agents_dir: ../configs/agents
    evaluations:
      enabled: true
      root: /root/archi/evaluations
      agent_config_path: /root/archi/configs/config.eval.yaml
      mcp_config_path: ../configs/qa_evaluation_mcp.yaml
```

- `agents_dir` points to the host directory of Markdown agent specs that the
  Console offers for selection.
- `evaluations.enabled` must be exactly `true` to expose `/evaluations` and its
  APIs; an omitted block, an omitted `enabled` field, or `enabled: false`
  leaves the console disabled.
- `evaluations.root` is the in-container catalog root for datasets, profiles,
  atom-review drafts, jobs, and run artifacts. Defaults to
  `/root/archi/evaluations`. The chat app creates this tree at start-up,
  parent directories included.

  **Constraint:** `root` must be `/root/archi/evaluations` or a path beneath
  it (for example `/root/archi/evaluations/trial-a`). The compose bind mount is
  fixed at `/root/archi/evaluations` — the host volume is attached there and
  nowhere else. A root set outside that subtree is technically valid YAML, but
  the path falls in ephemeral container storage: artifacts accumulate during the
  session and are silently discarded on the next `archi create --force`.
  `archi create` refuses configs that set `root` outside the mount, so a
  mistyped root is reported at deploy time instead of quietly writing artifacts
  to storage that the next redeploy drops.

  !!! warning "The mount is not a backup"

      Keeping `root` inside the mount protects the catalog when a container is
      recreated or restarted. It does **not** protect it from
      `archi create --force`: that path calls `remove_existing_deployment()`,
      which deletes the whole deployment directory — and the host side of this
      mount, `data/evaluations`, sits inside it. Copy the catalog out of
      `${ARCHI_DIR:-$HOME/.archi}/archi-<name>/data/evaluations` before a force
      redeploy if you need to keep it. `ARCHI_DIR` is usually unset, and the CLI
      then defaults it to `~/.archi`.

  If the root cannot be used at runtime — a read-only mount, or a permission
  mismatch on the host directory — the console disables itself and chat keeps
  serving. Look for the start-up error line naming the root, then correct this
  setting and redeploy to re-enable the console.
- `evaluations.agent_config_path` is the in-container path to the Archi
  deployment YAML that defines the agent under test. This key is **required**
  when `enabled` is `true`; it has **no default**. `archi create` refuses a
  config that omits it or that names the live deployment config
  (`/root/archi/configs/config.yaml`), because every evaluation run copies the
  named file into the host-mounted run workspace the console serves — credential
  values included. Use a redacted copy such as
  `/root/archi/configs/config.eval.yaml` instead. Archi does not generate that
  copy: place the redacted file in the deployment's own `configs/` directory on
  the host (`~/.archi/archi-<name>/configs/`, or `$ARCHI_DIR/archi-<name>/configs/`),
  which Compose mounts at `/root/archi/configs`. A run whose `agent_config_path`
  names a file that is absent from the container starts and then fails the file
  check, so confirm the file is in place before the first run. A relative value
  is read inside the container and so resolves against `/root/archi`, not against
  the directory `archi create` ran in.
- `evaluations.mcp_config_path` is needed only for Dataset V2 live oracle
  items. It is an absolute host path or a path relative to this deployment YAML.
  Archi validates and stages the referenced evaluator MCP registry.

Top-level `mcp_servers` configures tools available to the tested agent; it is
not reused as the evaluator registry. See
[Chat-app evaluation configuration](evaluation.md#chat-app-evaluation-configuration)
and [Evaluator MCP registry](evaluation.md#evaluator-mcp-registry) for path
resolution, runtime reachability, authentication, and schema rules.

#### Provider Configuration

```yaml
services:
  chat_app:
    providers:
      local:
        enabled: true
        base_url: http://localhost:11434
        mode: ollama              # or openai_compat
        default_model: llama3.2
        models:
          - llama3.2
      gemini:
        enabled: true
```

### `services.postgres`

PostgreSQL database settings.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `host` | string | `postgres` | Database hostname |
| `port` | int | `5432` | Database port |
| `user` | string | `archi` | Database user |
| `database` | string | `archi-db` | Database name |

### `services.vectorstore`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `backend` | string | `postgres` | Vector store backend (only `postgres` supported) |

### `services.data_manager`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `port` | int | `7871` | Internal port |
| `external_port` | int | `7871` | Host-mapped port |
| `host` | string | `0.0.0.0` | Network binding |
| `enabled` | bool | `true` | Enable data manager service |

### `services.grafana`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `port` | int | `3000` | Grafana port |
| `external_port` | int | `3000` | Host-mapped port |

### `services.grader_app`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `port` | int | `7861` | Internal port |
| `external_port` | int | `7862` | Host-mapped port |
| `provider` | string | — | Provider for grading pipelines |
| `model` | string | — | Model for grading pipelines |
| `num_problems` | int | — | Number of problems (must match rubric files) |
| `local_rubric_dir` | string | — | Path to rubric files |
| `local_users_csv_dir` | string | — | Path to users CSV |

### Other Services

- **`services.piazza`**: Requires `network_id`, `agent_class`, `provider`, `model`
- **`services.mattermost`**: Requires `update_time`
- **`services.redmine_mailbox`**: Requires `url`, `project`, `redmine_update_time`, `mailbox_update_time`
- **`services.benchmarking`**: See [Benchmarking](benchmarking.md)

---

## `data_manager`

Controls data ingestion, vectorstore behaviour, and retrieval settings.

### Core Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `collection_name` | string | `default_collection` | Vector store collection name |
| `embedding_name` | string | `OpenAIEmbeddings` | Embedding backend |
| `chunk_size` | int | `1000` | Max characters per text chunk |
| `chunk_overlap` | int | `0` | Overlapping characters between chunks |
| `parallel_workers` | int | `32` | Parallel **embedding**-phase ingestion workers |
| `scrape_workers` | int | `8` | Parallel **scrape**-phase workers: how many seed URLs are crawled concurrently |
| `scrape_per_host_workers` | int | `4` | Cap on concurrent in-flight requests to any single host |
| `reset_collection` | bool | `true` | Wipe collection on startup |
| `distance_metric` | string | `cosine` | Similarity metric: `cosine`, `l2`, `ip` |

> **Note:** `scrape_workers` and `scrape_per_host_workers` control the **scrape
> phase** only and are independent of `parallel_workers`, which governs the
> **embedding phase**. Both scrape knobs coerce to `int`, fall back to their
> defaults on invalid values (including YAML's non-finite `.inf` / `.nan`), and
> clamp to a minimum of `1` — so `scrape_workers: 0` is accepted and means `1`, not
> the default of `8`. Setting `scrape_workers: 1` reproduces the sequential scrape
> path exactly, in input order.
>
> **What "per host" means.** `scrape_per_host_workers` is a budget owed to a
> *server*, so it is enforced across the whole data-manager process, not per
> collection run: a scheduled link ingest and a `/document_index/upload_url`
> request that overlap contend for the same slots rather than each spending the
> full budget. Host slots also follow redirects — a seed on `example.org` that
> redirects to `www.example.org` moves onto the destination's slot for the rest of
> its crawl, so seeds that funnel into one host cannot collectively exceed the cap
> there. A seed URL the parser cannot read at all gets its own private slot and
> fails on its own instead of aborting the run.
>
> **Database impact of raising `scrape_workers`.** The scrape persistence path does
> **not** use the pooled connections in `src/utils/connection_pool.py`. Each catalog
> write goes through `PostgresCatalogService.upsert_resource()`, which opens a fresh
> `psycopg2.connect()` for the statement and closes it immediately. Workers therefore
> never block on a pool checkout — instead each in-flight write is one more *direct*
> connection, so the ceiling that matters is the Postgres server's `max_connections`.
> Failures there surface as `OperationalError` and are retried three times (2s, then
> 4s backoff) before the seed is abandoned. Keep `scrape_workers` comfortably below
> `max_connections` minus whatever the chat app and other services are already
> holding.

### Retrieval Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `retrievers.hybrid_retriever.num_documents_to_retrieve` | int | `5` | Top-k documents per query |
| `retrievers.hybrid_retriever.bm25_weight` | float | `0.6` | Weight for the normalized BM25 component (should sum to 1.0 with semantic) |
| `retrievers.hybrid_retriever.semantic_weight` | float | `0.4` | Weight for the normalized semantic component |
| `stemming.enabled` | bool | `false` | Enable Porter Stemmer for improved matching |

> **Note:** `use_hybrid_search` is a dynamic runtime setting (managed via the configuration API), not a YAML config key.

### Chunking

Controls how documents are split at ingestion. The default `character` strategy
uses the flat `chunk_size`/`chunk_overlap` settings above. Setting `strategy` to
`sentence` or `markdown` enables **hierarchical** parent-child chunking: small
embedded child nodes linked to larger parent context nodes (the parent text is
what a hierarchical-rerank retriever returns).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `chunking.strategy` | string | `character` | `character` (flat), `sentence`, or `markdown` (both hierarchical) |
| `chunking.parent_chunk_size` | int | `2048` | Target size of parent context nodes (hierarchical strategies only) |
| `chunking.child_chunk_size` | int | `512` | Target size of embedded child leaf nodes (hierarchical strategies only) |

```yaml
data_manager:
  chunking:
    strategy: sentence
    parent_chunk_size: 2048
    child_chunk_size: 512
```

> **Backward compatibility:** `parent_chunk_size`/`child_chunk_size` are optional.
> Omitting them reproduces the built-in defaults (2048/512), so an existing
> deployment's chunking is unchanged. They exist so a benchmark can sweep chunk
> sizes and recommend defaults from data — see
> [Benchmarking → Hierarchical-rerank A/B](benchmarking.md#hierarchical-rerank-ab).

### Sources

```yaml
data_manager:
  sources:
    links:
      input_lists:
        - miscellanea.list
      scraper:
        reset_data: true
        verify_urls: false
        enable_warnings: false
      selenium_scraper:
        enabled: false
    git:
      enabled: false
    sso:
      enabled: false
    jira:
      url: https://jira.example.com
      projects: []
      anonymize_data: true
      cutoff_date: null
    redmine:
      url: https://redmine.example.com
      project: null
      anonymize_data: true
```

The `visible` flag on any source (`sources.<name>.visible`) controls whether content appears in chat citations (default: `true`).

### Processing (HTML → Markdown & categorization)

A per-document processing stage runs at the persistence seam — *after* a resource is
collected (scraped/uploaded/fetched) and *before* it is written to disk. It is built
once from `data_manager.processing` and applied uniformly across **both** scheduled
ingest and the uploader UI, so the two never produce an inconsistent corpus.

Data flow per document: **collect → capture (title + source category) → convert (HTML→Markdown, KB article-body slice) → categorize (optional LLM) → persist**.

```yaml
data_manager:
  processing:
    html_to_markdown:
      enabled: true            # default: true (cheap, local)
    categorization:
      enabled: false           # default: false (one LLM call per document)
      provider: local          # provider key under services.chat_app.providers
      model: qwen3             # model id for that provider
      max_chars: 4000          # content is truncated to this many chars before the call
      max_concurrency: 1       # concurrent LLM calls; 1 = serial (default)
      categories:              # the closed set of labels the model must choose from
        - compute
        - storage
        - software
        - policy
```

| Key | Default | Effect |
| --- | --- | --- |
| `html_to_markdown.enabled` | `true` | Convert string HTML content (suffix `html`/`htm`) to ATX Markdown via `markdownify`, flip the suffix and path fields to `.md`, and record `metadata.converted_from = "html"`. The `.md` file then loads through `TextLoader` instead of `BSHTMLLoader`, so headings, lists, tables, and links survive into chunks. For FASRC KB (Echo-KB) pages, the converted Markdown is additionally sliced to the article body between the page's `Table of Contents` and `Bookmarkable Links` (or, when absent, `Last Updated`) landmarks, dropping the surrounding category-filter nav and footer; pages without those landmarks (non-KB sources) keep the full-page conversion. |
| `categorization.enabled` | `false` | Assign one label from `categories` to each document via an LLM and store it under `metadata.llm_category`. |
| `categorization.provider` / `model` | — | Which chat model to use. `provider` is a key under `services.chat_app.providers`; that block (base_url / mode / models / extra_kwargs) supplies the model's `provider_config`, so a custom local/vLLM endpoint is honored. |
| `categorization.max_chars` | `4000` | Document content is truncated to this length before the model call (bounds cost/latency). |
| `categorization.max_concurrency` | `1` | Upper bound on documents in an LLM call at once. Categorization runs inside `persist_resource`, which the scrape phase calls from a pool sized by `scrape_workers` — this knob keeps the request rate to the model provider decided by the model's limits rather than by a fetch-politeness setting. Anything that is not a positive integer coerces to `1`; a bad value never means "unbounded". |
| `categorization.categories` | `[]` | The closed label set. An empty list (or any error / out-of-list / model-not-configured) yields `metadata.llm_category = "uncategorized"`. |

**Behavior and caveats:**

- **No-op when disabled.** A **missing** `processing` block means conversion on,
  categorization off (the shipped default). An explicitly all-disabled block makes
  the persistence service behave byte-for-byte identically to the unwrapped service.
- **Never blocks ingest.** A conversion that raises, or that yields blank/whitespace
  Markdown (e.g. a script-only page), keeps the original resource. A categorization
  error never raises and defaults to `uncategorized`.
- **`llm_category` is distinct from `category`.** A source-provided
  `metadata.category` is **never** overwritten; the LLM label is written to
  `metadata.llm_category`. Both propagate to `documents.extra_json` and onward to
  `document_chunks.metadata`.
- **Source category capture (KB).** When conversion is enabled, an HTML page's
  breadcrumb category is captured *before* conversion into `metadata.category` — for
  FASRC KB pages this is the site taxonomy term (`Home › <Category> › <Article>`,
  e.g. `Storage`, `Cluster Usage`). It never overwrites a category a scraper already
  set (e.g. the Indico event category) and is silent on pages without a breadcrumb.
  Like the body slice, it takes effect only on **newly** ingested documents or when an
  already-persisted document is force-overwritten — see *Applying to an existing
  corpus* below.
- **Multi-line code becomes a fenced block.** A `<code>` element with no `<pre>`
  ancestor that contains a `<br>` is a code listing, not an inline span. The conversion
  wraps it in a `<pre>` before `markdownify` runs, so it becomes a fenced code block and
  no comment line inside it parses as a heading (issue #399). The fence gets an
  infostring only when a class on the element is one of `bash`, `sh`, `spec`, `lua`,
  `python`, `c`, `cpp`, `fortran`, `r`, `perl`, `json`, `yaml`, `text`; any other class
  gives a bare fence. A source newline next to a `<br>` (WordPress emits `<br />\n`) is
  dropped, so the fence has no blank line between code lines. Native `<pre>` blocks and
  single-line inline `<code>` convert as before. Like the body slice, the change reaches
  disk only for new or force-overwritten documents — see *Applying to an existing
  corpus* below.
- **Cost.** Categorization issues one LLM call per document — expensive on large
  crawls, hence off by default.
- **Local `.html` uploads are not converted.** Uploaded local files arrive as `bytes`
  (not string content), so the conversion guard skips them; scraped/web HTML is the
  target. (A decode-on-`html` path is future work.)
- **Applying to an existing corpus (a plain re-ingest is _not_ enough).** Standard-URL
  ingest calls `persist_resource(resource, output_dir)` **without** `overwrite`, and the
  persistence layer skips writing when the target path already exists. So for a document
  already on disk (an existing deployment), re-ingesting does **not** rewrite its
  persisted Markdown — the new body slice / `category` never reach disk, and the
  vectorstore (which detects refreshes by the persisted **filename**, e.g.
  `page.html` → `page.md`, not by content) keeps serving the old chunks. A content-only
  change under the same `.md` filename and unchanged hash therefore requires a
  **force-overwrite or a nuke + re-ingest** (or removing the document so it is re-added)
  to take effect. New documents are unaffected — they are sliced/categorized on first
  ingest.
- **Missing categorization provider fails loud, not silent.** If `categorization.enabled`
  is true but its `provider` is absent from `services.chat_app.providers`, the
  categorizer is **not** built (a prominent warning is logged and no `llm_category` is
  written) rather than falling back to a default endpoint and mislabeling every
  document. Conversion and ingest proceed normally.
- **Out of scope:** retrieval-time filtering by `llm_category` (retrievers do not read
  metadata filters yet), and retroactive conversion of already-ingested documents that
  are never re-ingested.

### Embedding Configuration

```yaml
data_manager:
  embedding_name: OpenAIEmbeddings
  embedding_class_map:
    OpenAIEmbeddings:
      class: OpenAIEmbeddings
      kwargs:
        model: text-embedding-3-small
      similarity_score_reference: 0.0
```

`similarity_score_reference` is a minimum cosine similarity in the range `0..1`; `0.0` means cite everything retrieved. See [Models & Providers](models_providers.md#embedding-models) for all embedding options.

### Anonymizer

```yaml
data_manager:
  utils:
    anonymizer:
      nlp_model: en_core_web_sm
      excluded_words: []
      greeting_patterns: []
      signoff_patterns: []
      email_pattern: '[\w\.-]+@[\w\.-]+\.\w+'
      username_pattern: '\[~[^\]]+\]'
```

---

## Agent Configuration Model

Archi no longer uses a top-level `archi:` block in standard deployment YAML.

Agent behavior is defined by:

- `services.chat_app.agent_class`: which pipeline class runs (for example `CMSCompOpsAgent`)
- `services.chat_app.agents_dir`: where agent spec markdown files live
- agent specs (`*.md`): selected tool subset (`tools`) and system prompt body
- `services.chat_app.tools`: optional agent-class-specific tool settings

Example:

```yaml
services:
  chat_app:
    agent_class: CMSCompOpsAgent
    agents_dir: examples/agents
    tools:
      monit:
        url: https://monit-grafana.cern.ch
```

See [Agents & Tools](agents_tools.md) for agent spec format and tool selection.

---

## Complete Example

```yaml
name: my_deployment

global:
  DATA_PATH: "/root/data/"
  ACCEPTED_FILES: [".txt", ".pdf", ".md"]
  verbosity: 3

services:
  chat_app:
    agent_class: CMSCompOpsAgent
    agents_dir: examples/agents
    default_provider: local
    default_model: llama3.2
    trained_on: "Course documentation"
    hostname: "example.mit.edu"
    external_port: 7861
    providers:
      local:
        enabled: true
        base_url: http://localhost:11434
        mode: ollama
        models:
          - llama3.2
  postgres:
    port: 5432
    database: archi-db
  vectorstore:
    backend: postgres

data_manager:
  sources:
    links:
      input_lists:
        - examples/deployments/basic-gpu/miscellanea.list
      scraper:
        reset_data: true
        verify_urls: false
  embedding_name: OpenAIEmbeddings
  chunk_size: 1000
  chunk_overlap: 0
```

> **Tip:** For the full base template with all defaults, see `src/cli/templates/base-config.yaml` in the repository.
