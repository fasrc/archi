# Data Sources

Archi ingests content from a variety of **data sources** into the PostgreSQL-backed vector store used for document retrieval. Sources are configured in `data_manager.sources` in your YAML configuration.

> **Note:** The `links` source is always enabled by default — you do not need to pass it explicitly.

---

## Web Link Lists

A web link list is a simple text file containing one URL per line. Archi fetches the content from each URL and adds it to the vector store using the `Scraper` class.

### Configuration

Define which link lists to ingest in your configuration file:

```yaml
data_manager:
  sources:
    links:
      input_lists:
        - miscellanea.list
        - additional_urls.list
```

Each `.list` file contains one URL per line:

```
https://example.com/page1
https://example.com/page2
```

### Customizing the Scraper

You can tune HTTP scraping behaviour:

```yaml
data_manager:
  sources:
    links:
      scraper:
        reset_data: true
        verify_urls: false
        enable_warnings: false
```

### SSO-Protected Links

If some links are behind a Single Sign-On (SSO) system, enable the SSO source and configure the Selenium-based collector:

```yaml
data_manager:
  sources:
    sso:
      enabled: true
    links:
      selenium_scraper:
        enabled: true
        selenium_class: CERNSSOScraper
        selenium_class_map:
          CERNSSOScraper:
            kwargs:
              headless: true
              max_depth: 2
```

With `sso.enabled: true`, prefix protected URLs with `sso-`:

```
sso-https://example.com/protected/page
```

**Secrets:**

```bash
SSO_USERNAME=username
SSO_PASSWORD=password
```

### Running

Link scraping is controlled by your config (`data_manager.sources.links.enabled`).

---

## Git Scraping

Ingest content from MkDocs-based git repositories using the `GitScraper` class, which extracts Markdown content directly instead of scraping rendered HTML.

### Configuration

```yaml
data_manager:
  sources:
    git:
      enabled: true
```

In your link lists, prefix repository URLs with `git-`:

```
git-https://github.com/example/mkdocs/documentation.git
```

### Secrets

```bash
GIT_USERNAME=your_username
GIT_TOKEN=your_token
```

Once enabled in config, deploy normally with `archi create --config <config.yaml> --services <...>`.

---

## JIRA

Fetch issues and comments from specified JIRA projects using the `JiraClient` class.

### Configuration

```yaml
data_manager:
  sources:
    jira:
      url: https://jira.example.com
      projects:
        - PROJECT_KEY
      anonymize_data: true
      cutoff_date: "2023-01-01"
```

The optional `cutoff_date` skips tickets created before the specified ISO-8601 date.

### Anonymization

Customize data anonymization to remove personal information:

```yaml
data_manager:
  utils:
    anonymizer:
      nlp_model: en_core_web_sm
      excluded_words:
        - Example
      greeting_patterns:
        - '^(hi|hello|hey|greetings|dear)\b'
      signoff_patterns:
        - '\b(regards|sincerely|best regards|cheers|thank you)\b'
      email_pattern: '[\w\.-]+@[\w\.-]+\.\w+'
      username_pattern: '\[~[^\]]+\]'
```

### Secrets

```bash
JIRA_PAT=<your_jira_personal_access_token>
```

Once enabled in config, deploy normally with `archi create --config <config.yaml> --services <...>`.

---

## Redmine

Ingest solved tickets (question/answer pairs) from Redmine into the vector store.

### Configuration

```yaml
data_manager:
  sources:
    redmine:
      url: https://redmine.example.com
      project: my-project
      anonymize_data: true
```

### Secrets

```bash
REDMINE_USER=...
REDMINE_PW=...
```

Once enabled in config, deploy normally with `archi create --config <config.yaml> --services <...>`.

> To automate email replies to resolved tickets, also enable the `redmine-mailer` service. See [Services](services.md).

---

## Adding Documents Manually

### Document Upload (via Chat UI)

The chatbot service includes a built-in document upload interface. When logged in to the chat UI, navigate to `/upload` to upload documents through your browser.

**First-time setup — create an admin account:**

```bash
docker exec -it <CONTAINER-ID> bash
python -u src/bin/service_create_account.py
```

Run the script from the `/root/archi` directory inside the container. After creating an account, visit the chat UI to log in and upload documents.

