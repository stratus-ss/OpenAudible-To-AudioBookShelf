# Audiobook Ingestion MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes the Import-To-AudioBookShelf pipeline as tools for AI agents. It automates downloading audiobooks from Audible via [Libation](https://getlibation.com/), organizing them into an Author/Series/Title folder hierarchy, and ingesting them into [AudioBookShelf (ABS)](https://www.audiobookshelf.org/).

Supports **multiple libraries** (e.g. kids books, adult books, podcasts) via a YAML registry, and includes **podcast tools** for searching, subscribing, and downloading podcast episodes -- including a manual download fallback for podcasts without public RSS feeds.

Supports Streamable HTTP (default at `/mcp`, configurable via `MCP_STREAMABLE_PATH`), SSE, and stdio transports for remote and local use with Cursor, Claude Code, Moltis, and other MCP clients.

---

## Choose Your Path

There are two equivalent ways to run this MCP server. Pick the one that matches your environment:

| Path | Prerequisites | Setup time | Best for |
|------|---------------|------------|----------|
| **Container (Docker / Compose)** | Docker 20.10+, Docker Compose v2, your existing Libation config + books + ABS audiobooks directory on the host | ~10 min | New deploys, multiple libraries (kids/adult/podcasts), no host pollution, reproducible |
| **Python + venv + systemd** (bare-metal) | Python 3.12+, Libation installed natively (Arch AUR or GitHub release), NFS mounts configured | ~30 min | Existing systemd-managed host with Libation already installed, debugging Python directly |

**The container image bundles Libation + .NET runtime + Python + MCP server together** -- you don't need to install anything on the host beyond Docker itself. Both paths read the same `.env` and `libraries.yaml` files.

