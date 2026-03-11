# Archi Data Ingestion Caching Strategy

## The Problem

Archi's data manager re-fetches every source URL on each container restart and scheduled ingestion cycle. Source platforms like TWiki are complaining about the hammering, and during development the same content gets pulled over and over. There's no HTTP-level caching, no conditional requests, and no rate limiting.

The `PersistenceService` already skips re-writing files it has on disk (hash-based dedup in `collectors/persistence.py:35`), but the **network requests still happen every time** — the scraper fetches the full content before persistence can check if it already has it.

---

## Two Independent Approaches

These solve the same problem at different layers. **Choose one based on your constraints**, or implement both if needed — they don't conflict.

| | Squid Proxy | requests-cache |
|---|---|---|
| **Layer** | Network (HTTP proxy) | Application (Python) |
| **Code changes** | None — config only | ~20 lines in scraper.py |
| **HTTPS support** | No (CONNECT tunnel, uncached) | Yes (caching happens after TLS) |
| **Persistence** | Squid disk/memory cache (new volume) | Existing PostgreSQL (no new infra) |
| **Covers** | Any container's HTTP traffic | Only `requests.Session()` calls |
| **Doesn't cover** | HTTPS, git, JIRA/Redmine APIs, Selenium | Git, JIRA/Redmine APIs, Selenium |
| **Operational overhead** | New container to manage | New pip dependency + DB tables |
| **Conditional GET (ETag/304)** | Yes (for HTTP) | Yes (for HTTP and HTTPS) |

---

## Option A: Squid Caching Proxy (Network Layer)

A Squid container sits between archi's data-manager and the internet. Every HTTP request passes through it, and Squid serves cached responses for repeat fetches. Zero code changes to archi — just add a container and set environment variables.

**Best for:** Environments where multiple containers fetch from the same sources, or where you want caching without touching application code.

**Limitation:** Cannot cache HTTPS content without SSL bump (significant complexity). HTTPS requests pass through as CONNECT tunnels, uncached.

### squid.conf (aggressive dev mode)

```squid
http_port 3128

# 5GB disk cache, 256MB memory cache
cache_dir ufs /var/spool/squid 5000 16 256
cache_mem 256 MB
maximum_object_size 512 MB

# Cache everything aggressively — override server no-cache headers
refresh_pattern -i \.(pdf|doc|docx)$    43200 90% 432000 override-expire ignore-no-cache ignore-private
refresh_pattern -i \.(md|markdown|rst)$ 1440  90% 43200  override-expire ignore-no-cache ignore-private
refresh_pattern -i \.html?$             1440  90% 43200  override-expire ignore-no-cache ignore-private
refresh_pattern .                       1440  90% 86400  override-expire ignore-no-cache ignore-private reload-into-ims

# Allow all traffic (internal network only)
acl localnet src 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16
http_access allow localnet
http_access allow localhost
http_access deny all

access_log /var/log/squid/access.log
cache_log /var/log/squid/cache.log
```

### Add to docker-compose

```yaml
squid-cache:
  image: ubuntu/squid:latest
  container_name: archi-squid-cache
  volumes:
    - ./squid.conf:/etc/squid/squid.conf:ro
    - squid-cache-data:/var/spool/squid
  networks:
    - archi-net
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "squid", "-k", "check"]
    interval: 30s
    timeout: 10s
    retries: 3
```

Then add to the data-manager service:

```yaml
data-manager:
  environment:
    HTTP_PROXY: http://squid-cache:3128
    HTTPS_PROXY: http://squid-cache:3128
  depends_on:
    squid-cache:
      condition: service_healthy
```

### What Squid Caches vs. Doesn't

| Content | Cached? | Why |
|---|---|---|
| HTTP HTML pages | Yes | Standard HTTP caching |
| HTTP PDFs/docs | Yes | Binary objects cached up to 512MB |
| HTTPS anything | **No** | CONNECT tunnel — Squid can't inspect |
| Git clones | **No** | Uses SSH or git protocol, not HTTP proxy |
| JIRA/Redmine APIs | **No** | HTTPS API calls tunnel through |
| Selenium pages | **No** | WebDriver doesn't use HTTP_PROXY |