### Directly Copying Files

Documents used for RAG live in the container at `/root/data/<directory>/`. You can copy files directly:

```bash
docker cp myfile.pdf <container-id>:/root/data/my_docs/
```

To create a new directory inside the container:

```bash
docker exec -it <container-id> mkdir /root/data/my_docs
```

---

## Re-ingesting to Backfill Title-Aware Search Text

Title- and filename-aware retrieval injects a `Title: …\nSource: …` header into the
searchable text of **every** chunk at ingestion time, so that both the embedding and the
full-text index contain the document's title (`display_name`) and filename. This is
controlled by `data_manager.title_header.enabled` (default `true`).

Because the header is baked into a chunk's embedding when the chunk is embedded, the
injected header only affects chunks that are ingested **after** the feature is enabled.
Documents that were ingested before enabling it keep their body-only embeddings until they
are re-embedded. Re-ingesting (re-embedding) existing corpora is therefore required to get
the full benefit for already-ingested content.

> **Note:** The weighted full-text index re-ranks title/filename matches for the *existing*
> corpus immediately, without re-embedding — applying the idempotent migration
> (`src/cli/templates/migrations/0001_weighted_chunk_tsv.sql`) rebuilds the title/filename
> weighting over the current rows. Re-embedding is only needed to add title/filename tokens
> to the **semantic** (vector) representation.

### Backfill procedure

1. **Ensure header injection is enabled** in your configuration (it is on by default):

   ```yaml
   data_manager:
     title_header:
       enabled: true
   ```

2. **Force every document to be re-embedded.** Set `reset_collection: true` so the
   data-manager truncates `document_chunks` and resets each document's ingestion status to
   `pending` on startup, then re-embeds the full corpus from the persisted source content:

   ```yaml
   data_manager:
     reset_collection: true
   ```

3. **Re-run the data-manager.** Re-deploy (or restart the `data-manager` service) so the
   ingestion run picks up the new setting and re-embeds every document with the injected
   header:

   ```bash
   archi create --config <config.yaml> --services data-manager
   ```

4. **Confirm the backfill.** Watch the data-manager logs and verify documents move back to
   an indexed state:

   ```bash
   docker logs -f archi-<name>-data-manager
   ```

   You can also browse the [Data Viewer](#data-viewer) to confirm chunk counts and content
   include the injected `Title:`/`Source:` header.

5. **Turn `reset_collection` back off** once the backfill completes, so subsequent
   deployments do not wipe and re-embed the collection again:

   ```yaml
   data_manager:
     reset_collection: false
   ```

> **Cost:** Re-embedding the entire corpus calls the embedding model once per chunk, so the
> backfill incurs the same cost as the original ingestion. For API-based embedding providers
> this is a billable operation; run it during a maintenance window for large corpora.

If you only want to re-embed without permanently wiping unrelated state, the same effect is
achieved per deployment by the standard ingestion run with `reset_collection: true`; there
is no partial backfill that re-embeds only stale documents — re-embedding is all-or-nothing
via `reset_collection`.

---

## Data Viewer

The chat interface includes a built-in **Data Viewer** for browsing and managing ingested documents. Access it at `/data` on your chat app (e.g., `http://localhost:7861/data`).

### Features

- **Browse documents**: View all ingested documents with metadata (source, file type, chunk count)
- **Search and filter**: Filter documents by name or source type
- **View content**: Click a document to see its full content and individual chunks
- **Enable/disable documents**: Toggle whether specific documents are included in RAG retrieval
- **Bulk operations**: Enable or disable multiple documents at once

### Document States

| State | Description |
|-------|-------------|
| Enabled | Document chunks are included in retrieval (default) |
| Disabled | Document is excluded from retrieval but remains in the database |

Disabling documents is useful for temporarily excluding outdated content, testing retrieval with specific document subsets, or hiding sensitive documents from certain users.

---

## Source Configuration Notes

- Source configuration is persisted to PostgreSQL (`static_config.sources_config`) at deployment time and used at runtime.
- The `visible` flag on a source (`sources.<name>.visible`) controls whether content from that source appears in chat citations and user-facing listings. It defaults to `true`.
- All sources can be listed with `archi list-services`.