**For container:** skip Part 1 entirely and go to [Step 5c: Docker Deployment](#step-5c-docker-deployment-alternative) below. Then [Step 7](#step-7-connect-your-ai-client) for IDE integration. Steps 1-6 are for the bare-metal path.

**For bare-metal:** continue with Part 1 below.

---

## Part 1: Human Setup Guide

Everything you need to install, configure, and run the MCP server from scratch.

### What You Need Before Starting

This MCP server is a thin wrapper around the parent project's `openaudible_to_audiobookshelf` package. It does not duplicate logic -- it imports and calls those functions directly. The parent package must be installed in editable mode (`pip install -e .` from the repo root) before running the MCP server. You need three things running:

1. **Libation** -- an open-source Audible library manager that downloads and decrypts your audiobooks. The MCP server calls `libationcli` (Libation's CLI) to scan your Audible account, download books, and export metadata as JSON.

2. **AudioBookShelf (ABS)** -- a self-hosted audiobook server. The MCP server uses the ABS REST API to trigger library scans, match books to Audible metadata, update series information, and delete items.

3. **A shared filesystem path** -- the MCP server copies/moves audio files from Libation's download directory into ABS's audiobooks directory. If ABS runs on a different machine, this is typically an NFS mount. If both run on the same machine, it's just a local directory.

### Step 1: Install System Dependencies

```bash
# Libation CLI (Arch Linux via AUR)
yay -S libation

# Libation CLI (other distros -- download from GitHub releases)
# https://github.com/rmcrackan/Libation/releases
# Extract and place `libationcli` somewhere on your PATH

# Verify it works
libationcli --version
```

Configure Libation with your Audible account credentials. Run `libationcli scan` once to verify it can reach your Audible library.

> **Important:** Ensure Libation is configured for single-file output (not chapter-split). In `Settings.json`, set `"SplitFilesByChapter": false`. Chapter-split downloads produce many small files per book which the pipeline does not handle.

### Step 2: Clone the Repository and Create a Virtual Environment

```bash
git clone https://github.com/stratus-ss/Import-To-AudioBookShelf.git
cd Import-To-AudioBookShelf
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The `requirements.txt` installs: `requests`, `pyyaml`, `pydantic`, `mcp[cli]`, and `monkeyplug` (for optional profanity cleaning).

### Step 3: Set Up the Shared Filesystem

The MCP server needs write access to the directory that ABS reads audiobooks from.

**Same machine (ABS runs locally):**

```bash
# Just point DESTINATION_BOOK_DIRECTORY at your ABS audiobooks path
# e.g. /opt/audiobookshelf/audiobooks
```

**Different machines (ABS runs remotely):**

```bash
# On the machine running the MCP server, mount the ABS audiobooks directory via NFS
sudo mount -t nfs abs-server:/path/to/audiobookshelf/audiobooks /mnt/abs/audiobooks

# Add to /etc/fstab for persistence
# abs-server:/path/to/audiobookshelf/audiobooks /mnt/abs/audiobooks nfs defaults,_netdev 0 0
```

### Step 4: Get Your ABS API Token and Library ID

1. Log into your ABS web UI
2. Go to **Settings > Users > your user > API Token** and copy it
3. Your library ID is in the URL when you browse a library: `http://abs-host:13378/library/<library-id>/bookshelf`

### Step 5: Create the Environment File

```bash
cd abs_mcp
cp .env.example .env
```

Edit `.env` with your actual values:

```bash
# Where Libation downloads books to
SOURCE_AUDIO_BOOK_DIRECTORY=/home/youruser/Libation/Books

# Where ABS reads audiobooks from (local path or NFS mount)
DESTINATION_BOOK_DIRECTORY=/mnt/abs/audiobooks

# ABS connection
ABS_SERVER_URL=http://your-abs-host:13378
ABS_LIBRARY_ID=your-library-uuid
ABS_API_TOKEN=your-api-token
```

All other values have sensible defaults. See the full [Environment Variables](#environment-variables) reference below.

### Step 5b: Configure Multiple Libraries (Optional)

If you have multiple ABS libraries (e.g. kids books, adult books, podcasts), create a libraries config:

```bash
cp libraries.example.yaml libraries.yaml
```

Edit `libraries.yaml` with your library UUIDs and destination paths:

```yaml
default_library: kids

libraries:
  kids:
    library_id: "your-kids-library-uuid"
    destination_dir: "/path/to/kids/audiobooks"
    media_type: book
  adult:
    library_id: "your-adult-library-uuid"
    destination_dir: "/path/to/adult/audiobooks"
    media_type: book
  adult_podcasts:
    library_id: "your-podcast-library-uuid"
    destination_dir: "/path/to/adult/podcasts"
    media_type: podcast
```

Add to `.env`:

```bash
LIBRARIES_CONFIG=abs_mcp/libraries.yaml
```

Tools then accept a `library` parameter (e.g. `library="kids"`) instead of raw UUIDs.

### Step 5c: Docker Deployment (Alternative)

Instead of running on bare metal with systemd, you can run the MCP server as a container. The image bundles Libation (CLI binary, .NET runtime, account decryption) and the MCP server together — no need to install Libation on the host.

**Pull the image:**

```bash
docker pull ghcr.io/stratus-ss/mcps/audiobook-ingestion-mcp:latest
```

**Required volume mounts:**

1. **Libation config** — your existing `AccountsSettings.json`, `Settings.json`, `LibationContext.db` directory.
2. **Libation books source** — your existing downloaded audiobooks directory.
3. **Per-library destination** — one mount per `destination_dir` entry in your `libraries.yaml`. The MCP server resolves `destination_dir` per library at tool-call time, so these paths must match what's in your `libraries.yaml`. Typical setups mount an NFS share that ABS also reads.
4. **`libraries.yaml` (read-only)** — your multi-library config from the host.
5. **`.env` (read-only)** — your ABS API token and connection settings from the host.

> **Important:** The image contains **no credentials, library UUIDs, or environment files**. You MUST mount your own `.env` and `libraries.yaml` from the host. The image ships `.env.example` and `libraries.example.yaml` as templates — `cp` them outside the container to create your editable copies.

**NFS note:** If your ABS audiobooks directory is on a remote server, establish the NFS mount on the host **before** starting the container. Mounts inside the container must resolve to paths the host already has mounted.

**Run with `docker run`:**

```bash
# Run from the REPO ROOT (so relative paths to ./abs_mcp/ resolve correctly)
cd /path/to/Import-To-AudioBookShelf
docker run -d --name audiobook-ingestion-mcp \
  --restart unless-stopped \
  -p 8765:8765 \
  -e LIBATION_CLI=/libation/LibationCli \
  -e LIBATION_FILES_DIR=/config \
  -e MCP_ENV_FILE=/app/abs_mcp/.env \
  -v /path/to/libation/config:/config \
  -v /path/to/libation/books:/data \
  -v /path/to/kids/audiobooks:/path/to/kids/audiobooks \
  -v /path/to/adult/audiobooks:/path/to/adult/audiobooks \
  -v ./abs_mcp/libraries.yaml:/app/abs_mcp/libraries.yaml:ro \
  -v ./abs_mcp/.env:/app/abs_mcp/.env:ro \
  ghcr.io/stratus-ss/mcps/audiobook-ingestion-mcp:latest
```

Add one `-v /host/path:/container/path` line per library `destination_dir` in your `libraries.yaml`. Podcast libraries with `media_type: podcast` also need their destination directory mounted.

**Run with `docker-compose` (recommended):**

All container artifacts live in the `docker/` directory: `Dockerfile`, `docker-compose.yml`, `docker-compose.override.example.yml`, and `scripts/regen-egg-info.sh`. Run from the REPO ROOT:

```bash
cd /path/to/Import-To-AudioBookShelf
# Quick start (uses placeholder paths — won't work until you create an override):
docker compose -f docker/docker-compose.yml up -d

# Real deploy (recommended): copy and edit the override, then run with both files
cp docker/docker-compose.override.example.yml docker/docker-compose.override.yml
# Edit docker/docker-compose.override.yml to use your real host paths
docker compose -f docker/docker-compose.yml -f docker/docker-compose.override.yml up -d
```

The base `docker/docker-compose.yml` is committed with placeholders; `docker/docker-compose.override.yml` is gitignored so your real paths stay local.

**Files referenced (all under `docker/`):**

- `docker/Dockerfile` — multi-stage image build definition
- `docker/.dockerignore` — excludes secrets and build artifacts from the build context
- `docker/docker-compose.yml` — placeholder mount pattern, committed to git
- `docker/docker-compose.override.example.yml` — real-path override template
- `docker/scripts/regen-egg-info.sh` — runtime helper for the local package's dist-info

### Step 6: Start the Server (Bare Metal)

**Streamable HTTP mode (remote/network access -- default):**

```bash
cd /path/to/Import-To-AudioBookShelf
source venv/bin/activate
MCP_ENV_FILE=abs_mcp/.env venv/bin/python abs_mcp/mcp_server.py
# Listening on http://0.0.0.0:8765
```

**stdio mode (local, for Cursor/Claude Code):**

```bash
# Typically started automatically by Cursor or Claude Code via their MCP config
# See the IDE Integration sections below
```

### Step 7: Connect Your AI Client

Pick the setup that matches your environment.

#### Cursor (stdio -- local)

The server runs on the same machine as Cursor. Add to `~/.cursor/mcp.json` under `"mcpServers"`:

> **Important:** Use absolute paths. Cursor does not reliably resolve relative paths via `cwd`.

```json
{
  "audiobook-ingestion": {
    "command": "/path/to/Import-To-AudioBookShelf/venv/bin/python",
    "args": ["/path/to/Import-To-AudioBookShelf/abs_mcp/mcp_server.py"],
    "env": {
      "MCP_TRANSPORT": "stdio",
      "MCP_ENV_FILE": "/path/to/Import-To-AudioBookShelf/abs_mcp/.env"
    }
  }
}
```

#### Cursor (SSE -- remote)

The server runs on a different machine (the one with Libation and the NFS mount). Start the server on that host per Step 6, then add to `~/.cursor/mcp.json`:

```json
{
  "audiobook-ingestion": {
    "url": "http://your-abs-host:8765/sse"
  }
}
```

#### Claude Code (stdio -- local)

Add to `~/.claude/settings.json` (or project `.mcp.json`):

```json
{
  "mcpServers": {
    "audiobook-ingestion": {
      "command": "/path/to/Import-To-AudioBookShelf/venv/bin/python",
      "args": ["/path/to/Import-To-AudioBookShelf/abs_mcp/mcp_server.py"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "MCP_ENV_FILE": "/path/to/Import-To-AudioBookShelf/abs_mcp/.env"
      }
    }
  }
}
```

#### Claude Code (SSE -- remote)

```json
{
  "mcpServers": {
    "audiobook-ingestion": {
      "url": "http://your-abs-host:8765/sse"
    }
  }
}
```

#### Moltis (Streamable HTTP)

Add to your Moltis config (see `moltis-mcp-config.toml`):

```toml
[mcp.servers.audiobook_ingestion]
transport = "streamable-http"
url = "http://your-abs-host:8765/sse"
```

> **Note:** The URL path is `/sse` when the server is configured with `MCP_STREAMABLE_PATH=/sse` for broad client compatibility. If your Moltis version only recognizes `transport = "sse"`, use that instead -- Moltis 0.1.x sends Streamable HTTP requests under the `"sse"` transport label.

#### systemd (persistent service)

The included `audiobook-ingestion-mcp.service` starts the MCP server in Streamable HTTP mode on boot. It waits for networking and the NFS mount before starting, and restarts automatically on failure.

The service file ships with paths for the `open-audible` host. If your paths differ, edit the `WorkingDirectory`, `Environment`, and `ExecStart` lines before installing.

```bash
# Install and enable
sudo cp abs_mcp/audiobook-ingestion-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now audiobook-ingestion-mcp

# Check status
sudo systemctl status audiobook-ingestion-mcp

# View logs
journalctl -u audiobook-ingestion-mcp -f
```

> **Note:** The MCP server must run on the host where `libationcli` is installed and the ABS audiobooks directory is writable (directly or via NFS). For remote setups, start the server on that host in Streamable HTTP mode and connect from your AI client via URL.

#### Docker (persistent container)

If you deployed the container per Step 5c, connect your AI client to the containerized server the same way you'd connect to a bare-metal Streamable HTTP server — point at the host where the container's port 8765 is published:

```json
{
  "mcpServers": {
    "audiobook-ingestion": {
      "url": "http://your-host:8765/mcp"
    }
  }
}
```

The container listens on `/mcp` (default Streamable HTTP path) regardless of host platform. Mounts and environment come from `docker/docker-compose.yml` / `docker/docker-compose.override.yml` — no client-side env vars needed.

### Testing

#### Test Script

`test_mcp.sh` exercises every tool with default `.env` values **and** per-call overrides.

```bash
bash abs_mcp/test_mcp.sh                        # full test against .env.test
bash abs_mcp/test_mcp.sh --skip-download         # skip Libation download step
bash abs_mcp/test_mcp.sh --env abs_mcp/.env      # use production env
```

#### Test Environment

`.env.test` is pre-configured for a test ABS instance. Adjust the values for your environment:

- **ABS host**: `your-abs-host:13378`
- **NFS mount**: `your-nfs-host:/path/to/audiobookshelf/audiobooks` -> `/mnt/abs-test/audiobooks`

```bash
MCP_ENV_FILE=abs_mcp/.env.test venv/bin/python abs_mcp/mcp_server.py
```

---

## Part 2: LLM Reference

Structured reference for AI agents consuming this MCP server. All tool parameters are optional and fall back to the `.env` default when omitted. Any value can be overridden per-call without restarting the server.

### Architecture

```
Audible Cloud
     |
     v
 libationcli  (scan / liberate / export)
     |
     v
 Libation/Books/          <-- SOURCE_AUDIO_BOOK_DIRECTORY
   ├── Book Title [ASIN]/
   │     └── Book Title: Subtitle [ASIN].m4b
   └── libation.json      <-- generated by export
     |
     v
 process_books            (organize into Author/Series/Title hierarchy)
     |
     v
 /mnt/abs/audiobooks/     <-- DESTINATION_BOOK_DIRECTORY (NFS mount to ABS)
   └── Author_Name/
       └── Series_Name/
           └── Book_Title/
               └── file.m4b
     |
     v
 scan_audiobookshelf      (ABS HTTP API: POST /api/libraries/{id}/scan)
     |
     v
 match_audiobookshelf     (ABS HTTP API: POST /api/items/{id}/match)
     |
     v
 update_book_series       (ABS HTTP API: PATCH /api/items/{id}/media)
```

### Pipeline Flow

The pipeline has six discrete steps. Each step has a dedicated MCP tool and can also be invoked from the CLI via `--step`. LLM agents should call each step tool individually for reliability; the `ingest_books` tool runs all steps sequentially but can timeout on long operations.

1. **scan_audible** -- `libationcli scan` -- refresh Audible library metadata
2. **download_books** -- `libationcli liberate [ASINs]` -- download/decrypt audiobooks
3. **export_library** -- `libationcli export` -- regenerate `libation.json`
4. **organize_books** -- copy/move audio files into `Author/Series/Title` folders
5. **scan_audiobookshelf** -- ABS library scan -- makes ABS discover the new files
6. **match_audiobookshelf** -- ABS match -- matches each new item to Audible metadata

Additional discovery and management tools:

- **list_library** -- parse full Libation export and return full filtered matches by default
- **list_abs_library** -- parse full cached AudioBookShelf JSON and return full filtered matches by default
- **search_abs_library** -- quick direct ABS text search for ad-hoc lookups
- **get_source_status** -- inspect source/destination directories (summary by default, optional detail)
- **set_book_status** -- mark books as unliberated to force re-download
- **delete_library_items** -- remove ABS items with optional disk file cleanup
- **get_tool_metrics** -- recent response-byte and rough-token metrics (last 50 in-memory)
- **query_tool_metrics_history** -- persisted JSONL metrics query with tool/time filters

### Environment Variables

#### Source Paths

| Variable | Description | Default |
|---|---|---|
| `SOURCE_AUDIO_BOOK_DIRECTORY` | Libation books directory | (required) |
| `DESTINATION_BOOK_DIRECTORY` | ABS audiobooks directory (NFS mount) | (required) |
| `LIBATION_CLI` | Path to libationcli binary | `libationcli` |
| `LIBATION_FILE_LOCATIONS_PATH` | Path to Libation's `FileLocationsV2.json` | (empty) |
| `AUDIO_FILE_EXTENSION` | Audio file extension | `.m4b` |
| `COPY_INSTEAD_OF_MOVE` | Copy files instead of moving | `false` |
| `LIBATION_FOLDER_CLEANUP` | Delete Libation source folders after move | `false` |

#### AudioBookShelf Connection

| Variable | Description | Default |
|---|---|---|
| `ABS_SERVER_URL` | ABS server URL (e.g. `http://abs-host:13378`) | (required) |
| `ABS_LIBRARY_ID` | ABS library UUID | (required) |
| `ABS_API_TOKEN` | ABS API bearer token | (required) |

#### Profanity Cleaning (MonkeyPlug)

| Variable | Description | Default |
|---|---|---|
| `ENABLE_PROFANITY_CLEANING` | Enable audio profanity filtering | `false` |
| `REMOTE_WHISPER_URL` | Whisper-WebUI transcription server URL | (empty) |
| `SWEARS_FILE` | Custom swears list file (JSON or text) | (empty = MonkeyPlug default) |
| `WORKING_DIRECTORY` | Temp directory for processing | `/tmp/monkeyplug-cleaning` |
| `SAVE_TRANSCRIPTS` | Keep transcript JSON files | `true` |
| `TIMEOUT` | Transcription timeout in seconds | `600` |
| `CONFIDENCE_THRESHOLD` | Censoring confidence threshold (0.0-1.0) | `0.70` |
| `BEEP_MODE` | Beep over profanity instead of muting | `false` |

#### Multi-Library

| Variable | Description | Default |
|---|---|---|
| `LIBRARIES_CONFIG` | Path to YAML library registry | `abs_mcp/libraries.yaml` |

#### MCP Transport

| Variable | Description | Default |
|---|---|---|
| `MCP_TRANSPORT` | Transport protocol (`streamable-http`, `sse`, or `stdio`) | `streamable-http` |
| `MCP_HOST` | Listen address | `0.0.0.0` |
| `MCP_PORT` | Listen port | `8765` |
| `MCP_STREAMABLE_PATH` | URL path for the Streamable HTTP endpoint | `/mcp` |
| `TOOL_METRICS_PATH` | JSONL file path for persisted tool metrics | `abs_mcp/data/tool-metrics.jsonl` |

### Available Tools

#### Discovery & Status

##### list_libraries

List all configured libraries with their names, types, and IDs. Use this first to discover what libraries are available.

No parameters.

##### get_status

Pipeline status and ABS connectivity check. Returns source/destination paths, ABS server version, and connection health.

| Parameter | Type | Description |
|---|---|---|
| `library` | str | Library name from libraries.yaml |
| `source_dir` | str | Libation books directory |
| `destination_dir` | str | ABS audiobooks directory / NFS mount |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

##### list_library

List books in the Audible library from Libation's JSON export with optional filtering and parser-based matching. Returns ASIN, title, subtitle, author, series, duration, status, and date added.

| Parameter | Type | Description |
|---|---|---|
| `source_dir` | str | Libation books directory |
| `status` | str | Filter by BookStatus (e.g. `NotLiberated`, `Liberated`) |
| `author` | str | Filter by author name (case-insensitive substring) |
| `series` | str | Filter by series name (case-insensitive substring) |
| `title` | str | Filter by title (case-insensitive substring) |
| `max_duration` | int | Only books shorter than this many minutes |
| `min_duration` | int | Only books longer than this many minutes |
| `limit` | int | Max results to return (`0` = all matches) |
| `offset` | int | Number of filtered results to skip (default 0) |
| `sort_by` | str | Sort field: `duration`, `title`, `author`, `date_added` |

##### list_abs_library

List items in an AudioBookShelf library using server-side cache and parser logic. The tool fetches/parses the full dataset server-side, then returns results from that parsed dataset. Use `limit=0` to return all matches in one call.

| Parameter | Type | Description |
|---|---|---|
| `library` | str | Library name from libraries.yaml |
| `query` | str | Substring search across title/author/series |
| `title` | str | Title substring filter |
| `author` | str | Author substring filter |
| `series` | str | Series substring filter |
| `limit` | int | Max results to return (`0` = all matches) |
| `offset` | int | Number of filtered results to skip (default 0) |
| `sort_by` | str | Sort field: `title`, `author`, `series`, `duration`, `added_at` |
| `refresh` | bool | Force cache refresh from ABS API |
| `cache_max_age_seconds` | int | Auto-refresh cache age threshold (default 3600) |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

##### search_abs_library

Search an ABS library directly via `/api/libraries/{id}/search` and return compact parsed results.

| Parameter | Type | Description |
|---|---|---|
| `query` | str | **(required)** Search text |
| `library` | str | Library name from libraries.yaml |
| `limit` | int | Max results to return (`0` = all matches) |
| `offset` | int | Number of results to skip (default 0) |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

##### get_source_status

Inspect the Libation source and ABS destination directories. Returns compact summaries by default to avoid large payloads. Set `detail=true` only when you need per-folder destination details.

| Parameter | Type | Description |
|---|---|---|
| `source_dir` | str | Libation books directory |
| `destination_dir` | str | ABS audiobooks directory |
| `library` | str | Library name to resolve destination_dir |
| `detail` | bool | Include per-folder destination details (default false) |

##### get_tool_metrics

Return the recent in-memory response efficiency window for tool calls. Tracks response payload only.

| Parameter | Type | Description |
|---|---|---|
| `limit` | int | Number of recent records to return (default 10, max 50) |

##### query_tool_metrics_history

Query persisted JSONL metrics history with optional tool name and timestamp filters.

| Parameter | Type | Description |
|---|---|---|
| `tool_name` | str | Filter to a specific tool name |
| `since` | str | ISO lower bound timestamp |
| `until` | str | ISO upper bound timestamp |
| `limit` | int | Max records to return (default 50, max 500) |
| `offset` | int | Number of matching records to skip |

#### Ingestion Pipeline

##### scan_audible

Refresh the Audible library list via Libation (~10 seconds). Run before `download_books` to ensure the listing is current.

| Parameter | Type | Description |
|---|---|---|
| `libation_cli` | str | Path to libationcli binary |

##### set_book_status

Set the download status of books in Libation's database. Use `status="not-downloaded"` to mark books as unliberated so they can be re-downloaded with `download_books`.

| Parameter | Type | Description |
|---|---|---|
| `asins` | list[str] | **(required)** Product IDs (ASINs) of books to update |
| `status` | str | `not-downloaded` or `downloaded` (default: `not-downloaded`) |
| `force` | bool | Set status even if the audio file exists on disk (default: true) |
| `libation_cli` | str | Path to libationcli binary |

##### download_books

Download audiobooks from Audible via `libationcli liberate`. Pass specific ASINs or omit to download all un-downloaded books.

| Parameter | Type | Description |
|---|---|---|
| `asins` | list[str] | ASINs to download (empty = all new) |
| `libation_cli` | str | Path to libationcli binary |

##### export_library

Export Libation library metadata to `libation.json`. Run after `download_books` so the JSON reflects newly downloaded titles.

| Parameter | Type | Description |
|---|---|---|
| `source_dir` | str | Libation books directory |
| `libation_cli` | str | Path to libationcli binary |

##### organize_books

Organize downloaded audiobooks into `Author/Series/Title` hierarchy on the destination filesystem. Reads `libation.json`, filters by purchase date, copies/moves audio files.

Default audio format is `.m4b`. If no `.m4b` files are found in the source directory, the tool auto-detects the actual extension present (e.g. `.mp3`). The detected extension is included in the response. Pass `audio_file_extension` explicitly only if you specifically need a non-default format.

**Async job pattern (DR-6):** This tool returns immediately (in ~4ms) with `{"job_id": str, "status": "started"}`. The actual work runs in a background thread. Use `get_job_result(job_id)` to poll for the final response (which lives in the `result` field). Use `get_cleaning_progress()` for per-book/per-stage progress during long runs (free, 2-5ms each, safe to call concurrently). Single-slot guard: if a job is already running, this tool returns `{"error": "Job already running", "active_job_id": "..."}`. See `get_job_result` for the unknown-job-after-restart behavior.

| Parameter | Type | Description |
|---|---|---|
| `purchased_how_long_ago` | int | Days filter (0 = all) |
| `library` | str | Library name (e.g. 'kids', 'adult') |
| `source_dir` | str | Libation books directory |
| `destination_dir` | str | ABS audiobooks directory / NFS mount |
| `audio_file_extension` | str | Override extension (default: `.m4b`, auto-detects if not found) |
| `copy_instead_of_move` | bool | Copy files instead of moving |
| `libation_folder_cleanup` | bool | Delete Libation source folders after move |
| `libation_file_locations_path` | str | Path to Libation FileLocationsV2.json |
| `enable_profanity_cleaning` | bool | Toggle monkeyplug filtering. Failed books are skipped (absent from `moved`) and reported in `cleaning_failures[]`. Requires `REMOTE_WHISPER_URL` env var. Measured: ~8 min per hour of audio against production Whisper backend. |

##### get_cleaning_progress

Poll mid-operation for per-book profanity cleaning progress. Returns JSON with per-ASIN state (processing/transcribing/done/failed/skipped) plus per-book `stage` (staging|uploading|transcribing|done|failed|skipped) and aggregate `books_done` / `books_total` counters. Safe to call concurrently with `organize_books`/`ingest_books` — returns in 2-5ms.

##### get_job_result

Poll for the final result of an async `organize_books` or `ingest_books` call.

| Path | Return |
|------|--------|
| Job running | `{"status": "running", "job_id": str}` |
| Job complete | `{"status": "completed", "job_id": str, "result": dict}` — `result` matches the previous synchronous response schema (moved[], cleaning_failures[], cleaning{...}) |
| Unknown job | `{"error": "Unknown job", "job_id": str}` — server restarted mid-job; re-invoke the originating tool |
| Concurrent job | `organize_books` returns `{"error": "Job already running", "active_job_id": "..."}` instead of starting a new one |

##### scan_audiobookshelf

Trigger an ABS library scan and wait for it to settle. Run after `organize_books` so ABS discovers the new files.

| Parameter | Type | Description |
|---|---|---|
| `library` | str | Library name (e.g. 'kids', 'adult_podcasts') |
| `wait` | int | Seconds to wait after triggering scan (default: 15) |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

##### match_audiobookshelf

Match recently added ABS items to Audible metadata (cover art, description, narrator, series).

| Parameter | Type | Description |
|---|---|---|
| `days_ago` | int | Match items added within this many days (default: 7) |
| `library` | str | Library name (e.g. 'kids', 'adult') |
| `book_list` | list[dict] | Specific books to match (output from organize_books) |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

##### ingest_books

End-to-end pipeline: scan -> download -> export -> organize -> scan ABS -> match. Runs all six steps sequentially. **WARNING:** Can take 30+ minutes and may timeout. LLM agents should prefer calling individual step tools.

| Parameter | Type | Description |
|---|---|---|
| `asins` | list[str] | ASINs to download (empty = all new) |
| `purchased_how_long_ago` | int | Days filter (0 = all) |
| `library` | str | Library name (e.g. 'kids', 'adult') |
| `source_dir` | str | Libation books directory |
| `destination_dir` | str | ABS audiobooks directory / NFS mount |
| `audio_file_extension` | str | e.g. `.m4b`, `.mp3` |
| `copy_instead_of_move` | bool | Copy files instead of moving |
| `libation_folder_cleanup` | bool | Delete Libation source folders after move |
| `libation_file_locations_path` | str | Path to Libation FileLocationsV2.json |
| `libation_cli` | str | Path to libationcli binary |
| `enable_profanity_cleaning` | bool | Toggle monkeyplug filtering |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

#### Library Management

##### delete_library_items

Delete items from the ABS library by ID or purge everything. Optionally removes audio files from both the destination and source directories.

| Parameter | Type | Description |
|---|---|---|
| `item_ids` | list[str] | Specific ABS item IDs to delete |
| `delete_all` | bool | If true, delete every item in the library |
| `cleanup_files` | bool | Also remove audio files from destination and source directories |
| `library` | str | Library name (e.g. 'kids', 'adult') |
| `source_dir` | str | Libation source directory to clean |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

#### search_podcasts

Search for podcasts via iTunes (through the ABS API).

| Parameter | Type | Description |
|---|---|---|
| `term` | str | Search term (e.g. 'Under The Hood', 'Jupiter Broadcasting') |
| `abs_server_url` | str | ABS server URL |
| `abs_api_token` | str | ABS API bearer token |

#### add_podcast

Add a podcast to an ABS podcast library by RSS feed URL.

| Parameter | Type | Description |
|---|---|---|
| `feed_url` | str | The podcast RSS feed URL |
| `library` | str | Podcast library name (e.g. 'adult_podcasts') |
| `title` | str | Podcast title (auto-detected from feed if omitted) |
| `abs_server_url` | str | ABS server URL |
| `abs_api_token` | str | ABS API bearer token |

#### list_podcasts

List all podcasts in a podcast library.

| Parameter | Type | Description |
|---|---|---|
| `library` | str | Podcast library name (e.g. 'adult_podcasts') |
| `abs_server_url` | str | ABS server URL |
| `abs_api_token` | str | ABS API bearer token |

#### get_podcast_episodes

Get episodes for a specific podcast in ABS.

| Parameter | Type | Description |
|---|---|---|
| `podcast_id` | str | The ABS library item ID for the podcast |
| `abs_server_url` | str | ABS server URL |
| `abs_api_token` | str | ABS API bearer token |

#### download_podcast_episodes

Check for and download new episodes for a podcast via ABS.

| Parameter | Type | Description |
|---|---|---|
| `podcast_id` | str | The ABS library item ID for the podcast |
| `limit` | int | Max new episodes to download (0 = all, default 3) |
| `abs_server_url` | str | ABS server URL |
| `abs_api_token` | str | ABS API bearer token |

#### fetch_podcast_feed

Find and parse a podcast RSS feed, returning episode download URLs. Provide ONE of: search_term, apple_url, or feed_url. Uses iTunes Lookup API and feedparser.

| Parameter | Type | Description |
|---|---|---|
| `search_term` | str | Podcast name to search iTunes for |
| `apple_url` | str | Apple Podcasts URL (e.g. `https://podcasts.apple.com/.../id410937196`) |
| `feed_url` | str | Direct RSS feed URL |
| `max_episodes` | int | Max episodes to return (default 20) |

#### download_podcast_files

Download audio files from URLs into an ABS podcast directory. Use when ABS cannot subscribe to a podcast natively, or when the LLM has extracted download URLs via browser tools.

| Parameter | Type | Description |
|---|---|---|
| `urls` | list[str] | Audio file download URLs |
| `podcast_name` | str | Podcast name (used for folder name) |
| `library` | str | Podcast library name (e.g. 'adult_podcasts') |
| `episode_names` | list[str] | Optional display names for files |
| `trigger_scan` | bool | Trigger ABS library scan after download (default true) |
| `abs_server_url` | str | ABS server URL |
| `abs_api_token` | str | ABS API bearer token |

### Common Workflows

**Step-by-step ingestion (recommended for LLM agents):**

```
list_libraries()                                              # discover libraries
list_library(status="NotLiberated", limit=10, sort_by="duration")  # find books to download
scan_audible()                                                # refresh Audible library
download_books(asins=["B0C24R5GP1", "B09H7QBMGX"])           # download specific books
get_source_status(library="kids")                             # verify downloads, check extension
export_library()                                              # refresh libation.json
organize_books(library="kids", purchased_how_long_ago=0)      # auto-detects .m4b or .mp3
scan_audiobookshelf(library="kids")                           # trigger ABS scan
match_audiobookshelf(library="kids")                          # match to Audible metadata
list_abs_library(library="kids", author="Estrella")           # compact filtered view
search_abs_library(query="Ten Big Ones", library="kids")      # direct ABS lookup
```

**Force re-download a book:**

```
set_book_status(asins=["B0C24R5GP1"], status="not-downloaded")
download_books(asins=["B0C24R5GP1"])
```

**Quick ingest (end-to-end, may timeout for large libraries):**

```
ingest_books(library="kids")
ingest_books(asins=["B0C24R5GP1"], library="adult")
```

**Subscribe to a podcast via ABS (Layer 1):**

```
search_podcasts(term="Jupiter Broadcasting")
add_podcast(feed_url="https://feed.example.com/rss", library="adult_podcasts")
download_podcast_episodes(podcast_id="li_...", limit=3)
```

**Download podcast episodes manually (Layer 2 -- RSS fallback):**

```
fetch_podcast_feed(apple_url="https://podcasts.apple.com/us/podcast/under-the-hood-show/id410937196")
download_podcast_files(urls=["https://...mp3"], podcast_name="Under The Hood show", library="adult_podcasts")
```

**Download podcast with browser-discovered URLs (Layer 3):**

```
# LLM uses browser tools to find URLs, then:
download_podcast_files(urls=["https://...mp3"], podcast_name="My Podcast", library="adult_podcasts")
```

**Clean up after testing (with disk file removal):**

```
delete_library_items(delete_all=true, cleanup_files=true, library="kids")
get_source_status(library="kids")  # verify clean
```

**Inspect token efficiency of recent calls:**

```
get_tool_metrics(limit=20)
query_tool_metrics_history(tool_name="list_library", since="2026-04-01T00:00:00Z", limit=100)
```

### Known Behaviors

- **Audio format auto-detection:** The default audio format is `.m4b`. Some Audible content (e.g. short episodes, podcasts) downloads as `.mp3`. The `organize_books` tool auto-detects the actual extension in the source directory when `.m4b` files aren't found, so you don't need to manually specify it. The detected extension is returned in the response. Use `get_source_status` to inspect file types before organizing.
- **Filesystem sanitization:** Author and series folder names replace spaces with underscores (e.g. `Ben's Damn Adventure` becomes `Bens_Damn_Adventure`). The pipeline corrects this by explicitly patching series metadata via the ABS API after matching.
- **Title matching:** ABS may parse titles differently than the original metadata (e.g. appending "Unabridged"). The pipeline uses `short_title` (the main title without subtitle) for matching to handle this.
- **ABS item persistence — both directions matter:** Deleting files from disk and rescanning does **not** remove items from the ABS database. The reverse is also true: calling `delete_library_items` with `cleanup_files=false` (the default) only removes the ABS DB row, and ABS's file-system watcher (`disableWatcher=false` on both libraries) auto-rescans and re-imports the orphaned `.m4b` under a new UUID within ~5 seconds — the tool still returns `success=true`, so the resurrection is invisible at the MCP boundary. Always pass `cleanup_files=true` (or pre-`rm` the destination folder) when removing books from ABS.
- **Re-downloading books:** Libation tracks which books have been downloaded. To force a re-download, use `set_book_status(asins=[...], status="not-downloaded")` before calling `download_books`.
- **Libation chapter splitting:** If Libation is configured with `SplitFilesByChapter: true`, it produces many small `.m4b` files per book instead of one. The pipeline expects single-file output. Ensure `SplitFilesByChapter` is `false` in Libation's `Settings.json`.
- **Series data enrichment:** The ABS `/api/libraries/{id}/items` endpoint returns minified data that omits series metadata. During cache refresh, the server fetches series data from the `/api/libraries/{id}/series` endpoint and injects it into each item. This means series filters work correctly in `list_abs_library`.
- **Series filter title fallback:** The `series` filter in both `list_abs_library` and `list_library` also matches against the title field as a safety net for items that lack explicit series metadata.
- **Parser-first lookup behavior:** `list_library`, `list_abs_library`, and `search_abs_library` parse full datasets server-side. With `limit=0`, they return all matches in one call (no page-walking).
- **Compact JSON responses:** Tool responses are emitted as compact JSON (no pretty-print indentation) to reduce token usage.
- **Rough token estimate formula:** Metrics use `ceil(response_bytes / 4)` as a lightweight approximation for JSON/English payload token usage.
- **Metrics retention:** Recent metrics keep only the last 50 calls in memory; persisted JSONL auto-rotates when it grows past 2000 lines (keeps most recent 1000).

### rough_tokens Field Explanation

The `rough_tokens` field in tool metrics is an **approximate token count estimate** for the LLM response associated with a tool call. This is not an exact count -- it's derived from the response size in bytes and a rough characters-per-token ratio (~4 chars per token for typical English text). Use this field to gauge relative cost and response length across different tool calls, not for precise billing or audit purposes.

**Example response:**
```json
{
  "tool_name": "list_library",
  "response_bytes": 1234,
  "rough_tokens": 285,
  "duration_ms": 450
}
```