---

## Option B: requests-cache at the Application Layer

Drop in the `requests-cache` library to wrap `requests.Session()`. Every HTTP response gets cached to the existing PostgreSQL database. Handles HTTPS transparently, works with all content types, and survives container restarts with no extra volumes or services.

**Best for:** Environments where HTTPS sources are common (most modern sites), or where you want caching without adding infrastructure.

### Step 1: Add the dependency

In `pyproject.toml`, add to the `dependencies` list (line 15):

```toml
dependencies = [
    # ... existing deps ...
    "requests-cache>=1.1",
    # psycopg2-binary already present — required by requests-cache postgresql backend
]
```

Then rebuild: `pip install -e .`

> **Note:** `psycopg2-binary==2.9.10` is already a dependency — no extra driver needed.

### Step 2: Add a cached session factory to LinkScraper

**File:** `src/data_manager/collectors/scrapers/scraper.py`

Add a new method to `LinkScraper` and a module-level helper for use from `DataManager`:

```python
# new imports at top of scraper.py
import os
import requests_cache
from datetime import timedelta
from src.utils.env import read_secret

# new method on LinkScraper class
def _create_cached_session(self) -> requests.Session:
    """Create a cached HTTP session backed by PostgreSQL.

    Uses the same Postgres instance archi already runs.
    Falls back to a plain session if cache setup fails.
    """
    cache_enabled = os.environ.get("ARCHI_HTTP_CACHE_ENABLED", "true").lower() == "true"
    if not cache_enabled:
        return requests.Session()

    expire_hours = int(os.environ.get("ARCHI_CACHE_EXPIRE_HOURS", "24"))

    pg_host = os.environ.get("PGHOST", "postgres")
    pg_port = os.environ.get("PGPORT", "5432")
    pg_db   = os.environ.get("PGDATABASE", "archi-db")
    pg_user = os.environ.get("PGUSER", "archi")
    pg_pass = read_secret("PG_PASSWORD")
    connection = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"

    try:
        session = requests_cache.CachedSession(
            cache_name="archi_http_cache",
            backend="postgresql",
            connection=connection,
            expire_after=timedelta(hours=expire_hours),
            allowable_methods=["GET", "HEAD"],
            stale_if_error=True,
            allowable_codes=[200, 301, 302],
        )
        return session
    except Exception as e:
        logger.warning(f"Failed to create cached session, falling back to plain: {e}")
        return requests.Session()
```

**Key design choices:**
- Uses `read_secret()` (from `src/utils/env.py`) — supports Docker secrets (`PG_PASSWORD_FILE`) and plain env vars, matching the existing pattern in `DataManager.__init__()` (line 28).
- `stale_if_error=True` — serves cached content when the source site is down.
- `allowable_codes=[200, 301, 302]` — caches successful responses and redirects only.
- Graceful fallback — if Postgres is unreachable during cache init, scraping still works.
- `ARCHI_HTTP_CACHE_ENABLED` toggle — can disable caching without code changes.

### Step 3: Patch session creation in crawl_iter()

**File:** `src/data_manager/collectors/scrapers/scraper.py`, lines 197–211

**Current code:**
```python
elif not selenium_scrape and browserclient is not None:
    session = requests.Session()                    # line 198
    cookies = browserclient.authenticate(normalized_start_url)
    if cookies is not None:
        for cookie_args in cookies:
            cookie = requests.cookies.create_cookie(...)
            session.cookies.set_cookie(cookie)

else:
    session = requests.Session()                    # line 211
```

**Change to:**
```python
elif not selenium_scrape and browserclient is not None:
    session = self._create_cached_session()         # line 198
    cookies = browserclient.authenticate(normalized_start_url)
    if cookies is not None:
        for cookie_args in cookies:
            cookie = requests.cookies.create_cookie(...)
            session.cookies.set_cookie(cookie)

else:
    session = self._create_cached_session()         # line 211
```

