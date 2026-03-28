# Audiobook Ingestion MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes the OpenAudible-To-AudioBookShelf pipeline as tools for AI agents. It automates downloading audiobooks from Audible via [Libation](https://getlibation.com/), organizing them into an Author/Series/Title folder hierarchy, and ingesting them into [AudioBookShelf (ABS)](https://www.audiobookshelf.org/).

Supports **multiple libraries** (e.g. kids books, adult books, podcasts) via a YAML registry, and includes **podcast tools** for searching, subscribing, and downloading podcast episodes -- including a manual download fallback for podcasts without public RSS feeds.

Supports SSE transport (for remote/network use with Moltis, Cursor, Claude Code) and stdio transport (for local use with Cursor, Claude Code).

---

## Part 1: Human Setup Guide

Everything you need to install, configure, and run the MCP server from scratch.

### What You Need Before Starting

This MCP server is a thin wrapper around the parent project's pipeline (`openaudible_to_ab.py` and `modules/`). It does not duplicate logic -- it imports and calls those functions directly. You need three things running:

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
git clone https://github.com/stratus-ss/OpenAudible-To-AudioBookShelf.git
cd OpenAudible-To-AudioBookShelf
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
cd abs-mcp
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
LIBRARIES_CONFIG=abs-mcp/libraries.yaml
```

Tools then accept a `library` parameter (e.g. `library="kids"`) instead of raw UUIDs.

### Step 6: Start the Server

**SSE mode (remote/network access -- default):**

```bash
cd /path/to/OpenAudible-To-AudioBookShelf
source venv/bin/activate
MCP_ENV_FILE=abs-mcp/.env venv/bin/python abs-mcp/mcp_server.py
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
    "command": "/path/to/OpenAudible-To-AudioBookShelf/venv/bin/python",
    "args": ["/path/to/OpenAudible-To-AudioBookShelf/abs-mcp/mcp_server.py"],
    "env": {
      "MCP_TRANSPORT": "stdio",
      "MCP_ENV_FILE": "/path/to/OpenAudible-To-AudioBookShelf/abs-mcp/.env"
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
      "command": "/path/to/OpenAudible-To-AudioBookShelf/venv/bin/python",
      "args": ["/path/to/OpenAudible-To-AudioBookShelf/abs-mcp/mcp_server.py"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "MCP_ENV_FILE": "/path/to/OpenAudible-To-AudioBookShelf/abs-mcp/.env"
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

#### Moltis (SSE)

Add to your Moltis config (see `moltis-mcp-config.toml`):

```toml
[mcp.servers.audiobook_ingestion]
transport = "sse"
url = "http://your-abs-host:8765/sse"
```

#### systemd (persistent service)

The included `audiobook-ingestion-mcp.service` starts the MCP server in SSE mode on boot. It waits for networking and the NFS mount before starting, and restarts automatically on failure.

The service file ships with paths for the `open-audible` host. If your paths differ, edit the `WorkingDirectory`, `Environment`, and `ExecStart` lines before installing.

```bash
# Install and enable
sudo cp abs-mcp/audiobook-ingestion-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now audiobook-ingestion-mcp

# Check status
sudo systemctl status audiobook-ingestion-mcp

# View logs
journalctl -u audiobook-ingestion-mcp -f
```

> **Note:** The MCP server must run on the host where `libationcli` is installed and the ABS audiobooks directory is writable (directly or via NFS). For remote setups, start the server on that host in SSE mode and connect from your AI client via URL.

### Testing

#### Test Script

`test_mcp.sh` exercises every tool with default `.env` values **and** per-call overrides.

```bash
bash abs-mcp/test_mcp.sh                        # full test against .env.test
bash abs-mcp/test_mcp.sh --skip-download         # skip Libation download step
bash abs-mcp/test_mcp.sh --env abs-mcp/.env      # use production env
```

#### Test Environment

`.env.test` is pre-configured for a test ABS instance. Adjust the values for your environment:

- **ABS host**: `your-abs-host:13378`
- **NFS mount**: `your-nfs-host:/path/to/audiobookshelf/audiobooks` -> `/mnt/abs-test/audiobooks`

```bash
MCP_ENV_FILE=abs-mcp/.env.test venv/bin/python abs-mcp/mcp_server.py
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

The `ingest_books` tool runs this full sequence:

1. `libationcli scan` -- refresh Audible library metadata
2. `libationcli liberate [ASINs]` -- download/decrypt audiobooks
3. `libationcli export` -- regenerate `libation.json`
4. `move_audio_book_files()` -- copy/move `.m4b` files into `Author/Series/Title` folders
5. ABS library scan -- makes ABS discover the new files
6. ABS match -- matches each new item to Audible metadata (cover art, description, narrator)
7. Series metadata patch -- explicitly sets the correct series name and sequence via the ABS API (fixes underscore artifacts from filesystem sanitization)

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
| `LIBRARIES_CONFIG` | Path to YAML library registry | `abs-mcp/libraries.yaml` |

#### MCP Transport

| Variable | Description | Default |
|---|---|---|
| `MCP_TRANSPORT` | Transport protocol (`sse` or `stdio`) | `sse` |
| `MCP_HOST` | Listen address | `0.0.0.0` |
| `MCP_PORT` | Listen port | `8765` |

### Available Tools

#### list_libraries

List all configured libraries with their names, types, and IDs. Use this first to discover what libraries are available.

No parameters.

#### get_status

Pipeline status and ABS connectivity check. Returns source/destination paths, ABS server version, and connection health.

| Parameter | Type | Description |
|---|---|---|
| `library` | str | Library name from libraries.yaml |
| `source_dir` | str | Libation books directory |
| `destination_dir` | str | ABS audiobooks directory / NFS mount |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

#### list_library

List all books in the Audible library from Libation's JSON export. Returns ASIN, title, author, series, and date added for each book.

| Parameter | Type | Description |
|---|---|---|
| `source_dir` | str | Libation books directory |

#### download_books

Download audiobooks from Audible via `libationcli`. Runs scan, liberate, and export steps.

| Parameter | Type | Description |
|---|---|---|
| `asins` | list[str] | ASINs to download (empty = all new) |
| `force` | bool | Force re-download |
| `source_dir` | str | Libation books directory |
| `libation_cli` | str | Path to libationcli binary |

#### process_books

Organize downloaded audiobooks into `Author/Series/Title` hierarchy on the destination filesystem. Reads `libation.json`, filters by purchase date, copies/moves `.m4b` files.

| Parameter | Type | Description |
|---|---|---|
| `purchased_how_long_ago` | int | Days filter (0 = all) |
| `library` | str | Library name (e.g. 'kids', 'adult') |
| `source_dir` | str | Libation books directory |
| `destination_dir` | str | ABS audiobooks directory / NFS mount |
| `audio_file_extension` | str | e.g. `.m4b`, `.mp3` |
| `copy_instead_of_move` | bool | Copy files instead of moving |
| `libation_folder_cleanup` | bool | Delete Libation source folders after move |
| `libation_file_locations_path` | str | Path to Libation FileLocationsV2.json |
| `enable_profanity_cleaning` | bool | Toggle monkeyplug filtering |

#### scan_audiobookshelf

Trigger an ABS library scan. ABS discovers new/changed/removed files on disk.

| Parameter | Type | Description |
|---|---|---|
| `library` | str | Library name (e.g. 'kids', 'adult_podcasts') |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

#### match_audiobookshelf

Match recently added ABS items to Audible metadata (cover art, description, narrator, series). Uses `short_title` for matching to handle subtitle differences between filesystem names and ABS-parsed titles.

| Parameter | Type | Description |
|---|---|---|
| `days_ago` | int | Match items added within this many days |
| `library` | str | Library name (e.g. 'kids', 'adult') |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

#### ingest_books

End-to-end pipeline: download -> organize -> scan ABS -> match metadata -> update series. This is the primary tool for routine use.

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

#### delete_library_items

Delete items from the ABS library by ID or purge everything. ABS retains database entries even after files are removed from disk; use this tool to fully clean up.

| Parameter | Type | Description |
|---|---|---|
| `item_ids` | list[str] | Specific ABS item IDs to delete |
| `delete_all` | bool | If true, delete every item in the library |
| `library` | str | Library name (e.g. 'kids', 'adult') |
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

**Ingest all new books to the kids library:**

```
ingest_books(library="kids")
```

**Ingest specific books to the adult library:**

```
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

**Clean up after testing:**

```
delete_library_items(delete_all=true, library="kids")
```

### Known Behaviors

- **Filesystem sanitization:** Author and series folder names replace spaces with underscores (e.g. `Ben's Damn Adventure` becomes `Bens_Damn_Adventure`). The pipeline corrects this by explicitly patching series metadata via the ABS API after matching.
- **Title matching:** ABS may parse titles differently than the original metadata (e.g. appending "Unabridged"). The pipeline uses `short_title` (the main title without subtitle) for matching to handle this.
- **ABS item persistence:** Deleting files from disk and rescanning does **not** remove items from the ABS database. Use `delete_library_items` for full cleanup.
- **Libation chapter splitting:** If Libation is configured with `SplitFilesByChapter: true`, it produces many small `.m4b` files per book instead of one. The pipeline expects single-file output. Ensure `SplitFilesByChapter` is `false` in Libation's `Settings.json`.
