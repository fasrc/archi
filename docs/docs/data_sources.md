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

## Building a `sources.list` from a manifest

Instead of curating a web link list by hand, you can generate it from a typed
**manifest** with [`archi sources build`](cli_reference.md#archi-sources-build).
The manifest declares *where the URLs come from*; the command fetches and expands
them into the same one-URL-per-line `sources.list` the scraper already consumes.

### Manifest format

The manifest is a YAML list of seed entries. Every entry has a `type` and a
`url`:

```yaml
# sources.manifest.yaml
- type: sitemap          # fetch the sitemap XML and emit every <loc>
  url: https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml
  include: ["*/kb/*"]    # optional URL globs (fnmatch); keep only matches
  exclude: ["*/author/*"]# optional URL globs; drop matches

- type: crawl            # fetch an index page and extract its same-host links
  url: https://slurm.schedmd.com/archive/slurm-25.11.5/
  depth: 1               # optional, default 1
  include: []
  exclude: []

- type: literal          # emit a URL verbatim, never fetched or crawled
  url: https://en.wikipedia.org/wiki/Annie_Jump_Cannon
```

| Type | Behavior |
|------|----------|
| `sitemap` | Fetches the sitemap XML and emits every `<loc>`. Follows **one** level of `<sitemapindex>` nesting (each child sitemap is fetched once); a child that is itself an index is not followed. An empty sitemap is valid and contributes nothing. Honors `include`/`exclude` globs. |
| `crawl` | Fetches the index page, extracts anchor links, resolves relative links against the seed URL, and keeps only **same-host** links. Honors an optional `depth` (default 1) and `include`/`exclude` globs. Output is sorted for deterministic diffs. |
| `literal` | Emits the URL verbatim — never fetched, crawled, or glob-filtered. The URL is still normalized like every other entry (see below). |

An unknown `type`, a missing `url`, or invalid YAML makes the command exit
non-zero without writing anything.

### Output, normalization, and manual extras

The target list is regenerated **wholesale** every run. Every URL is normalized
(fragment dropped, scheme/host lowercased, a single trailing path slash
collapsed) and deduplicated preserving first-seen order, so `--dry-run` diffs
stay small and meaningful.

To keep hand-added entries — including prefixed lines like `git-…`, `sso-…`,
`elog-…`, `indico-…` — put them in a `manual-extras.list` beside the output.
Its non-comment, non-blank entries are appended verbatim after the generated
block. The generated block wins position: an extras line that duplicates a
generated URL is dropped so the URL appears exactly once.

### Build → import workflow

```bash
# 1. Preview the diff against the current list
archi sources build sources.manifest.yaml -c config.yaml --dry-run

# 2. Write the regenerated list (resolves the single input_lists entry)
archi sources build sources.manifest.yaml -c config.yaml

# 3. Write and re-ingest the deployment in one step
archi sources build sources.manifest.yaml -c config.yaml \
  --name dev --env-file .secrets.env --import
```

With `--import`, the command shells out to
`archi create --name <deployment> --config <config> --services <services> --force`
after a successful write (services default to `chatbot`). See the
[CLI Reference](cli_reference.md#archi-sources-build) for every flag.

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

## Indico

Ingest events, contributions (talks) and slide materials from an [Indico](https://getindico.io/) instance. Slides in PDF/PPTX/PPT/ODP format are converted to Markdown via [MarkItDown](https://github.com/microsoft/markitdown).

### Configuration

```yaml
data_manager:
  sources:
    indico:
      enabled: true
      base_url: https://indico.cern.ch
      use_sso: false       # set true for SSO-protected events
      slide_conversion:
        enabled: true
        formats: [pdf, pptx, ppt, odp]
```

Add event URLs to your link lists. URLs with `indico` in the hostname and `/event/` in the path are auto-detected. For Indico instances without `indico` in the hostname, use the explicit `indico-` prefix:

```
https://indico.cern.ch/event/1408515/
https://indico.stfc.ac.uk/event/1825/
indico-https://events.example.org/event/42/
```

For each event the scraper produces Markdown resources for the event metadata, each contribution (talk), and each slide deck. Multi-day events can be restricted with day-filtering options (`max_days`, `only_first_day`, `days`, `date_range`) — see `src/cli/templates/base-config.yaml` for the full set.

For SSO-protected events, set `use_sso: true` and configure the Selenium-based collector as for [SSO-Protected Links](#sso-protected-links). The same `SSO_USERNAME` / `SSO_PASSWORD` secrets are used.

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