Two lines changed. The rest of `crawl_iter()` is untouched — `session.get()` at line 236 works identically with `CachedSession` since it inherits from `requests.Session`.

> **Cookie compatibility:** `requests_cache.CachedSession` inherits from `requests.Session`, so `session.cookies.set_cookie()` (lines 202–208) works unchanged. Cookies are part of the request, not the cache key by default, which is correct for SSO auth.

### Step 4: Cache persistence — already handled

The cache lives in PostgreSQL, which already has persistent storage via its Docker volume. The tables `archi_http_cache_responses` and `archi_http_cache_redirects` are created automatically on first use and survive container restarts.

**No extra volumes, no extra services, no extra config.**

### Step 5: Add environment variables to data-manager service

**File:** `src/cli/templates/base-compose.yaml`, in the `data-manager` service environment block:

```yaml
environment:
  # ... existing vars ...
  ARCHI_HTTP_CACHE_ENABLED: "{{ archi_http_cache_enabled | default('true') }}"
  ARCHI_CACHE_EXPIRE_HOURS: "{{ archi_cache_expire_hours | default('24') }}"
```

**File:** `src/cli/templates/base-config.yaml`, under `data_manager.sources.links`:

```yaml
links:
  cache:
    enabled: true
    expire_hours: 24    # dev: 24-168, prod: 1-4
```

### Step 6: Cache cleanup at ingestion start

Add expired entry pruning at the top of `DataManager.run_ingestion()` (`data_manager.py:60`):

```python
def run_ingestion(self, progress_callback=None):
    """Execute initial ingestion and vectorstore update."""

    # prune expired HTTP cache entries
    try:
        from src.data_manager.collectors.scrapers.scraper import LinkScraper
        cache_session = LinkScraper()._create_cached_session()
        if hasattr(cache_session, 'cache'):
            cache_session.cache.delete(expired=True)
            logger.info("Pruned expired HTTP cache entries")
        cache_session.close()
    except Exception as e:
        logger.debug(f"Cache cleanup skipped: {e}")

    source_aggregation = [
        # ... existing steps unchanged ...
    ]
```

### What This Gets You

On first run, everything behaves normally — full fetch, full ingestion. On every subsequent run within the TTL window:

- **HTML pages**: Served from Postgres cache instantly, no network request
- **PDFs**: Cached as binary blobs in Postgres, served without re-downloading
- **Markdown files**: Same as HTML, cached by URL
- **TWiki pages**: Cached, TWiki sees zero repeat traffic
- **HTTPS content**: Works transparently (caching happens at the Python layer, after TLS)
- **SSO-authenticated pages**: Cookies applied per-request; cache keyed by URL, not cookies

The library also automatically sends `If-Modified-Since` and `ETag` headers when revalidating expired entries, so servers that support conditional GET respond with `304 Not Modified` — tiny response, no content transfer.

### What requests-cache Does NOT Cover

| Source type | Why not cached | Mitigation |
|---|---|---|
| **Selenium-rendered pages** | `session` is `None` on the Selenium path (line 191) | These are dynamic/JS-rendered; caching HTML wouldn't help |
| **Git clones** | Uses `GitPython Repo.clone_from()`, not `requests` | See "Git Sources" section below |
| **JIRA API calls** | Uses `jira` library with its own HTTP client | JIRA already supports incremental fetch via `since_iso` |
| **Redmine API calls** | Uses `redminelib` with its own HTTP client | Needs `modified_since` support (see below) |

---

## Addressing the Broader "Don't Pull Same Data" Problem

Beyond HTTP caching, there are other data paths that re-fetch unnecessarily:

### Git Sources

The current `GitScraper._clone_repo()` (`git_scraper.py:279–292`) does a fresh `Repo.clone_from()` every time. Worse, after harvesting, `shutil.rmtree()` deletes the clone (`git_scraper.py:114`), so there's nothing to pull from next time.

