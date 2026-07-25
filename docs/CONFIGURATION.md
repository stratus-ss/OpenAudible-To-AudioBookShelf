# Configuration

Complete reference for CLI flags, environment variables, and AudioBookShelf API configuration.

## Prerequisites

- **Python 3.12+**
- **Libation or OpenAudible** installed and configured
  - MCP server requires **Libation only** (OpenAudible lacks CLI automation)
  - Libation must be configured for single-file output (`SplitFilesByChapter: false`)
- **AudioBookShelf** instance with API token
- **Shared filesystem** between Libation/ABS (NFS mount or local)
- **Docker (optional):** The MCP server can run as a container instead of on bare metal. The image bundles Libation (CLI + .NET runtime) and the MCP server together. See [abs_mcp/README.md](../abs_mcp/README.md) "Docker Deployment" section for setup. Requires Docker 20.10+ and Docker Compose v2.

## CLI Options

All options can be passed on the command line or via a YAML file (`--yaml-arguments`).

### Connection

| Flag | Default | Description |
|------|---------|-------------|
| `--server-url` | (required) | AudioBookShelf base URL |
| `--abs-api-token` | (required) | ABS API bearer token |
| `--library-id` | (required) | ABS library UUID |

### Paths

| Flag | Default | Description |
|------|---------|-------------|
| `--books-json-path` | `~/OpenAudible/books.json` | Path to OpenAudible/Libation JSON |
| `--source-audio-book-directory` | (required for Libation) | Libation books directory |
| `--destination-book-directory` | (required) | Target directory for organized audiobooks |
| `--log-file-path` | `/tmp/book_processing.txt` | Log output path |

### Processing

| Flag | Default | Description |
|------|---------|-------------|
| `--download-program` | `OpenAudible` | Source: `OpenAudible` or `Libation` |
| `--audio-file-extension` | `.m4b` | Audio file extension to look for |
| `--purchased-how-long-ago` | `7` | Days of history to process |
| `--copy-instead-of-move` | `False` | Copy files instead of moving |
| `--generate-yaml` | `False` | Write YAML instead of running |
| `--enable-profanity-cleaning` | `False` | Run monkeyplug profanity cleaning during organize. Failed books are skipped (absent from `moved`) and reported in `cleaning_failures[]`. Measured: ~8 min per hour of audio on prod Whisper (RTX 5060 Ti, `tiny` model). Requires `REMOTE_WHISPER_URL` env var — fails fast on unreachable. |
| `--remote-whisper-url` | (env: `REMOTE_WHISPER_URL`) | Whisper-webui backend URL. Used by profanity cleaning. Fails fast on TCP-level unreachable (~5s probe). |

### Libation-Specific

| Flag | Default | Description |
|------|---------|-------------|
| `--libation-folder-cleanup` | `False` | Delete source folder after processing |
| `--libation-file-locations-path` | `""` | Path to FileLocationsV2.json |

## YAML Configuration

Instead of CLI flags, pass a YAML file:

```bash
openaudible-to-abs --yaml-arguments arguments.yaml
```

See `arguments.yaml` for an example.

## Pipeline Steps

Run individual steps with `--step`:

| Step | Description |
|------|-------------|
| `scan` | Read and normalize books from JSON |
| `download` | Download books (OpenAudible only) |
| `export` | Copy/move audio files to Author/Series/Title structure |
| `organize` | Restructure directories. When `--enable-profanity-cleaning=true`, each book is run through `audio_cleaner.process_audio_file()` first; failures are caught per-book, recorded in `cleaning_failures[]`, and the batch continues. |
| `scan-abs` | Trigger AudioBookShelf library rescan |
| `match` | Match books to Audible metadata in ABS |

**MCP note:** `organize_books` and `ingest_books` (MCP) use the async job pattern -- they return `{"job_id": ..., "status": "started"}` in ~4ms, with the actual work in a background thread. Poll `get_job_result(job_id)` for the final response (which includes `cleaning_failures[]` when profanity cleaning is enabled). See [abs_mcp/README.md](../abs_mcp/README.md) for details.

## AudioBookShelf API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/libraries/{id}/scan` | POST | Trigger library scan |
| `/api/libraries/{id}/books` | GET | List all books in library |

## Environment Variables

| Var | Purpose |
|-----|---------|
| `REMOTE_WHISPER_URL` | Required when `--enable-profanity-cleaning=true`. URL of the whisper-webui backend (set in `abs_mcp/.env` from your deployment's `${REMOTE_WHISPER_URL}`). Without it, cleaning fails fast with a clear `ValueError` rather than silently moving un-cleaned files. |
| `MCP_ENV_FILE` | Path to the abs_mcp `.env` file (production: `abs_mcp/.env`). Bundles ABS credentials, library paths, profanity-cleaning config. |
| `MCP_TRANSPORT` | `streamable-http` (default) or other FastMCP transport. |
| `MCP_HOST` | Bind host for the MCP server (default: `0.0.0.0`). |
| `MCP_PORT` | Bind port for the MCP server (default: `8765`; production uses `/sse` path). |
| `MCP_STREAMABLE_PATH` | HTTP path for streamable transport (default: `/sse`). |

`libationcli` must be on PATH for Libation auto-export.

## Troubleshooting

### "books.json not found"

Provide `--books-json-path` or ensure `--download-program Libation` with `--source-audio-book-directory` pointing to Libation's books folder. Libation can auto-generate `libation.json` via `libationcli export`.

### ABS scan returns 401

Verify `--abs-api-token` is correct and has library scan permissions.

### Files not appearing in ABS

1. Run `scan-abs` step manually
2. Verify shared filesystem is writable from both Libation and ABS hosts
3. Check `--destination-book-directory` matches ABS audiobook directory

### MCP server won't start

**Bare-metal:** Ensure `libationcli` is on PATH. MCP requires Libation (not OpenAudible). Check `abs_mcp/mcp_server.py` dependencies: `pip install -r requirements.txt` in the abs_mcp context.

**Docker container:**

1. **Container logs:** `docker logs audiobook-ingestion-mcp` — look for Python import errors, missing files, or ABS connection failures.
2. **Volume mounts:** `docker inspect audiobook-ingestion-mcp | jq '.[0].Mounts'` — verify `.env` and `libraries.yaml` are mounted at `/app/abs_mcp/` and that the Libation config and books directories are mounted at `/config` and `/data` respectively.
3. **libationcli inside container:** `docker exec audiobook-ingestion-mcp /libation/LibationCli --version` — should print the Libation version. If missing, the image was built without the Libation base layer.
4. **NFS mounts:** if the ABS audiobooks directory is on a remote server, confirm the NFS mount is established on the host BEFORE the container starts. Inside the container, run `df -h` to see mounted filesystems.
5. **Environment:** `docker exec audiobook-ingestion-mcp env | grep -E "MCP_|LIBATION_|ABS_"` — verify `MCP_ENV_FILE`, `LIBATION_CLI`, and `LIBATION_FILES_DIR` are set correctly.
6. **Network:** `docker exec audiobook-ingestion-mcp curl -s http://your-abs-host:13378/healthcheck` — should return `OK`. If unreachable, check firewall and `extra_hosts` configuration in docker-compose.

## See Also

- [README.md](../README.md) -- Installation and quick start
- [ARCHITECTURE.md](ARCHITECTURE.md) -- Module map
- [CODEFLOW.md](CODEFLOW.md) -- Runtime flows
- [AGENTS.md](AGENTS.md) -- AI agent rules, MCP usage, deployment options
- [abs_mcp/README.md](../abs_mcp/README.md) -- MCP server setup, tools, and Docker deployment
