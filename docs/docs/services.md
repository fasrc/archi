# Services

Archi supports several **services** — containerized applications that interact with the AI pipelines. Services are enabled at deploy time with the `--services` flag.

```bash
archi create [...] --services chatbot,uploader,grafana
```

List all available services with:

```bash
archi list-services
```

---

## Chat Interface

The primary user-facing service. Provides a web-based chat application for interacting with Archi's AI agents.

**Default port:** `7861`

### Key Features

- Streaming responses with tool-call visualization
- Agent selector dropdown for switching between agents
- Built-in [Data Viewer](data_sources.md#data-viewer) at `/data`
- Settings panel for model/provider selection
- [BYOK](models_providers.md#bring-your-own-key-byok) support
- Conversation history
- [Service Status Board & Alert Banners](#service-status-board--alert-banners)

### Configuration

```yaml
services:
  chat_app:
    agent_class: CMSCompOpsAgent
    agents_dir: examples/agents
    default_provider: local
    default_model: llama3.2
    trained_on: "Course documentation"
    hostname: "example.mit.edu"
    port: 7861
    external_port: 7861
```

### Running

```bash
archi create [...] --services chatbot
```

---

## Service Status Board & Alert Banners

The Service Status Board (SSB) is a built-in feature of the Chat Interface that lets designated operators communicate service health, planned downtime, known issues, and general announcements directly to all users — without external tooling.

### How It Works

**Alert banners** appear as colour-coded strips at the top of every page in the chat app. Up to 5 active alerts are displayed at once. Each banner can be individually dismissed by the user client-side. A **details** link redirects to the full status board.

The **Status Board** at `/ssb/status` provides:

- **Active Alerts** — non-expired alerts with severity badges, creator, and timestamp
- **Expired Alerts** — historical record shown at reduced opacity
- **Post New Alert form** — visible only to configured alert managers
- **Deployment** and **Knowledge Base** panels — which config is deployed and how the corpus was built; see [Deployment and Knowledge Base provenance](#deployment-and-knowledge-base-provenance)

### Deployment and Knowledge Base provenance

Two panels at the top of `/ssb/status` show where the running service came from. Both are read from Postgres. If a record cannot be read, the panel says it is unavailable and the alert sections still render.

**Deployment panel** (newest row of `deployment_record`, written by each deploy):

- **Config pin** — the config ref and short commit SHA that the deploy was pinned to, with one of three verdicts:
    - **✓ matches pin** — the deploy recorded that the config checkout was the pinned commit, with no tracked edits.
    - **pin verdict not recorded** — the deploy recorded no verdict (for example a hand-run `archi create`). This is not a claim that the config is clean, and not a claim that it was edited.
    - **⚠ live-edited config** — a warning box shown when there is evidence the running config is not the pin. It says "The deployed config is not the pinned commit." only when the deploy recorded a mismatch. It lists the tracked files edited on top of the pin (first 5, then "… N more").
- **Deployed HEAD** — shown only with the live-edited warning: the commit the config checkout was actually on.
- **App version** and **Deployed** — the archi version and the deploy time (UTC).

If no record exists, the panel says "Deployment provenance unavailable — recorded from the next deploy onward."

**Knowledge Base panel** (newest *successful* row of `ingest_run`, status `updated` or `up_to_date`):

- **Last ingest** — start time, end time (UTC), and duration of that run.
- **Documents** — embedded, failed, and pending counts at the end of that run.
- **Chunks** — the chunk count for the active collection only (chunks tagged with that collection, plus untagged chunks). Rows recorded before this rule counted chunks from every collection.
- **Ingest configuration (as used at ingest)** — the ingest settings saved with that run, such as chunking, embedding model, and **embedding dimensions**. With no `dimensions` set in `embedding_class_map`, the recorded dimension is the model's default (1536 for `OpenAIEmbeddings`, 384 for `HuggingFaceEmbeddings` and unknown models). Rows recorded before this rule stored 384 for every model; for those rows a change from 384 to the model's default is not reported as drift. An explicit `dimensions: 384` cannot yet be told apart from that old default (follow-up [#654](https://github.com/fasrc/archi/issues/654)).
- **⚠ configuration changed after this ingest** — the running config differs from the saved one. Each changed setting is listed with its value now and at ingest.

Unknown and failure states:

- **Current configuration unavailable — drift not evaluated.** The running config could not be read, so no drift check was done. This does not mean there is no drift.
- **⚠ the most recent ingest attempt failed at …** — a failed run is newer than the run shown. The counts and config are from the last successful run, but the failed attempt may have changed the live corpus (deletions and partial additions are committed as they happen).
- **No successful ingest run recorded** — no successful run exists yet. If a failed attempt exists, its time is shown below.

Values are recorded when a deploy or ingest runs. A row written before a rule above existed keeps its old value until the next deploy or ingest.

### Severity Levels

| Severity | Colour | Intended Use |
|----------|--------|--------------|
| `alarm` | Red | Service outage or critical failure |
| `warning` | Amber | Degraded performance, elevated error rate |
| `news` | Blue | Release notes, planned maintenance |
| `info` | Slate | General informational notices |

### Creating and Deleting Alerts

Navigate to **Status** in the main chat header (or go to `/ssb/status` directly). The **Post New Alert** form is shown to users who have alert manager access. Fill in:

- **Message** (required) — short text shown in the banner
- **Severity** (required) — one of `alarm`, `warning`, `news`, `info`
- **Description** (optional) — longer explanation shown only on the status page
- **Expires at** (optional) — datetime after which the alert is hidden from banners; expired alerts remain visible in the status board history

To delete an alert, click the **Delete** button on its card on the status board. Deletion is permanent.

Alerts can also be created via the REST API:

```bash
curl -X POST http://localhost:7861/api/ssb/alerts \
  -H 'Content-Type: application/json' \
  -d '{
    "severity": "warning",
    "message": "Embedding pipeline running — responses may be slower than usual",
    "description": "Optional longer explanation shown on the status board.",
    "expires_in_hours": 4
  }'
```

Or with an explicit expiry timestamp:

```bash
curl -X POST http://localhost:7861/api/ssb/alerts \
  -H 'Content-Type: application/json' \
  -d '{
    "severity": "alarm",
    "message": "Model backend unavailable",
    "expires_at": "2026-02-21T18:00:00"
  }'
```

### API Endpoints

| Method | Route | Auth Required | Description |
|--------|-------|---------------|-------------|
| `GET` | `/ssb/status` | Any authenticated user | Render the status board page |
| `POST` | `/api/ssb/alerts` | Alert managers only | Create a new alert |
| `DELETE` | `/api/ssb/alerts/<id>` | Alert managers only | Delete an alert by ID |

### Access Control

Alert managers are configured via `services.chat_app.alerts.managers` (username list) or the `alerts:manage` RBAC permission. The rules are:

1. **Auth disabled** → everyone may create and delete alerts.
2. **Auth enabled** → a user is an alert manager if **either**:
    - their username is in the `alerts.managers` list, **or**
    - their session roles grant the `alerts:manage` permission.
3. **Auth enabled, no username match, no `alerts:manage` permission** → nobody may manage (safe default; a warning is logged).

All users can always *view* alerts and the status board regardless of access level.

```yaml
# Username-based access (backwards compatible):
services:
  chat_app:
    alerts:
      managers:
        - alice
        - bob

# Role-based access (can be combined with the above):
services:
  chat_app:
    auth:
      auth_roles:
        roles:
          ops-team:
            permissions:
              - alerts:manage
```

See [Configuration → `services.chat_app.alerts`](configuration.md#serviceschat_appalerts) for the full reference.

---

## Document Upload

Document upload is exposed in the chat UI and backed by the **Data Manager** service. Documents can be uploaded via the web interface or by copying files directly into the data directory.

See [Data Sources — Adding Documents Manually](data_sources.md#adding-documents-manually) for setup instructions.

---

## Data Manager

A background service that handles data ingestion, vectorstore management, and scheduled re-scraping. It is automatically started with most deployments.

**Default port:** `7871`

### Features

- Orchestrates all data collectors (links, git, JIRA, Redmine)
- Manages the vectorstore (chunking, embedding, indexing)
- Provides a scheduling system for periodic re-ingestion
- Exposes API endpoints for ingestion status and schedule management

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/ingestion/status` | GET | Current ingestion progress |
| `/api/reload-schedules` | POST | Trigger schedule reload from database |
| `/api/schedules` | GET | Current schedule status |

### Configuration

```yaml
services:
  data_manager:
    port: 7871
    external_port: 7871
```

---

## Piazza Interface

Reads posts from a Piazza forum and posts draft responses to a specified Slack channel.

### Setup

1. Go to [Slack Apps](https://api.slack.com/apps) and sign in to your workspace.
2. Click **Create New App** → **From scratch**. Name the app and select the workspace.
3. Go to **Incoming Webhooks** under Features and toggle it on.
4. Click **Add New Webhook** and select the target channel.
5. Copy the **Webhook URL** to your secrets file.

### Configuration

Get the Piazza network ID from the class homepage URL (e.g., `https://piazza.com/class/m0g3v0ahsqm2lg` → `m0g3v0ahsqm2lg`).

```yaml
services:
  piazza:
    agent_class: QAPipeline
    provider: local
    model: llama3.2
    network_id: <your Piazza network ID>
  chat_app:
    trained_on: "Your class materials"
```

### Secrets

```bash
PIAZZA_EMAIL=...
PIAZZA_PASSWORD=...
SLACK_WEBHOOK=...
```

### Running

```bash
archi create [...] --services chatbot,piazza
```

---

## Redmine / Mailbox Interface

Reads new tickets in a Redmine project, drafts a response as a comment, and sends it as an email when the ticket is marked "Resolved" by an admin.

### Configuration

```yaml
services:
  redmine_mailbox:
    url: https://redmine.example.com
    project: my-project
    redmine_update_time: 10
    mailbox_update_time: 10
    answer_tag: "-- Archi -- Resolving email was sent"
```

### Secrets

```bash
IMAP_USER=...
IMAP_PW=...
REDMINE_USER=...
REDMINE_PW=...
SENDER_SERVER=...
SENDER_PORT=587
SENDER_REPLYTO=...
SENDER_USER=...
SENDER_PW=...
```

### Running

```bash
archi create [...] --services chatbot,redmine-mailer
```

---

## Mattermost Interface

Reads posts from a Mattermost forum and posts draft responses to a specified channel.

### Configuration

```yaml
services:
  mattermost:
    update_time: 60
```

### Secrets

```bash
MATTERMOST_WEBHOOK=...
MATTERMOST_PAK=...
MATTERMOST_CHANNEL_ID_READ=...
MATTERMOST_CHANNEL_ID_WRITE=...
```

### Running

```bash
archi create [...] --services chatbot,mattermost
```

---

## Slack Interface

A Slack bot that answers `@archi` mentions in channels and direct messages, in the Slack thread of the question. The bot is a thin client of the chat app: it sends each question, with the earlier messages of its thread, to the chat app's [`/v1/chat/completions`](api-reference-v1.md) endpoint. It loads no model and needs no GPU.

- One archi conversation for each Slack thread. A follow-up in the same thread continues it.
- The bot connects to Slack in Socket Mode, an outbound websocket, so the deployment needs no public URL.
- The bot posts a short placeholder at once and replaces it with the answer and its sources.

### Prerequisites

1. **Turn on the `/v1` API** in the chat app: `services.chat_app.openai_compat.enabled: true`. The Slack service checks `GET /v1/models` at start-up and stops with an error if `/v1` does not answer. On a host-mode deployment with authentication off, set `local_only: true` instead of `enabled: true`, so `/v1` answers only the bot on the same host (see [Local callers only](api-reference-v1.md#local-callers-only)).
2. **Create the Slack app.** A Slack workspace admin creates it from the [app manifest](#slack-app-manifest) below (api.slack.com → Your Apps → Create New App → From a manifest), then installs it in the workspace.
3. **Get the two tokens.** In the app settings, Basic Information → App-Level Tokens → create a token with the scope `connections:write`: this is `SLACK_APP_TOKEN` (`xapp-…`). OAuth & Permissions → Bot User OAuth Token: this is `SLACK_BOT_TOKEN` (`xoxb-…`).
4. **Invite the bot** to each channel where it must answer (`/invite @archi`).

### Slack app manifest

The bot needs `app_mentions:read` and `message.im` to receive questions, `chat:write` to answer, and the three `*:history` scopes to read the earlier messages of a thread.

```yaml
_metadata:
  major_version: 1
display_information:
  name: archi
features:
  bot_user:
    display_name: archi
    always_online: false
  app_home:
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - chat:write
      - channels:history
      - groups:history
      - im:history
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.im
  socket_mode_enabled: true
  org_deploy_enabled: false
  is_hosted: false
  token_rotation_enabled: false
```

!!! warning "Keep the Slack app internal"
    Do not distribute the app outside your workspace. Slack limits `conversations.replies`, which the bot uses to read a thread, to 1 request per minute for apps distributed outside the Slack Marketplace. Internal apps get Tier 3 limits.

!!! note "Who can ask"
    Every member of the workspace who can message the bot can query the knowledge base. The Slack workspace is the access boundary. All Slack questions use one archi identity; the bot log records the Slack user ID of each question.

### Configuration

```yaml
services:
  chat_app:
    openai_compat:
      enabled: true                 # or, host mode + auth off: local_only: true alone
  slack:
    chat_url: http://chatbot:7861   # default: the chatbot container, or localhost in host mode
    timeout_seconds: 600            # longest wait for one answer
    max_workers: 4                  # Slack threads answered at the same time
    history_limit: 20               # earlier thread messages sent with a question (0 = none)
```

All four keys are optional. Questions in one thread are answered one at a time, in order; different threads run in parallel up to `max_workers`.

### Secrets

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
# Required only when services.chat_app.auth.enabled is true:
ARCHI_API_TOKEN=archi_...
```

When chat authentication is on, `/v1` needs a bearer token. Create one with `POST /api/users/me/api-token` (see the [/v1 API reference](api-reference-v1.md)) as the archi user that Slack questions run as. `archi create` refuses to deploy the Slack service without it.

!!! warning "Turning on chat authentication later"
    If you turn on `services.chat_app.auth.enabled` on a deployment that already runs the Slack service, add `ARCHI_API_TOKEN` to the secrets file, then run `archi create --force` with the same services. `archi restart --service chatbot` does not give the token to the Slack container, and the bot then gets HTTP 401 for each question.

### Running

```bash
archi create [...] --services chatbot,slack
```

---

## Grafana Monitoring

Monitor system performance and LLM usage with a pre-configured Grafana dashboard.

**Default port:** `3000`

> **Note:** If redeploying with an existing name (without removing volumes), the PostgreSQL Grafana user may not have been created. Deploy a fresh instance to avoid issues.

### Configuration

```yaml
services:
  grafana:
    external_port: 3000
```

### Secrets

```bash
PG_PASSWORD=<your_database_password>
GRAFANA_PG_PASSWORD=<grafana_db_password>
```

### Running

```bash
archi create [...] --services chatbot,grafana
```

After deployment, access Grafana at `your-hostname:3000`. The default login is `admin`/`admin` — you'll be prompted to change the password on first login. Navigate to **Menu → Dashboards → Archi → Archi Usage** for the main dashboard.

> **Tip:** For the "Recent Conversation Messages" panel, click the three dots → **Edit** → find "Override 4" → enable **Cell value inspect** to expand long text entries. Click **Apply** to save.

---

## Grader Interface

An automated grading service for handwritten assignments with a web interface.

> **Note:** This service is experimental and not yet fully generalized.

### Requirements

The following files are needed:

- **`users.csv`**: Two columns — `MIT email` and `Unique code`
- **`solution_with_rubric_*.txt`**: One file per problem, named with the problem number. Begins with the problem name and a line of dashes.
- **`admin_password.txt`**: Admin code for resetting student attempts (passed as a secret).

### Configuration

```yaml
services:
  grader_app:
    provider: local
    model: llama3.2
    prompts:
      grading:
        final_grade_prompt: final_grade.prompt
      image_processing:
        image_processing_prompt: image_processing.prompt
    num_problems: 1
    local_rubric_dir: ~/grading/my_rubrics
    local_users_csv_dir: ~/grading/logins
  chat_app:
    trained_on: "rubrics, class info, etc."
```

### Secrets

```bash
ADMIN_PASSWORD=your_password
```

### Running

```bash
archi create [...] --services grader
```
