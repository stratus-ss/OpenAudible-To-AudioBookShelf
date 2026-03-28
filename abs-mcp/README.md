# Audiobook Ingestion MCP Server

MCP server that automates the audiobook pipeline: downloading from Audible via Libation, organizing files, and ingesting into AudioBookShelf. Designed for SSE transport (Moltis) or stdio (Cursor).

## Prerequisites

- Python 3.10+ with venv at `../venv`
- `mcp[cli]` and `monkeyplug` installed in the venv (see `../requirements.txt`)
- `libationcli` on PATH
- NFS mount (or local path) from ABS server's audiobooks directory to the host running this server

## Quick Start

```bash
cp .env.example .env
# Edit .env with your paths, ABS URL, library ID, and API token

cd /path/to/OpenAudible-To-AudioBookShelf
source venv/bin/activate
env $(cat abs-mcp/.env | xargs) python abs-mcp/mcp_server.py
```

The server starts on `http://0.0.0.0:8765` (SSE) by default.

## Environment Variables

### Source Paths

| Variable | Description | Default |
|---|---|---|
| `SOURCE_AUDIO_BOOK_DIRECTORY` | Libation books directory | (required) |
| `DESTINATION_BOOK_DIRECTORY` | ABS audiobooks directory (NFS mount) | (required) |
| `LIBATION_CLI` | Path to libationcli binary | `libationcli` |
| `LIBATION_FILE_LOCATIONS_PATH` | Path to Libation's `FileLocationsV2.json` | (empty) |
| `AUDIO_FILE_EXTENSION` | Audio file extension | `.m4b` |
| `COPY_INSTEAD_OF_MOVE` | Copy files instead of moving | `false` |
| `LIBATION_FOLDER_CLEANUP` | Delete Libation source folders after move | `false` |

### AudioBookShelf Connection

| Variable | Description | Default |
|---|---|---|
| `ABS_SERVER_URL` | ABS server URL (e.g. `http://abs-host:13378`) | (required) |
| `ABS_LIBRARY_ID` | ABS library UUID | (required) |
| `ABS_API_TOKEN` | ABS API bearer token | (required) |

### Profanity Cleaning (MonkeyPlug)

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

### MCP Transport

| Variable | Description | Default |
|---|---|---|
| `MCP_TRANSPORT` | Transport protocol (`sse` or `stdio`) | `sse` |
| `MCP_HOST` | Listen address | `0.0.0.0` |
| `MCP_PORT` | Listen port | `8765` |

## Available Tools

Every tool parameter is optional and falls back to the `.env` default when omitted. An agent can override any value per-call without restarting the server.

### get_status

Pipeline status and ABS connectivity check.

| Parameter | Type | Description |
|---|---|---|
| `source_dir` | str | Libation books directory |
| `destination_dir` | str | ABS audiobooks directory / NFS mount |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

### list_library

List all books in the Audible library from Libation's export.

| Parameter | Type | Description |
|---|---|---|
| `source_dir` | str | Libation books directory |

### download_books

Download audiobooks from Audible via Libation CLI.

| Parameter | Type | Description |
|---|---|---|
| `asins` | list[str] | ASINs to download (empty = all new) |
| `force` | bool | Force re-download |
| `source_dir` | str | Libation books directory |
| `libation_cli` | str | Path to libationcli binary |

### process_books

Organize downloaded audiobooks into Author/Series/Title hierarchy.

| Parameter | Type | Description |
|---|---|---|
| `purchased_how_long_ago` | int | Days filter (0 = all) |
| `source_dir` | str | Libation books directory |
| `destination_dir` | str | ABS audiobooks directory / NFS mount |
| `audio_file_extension` | str | e.g. `.m4b`, `.mp3` |
| `copy_instead_of_move` | bool | Copy files instead of moving |
| `libation_folder_cleanup` | bool | Delete Libation source folders after move |
| `libation_file_locations_path` | str | Path to Libation FileLocationsV2.json |
| `enable_profanity_cleaning` | bool | Toggle monkeyplug filtering |

### scan_audiobookshelf

Trigger an ABS library scan.

| Parameter | Type | Description |
|---|---|---|
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

### match_audiobookshelf

Match recently added ABS items to Audible metadata.