**Fix:** Keep the clone and use `git fetch` + `git checkout` on subsequent runs.

```python
# In git_scraper.py, replace _clone_repo()
def _clone_repo(self, url_dict: dict) -> Path:
    clone_url = url_dict["clone_url"]
    branch = url_dict["branch"]
    repo_name = url_dict["repo_name"]
    repo_path = self.git_dir / repo_name

    if (repo_path / ".git").exists():
        logger.info(f"Updating existing clone of {repo_name}...")
        repo = Repo(repo_path)
        repo.remotes.origin.fetch()
        target = branch or repo.active_branch.name
        repo.git.checkout(target)
        repo.remotes.origin.pull()
    else:
        logger.info(f"Cloning repository {repo_name}...")
        if branch is None:
            Repo.clone_from(clone_url, repo_path)
        else:
            Repo.clone_from(clone_url, repo_path, branch=branch)

    return repo_path
```

**Also remove the `shutil.rmtree()` call** at line 114 (or make it configurable), so the clone persists for next run. The data volume already persists across restarts.

> **Trade-off:** Keeping clones uses disk space on the data volume. For most repos this is negligible. If disk is a concern, a configurable `keep_clones: true` flag in git config would work.

### JIRA Tickets — Already Incremental

The JIRA collector (`jira.py:142–144`) already supports incremental fetching:

```python
since_formatted = self._format_jira_datetime(since_iso, "since_iso")
if since_formatted:
    query_parts.append(f'updated >= "{since_formatted}"')
```

And the scheduler passes `since_iso=last_run` in `ticket_manager.py:82–96`. **No changes needed.**

### Redmine Tickets — Needs Incremental Support

Unlike JIRA, the Redmine collector (`redmine_tickets.py:171–175`) fetches **all closed issues** every run with no time filter:

```python
def _get_closed_issues(self, project) -> Any:
    return self.redmine.issue.filter(
        project_id=project.id,
        status_id="closed",
    )
```

**Fix:** Add `updated_on` filter:

```python
def _get_closed_issues(self, project, since_iso=None) -> Any:
    filters = {
        "project_id": project.id,
        "status_id": "closed",
    }
    if since_iso:
        filters["updated_on"] = f">={since_iso}"
    return self.redmine.issue.filter(**filters)
```

### Rate Limiting (Protect Source Sites)

There is **no rate limiting** anywhere in the web scraping pipeline. The scraper fires `session.get()` calls as fast as the network allows.

Add a simple per-request delay in `crawl_iter()` (`scraper.py`), before the request at line 236:

```python
import time

# before session.get() at line 236:
delay = float(os.environ.get("ARCHI_SCRAPE_DELAY_SECONDS", "0.5"))
if delay > 0:
    time.sleep(delay)
```

Or use config-driven rate limits per domain:

```yaml
data_manager:
  sources:
    links:
      rate_limit:
        default_delay_seconds: 1.0
        per_domain:
          twiki.example.com: 2.0     # extra gentle with TWiki
          docs.example.com: 0.5
```

---

## Summary: Implementation Priority

### HTTP Caching (choose one)

| Option | Effort | Impact | Covers | Limitation |
|---|---|---|---|---|
| **Option A: Squid proxy** | Config only (no code) | High — HTTP cache | HTTP sources | No HTTPS caching |
| **Option B: requests-cache** | ~20 lines | High — HTTP + HTTPS cache | All `requests.Session()` traffic | New pip dependency |

### Source-Specific Improvements (independent of caching choice)

| Change | Effort | Impact | Covers | Status |
|---|---|---|---|---|
| **Rate limiting delay** | ~5 lines | Medium — stops hammering immediately | All HTTP sources | Not implemented |
| **Git fetch instead of clone** | ~15 lines + remove rmtree | Medium — eliminates redundant clones | Git sources | Not implemented |
| **Redmine modified_since** | ~5 lines | Medium — only fetches updated tickets | Redmine tickets | Not implemented |
| ~~JIRA modified_since~~ | — | — | — | **Already implemented** |
