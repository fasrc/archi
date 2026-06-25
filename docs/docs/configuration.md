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
| `parallel_workers` | int | `32` | Parallel ingestion workers |
| `reset_collection` | bool | `true` | Wipe collection on startup |
| `distance_metric` | string | `cosine` | Similarity metric: `cosine`, `l2`, `ip` |

### Retrieval Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `retrievers.hybrid_retriever.num_documents_to_retrieve` | int | `5` | Top-k documents per query |
| `retrievers.hybrid_retriever.bm25_weight` | float | `0.6` | BM25 keyword score weight |
| `retrievers.hybrid_retriever.semantic_weight` | float | `0.4` | Semantic similarity weight |
| `stemming.enabled` | bool | `false` | Enable Porter Stemmer for improved matching |

> **Note:** `use_hybrid_search` is a dynamic runtime setting (managed via the configuration API), not a YAML config key.

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

Data flow per document: **collect → convert (HTML→Markdown) → categorize (optional LLM) → persist**.

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
      categories:              # the closed set of labels the model must choose from
        - compute
        - storage
        - software
        - policy
```

| Key | Default | Effect |
| --- | --- | --- |
| `html_to_markdown.enabled` | `true` | Convert string HTML content (suffix `html`/`htm`) to ATX Markdown via `markdownify`, flip the suffix and path fields to `.md`, and record `metadata.converted_from = "html"`. The `.md` file then loads through `TextLoader` instead of `BSHTMLLoader`, so headings, lists, tables, and links survive into chunks. |
| `categorization.enabled` | `false` | Assign one label from `categories` to each document via an LLM and store it under `metadata.llm_category`. |
| `categorization.provider` / `model` | — | Which chat model to use. `provider` is a key under `services.chat_app.providers`; that block (base_url / mode / models / extra_kwargs) supplies the model's `provider_config`, so a custom local/vLLM endpoint is honored. |
| `categorization.max_chars` | `4000` | Document content is truncated to this length before the model call (bounds cost/latency). |
| `categorization.categories` | `[]` | The closed label set. An empty list (or any error / out-of-list / model-not-configured) yields `metadata.llm_category = "uncategorized"`. |

**Behavior and caveats:**

- **No-op when disabled.** A **missing** `processing` block means conversion on,
  categorization off (the shipped default). An explicitly all-disabled block makes
  the persistence service behave byte-for-byte identically to the unwrapped service.
- **Never blocks ingest.** A conversion that raises, or that yields blank/whitespace
  Markdown (e.g. a script-only page), keeps the original resource. A categorization
  error never raises and defaults to `uncategorized`.
- **`llm_category` is distinct from `category`.** A source-provided
  `metadata.category` (e.g. the Indico scraper's event category) is **never**
  overwritten; the LLM label is written to `metadata.llm_category`. Both propagate to
  `documents.extra_json` and onward to `document_chunks.metadata`.
- **Cost.** Categorization issues one LLM call per document — expensive on large
  crawls, hence off by default.
- **Local `.html` uploads are not converted.** Uploaded local files arrive as `bytes`
  (not string content), so the conversion guard skips them; scraped/web HTML is the
  target. (A decode-on-`html` path is future work.)
- **Re-ingest refreshes chunks.** Hashes are identity-based (URL/path), so converting
  an already-ingested HTML document keeps its hash. On re-ingest the vectorstore
  detects that the persisted filename changed (`page.html` → `page.md`) and refreshes
  that document's chunks, so retrieval never serves stale HTML-flattened content.
  Note this detection keys off the persisted **filename**, not the content: it catches
  the HTML→Markdown case (the extension always flips) but a content-only change that
  keeps the **same filename** under an unchanged hash is not detected and would retain
  its old chunks until the hash changes or the document is removed and re-added.
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
      similarity_score_reference: 10
```

See [Models & Providers](models_providers.md#embedding-models) for all embedding options.

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