| Parameter | Type | Description |
|---|---|---|
| `days_ago` | int | Match items added within this many days |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

### ingest_books

End-to-end pipeline: download -> organize -> scan ABS -> match metadata.

| Parameter | Type | Description |
|---|---|---|
| `asins` | list[str] | ASINs to download (empty = all new) |
| `purchased_how_long_ago` | int | Days filter (0 = all) |
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

### delete_library_items

Delete items from the ABS library by ID or purge everything. Useful for test cleanup since ABS retains database entries even after files are removed from disk.

| Parameter | Type | Description |
|---|---|---|
| `item_ids` | list[str] | Specific ABS item IDs to delete |
| `delete_all` | bool | If true, delete every item in the library |
| `abs_server_url` | str | ABS server URL |
| `abs_library_id` | str | ABS library UUID |
| `abs_api_token` | str | ABS API bearer token |

### Multi-Library Example

```
"Add new books to the kids library"
  -> ingest_books(abs_library_id="<kids-uuid>", destination_dir="/mnt/abs/kids-audiobooks")

"Add new books to the adults library"
  -> ingest_books()  # uses .env defaults
```

## Running

### Manual

```bash
source ../venv/bin/activate
env $(cat .env | xargs) python mcp_server.py
```

### systemd

```bash
sudo cp audiobook-ingestion-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now audiobook-ingestion-mcp
```

Requires `.env` at the path specified in the service file's `EnvironmentFile`.

### Cursor (stdio -- local)

When the MCP server runs on the same machine as Cursor. The server auto-loads `abs-mcp/.env` on startup, so the client config only needs the command and transport.

> **Important:** Use absolute paths for both the Python binary and the script. Cursor does not reliably resolve relative paths via `cwd`.

Add to `~/.cursor/mcp.json` under `"mcpServers"`:

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

### Cursor (SSE -- remote)

When the MCP server runs on a remote host (e.g. the machine with Libation and the NFS mount) and Cursor connects over the network.

**On the remote host**, start the server:

```bash
cd /path/to/OpenAudible-To-AudioBookShelf
MCP_ENV_FILE=abs-mcp/.env venv/bin/python abs-mcp/mcp_server.py
```

**In Cursor**, add to `~/.cursor/mcp.json` under `"mcpServers"`:

```json
{
  "audiobook-ingestion": {
    "url": "http://your-abs-host:8765/sse"
  }
}
```

### Claude Code (stdio -- local)

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

### Claude Code (SSE -- remote)

Add to `~/.claude/settings.json` (or project `.mcp.json`):

```json
{
  "mcpServers": {
    "audiobook-ingestion": {
      "url": "http://your-abs-host:8765/sse"
    }
  }
}
```

> **Note:** The MCP server must run on the host where Libation and the NFS mount to the ABS audiobooks directory are available. For remote setups, start the server on that host in SSE mode and connect from Cursor/Claude Code via the URL.

## Moltis Integration (SSE)

Add to your Moltis config (see `moltis-mcp-config.toml`):

```toml
[mcp.servers.audiobook_ingestion]
transport = "sse"
url = "http://your-abs-host:8765/sse"
```

Requires the server running in SSE mode (default).

## Testing

### Test Script

`test_mcp.sh` exercises every tool with default `.env` values **and** per-call overrides.

```bash
bash abs-mcp/test_mcp.sh                        # full test against .env.test
bash abs-mcp/test_mcp.sh --skip-download         # skip Libation download step
bash abs-mcp/test_mcp.sh --env abs-mcp/.env      # use production env
```

The test covers prerequisites, bridge startup, tool discovery, then each tool with both default and override parameters (get_status, list_library, download_books, process_books, scan_audiobookshelf, match_audiobookshelf, ingest_books, delete_library_items).

### Test Environment

`.env.test` is pre-configured for the test ABS instance:

- **ABS host**: `your-abs-host:13378` (your.abs.server.ip)
- **NFS mount**: `your-nfs-host:/path/to/audiobookshelf/audiobooks` -> `/mnt/abs-audiobooks` on your-abs-host
- **Credentials**: admin / testadmin123

Run the server against test:

```bash
env $(cat abs-mcp/.env.test | xargs) python abs-mcp/mcp_server.py
```
