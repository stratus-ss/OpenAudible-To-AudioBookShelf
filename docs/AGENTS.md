# AGENTS.md

## Project Overview

OpenAudible-To-AudioBookShelf (renaming to Import-To-AudioBookShelf) moves audiobook files from OpenAudible or Libation to an AudioBookShelf server. The CLI runs discrete steps: `scan` → `download` → `export` → `organize` → `scan-abs` → `match`. An optional `abs-mcp/` FastMCP server exposes pipeline tools for AI agents.

- **Language:** Python 3.12+
- **Entry:** `openaudible-to-abs` CLI or `python -m openaudible_to_audiobookshelf`
- **Key modules:** `src/openaudible_to_audiobookshelf/config.py`, `src/openaudible_to_audiobookshelf/audio_bookshelf.py`, `src/openaudible_to_audiobookshelf/utils.py`, `src/openaudible_to_audiobookshelf/audio_cleaner.py`
- **MCP:** `abs-mcp/mcp_server.py` (entry), `abs-mcp/_mcp_bridge.py` (bridge), `abs-mcp/tool_metrics.py` (response-efficiency tracking), `abs-mcp/library_parser.py` (parser-first lookups) -- Libation only (not OpenAudible)
- **Tests:** `tests/`

## Commands

```bash
# Install (Python/bare-metal path)
pip install -r requirements.txt

# Run full pipeline (Python/bare-metal)
openaudible-to-abs --server-url "http://abs.example.com" --abs-api-token "TOKEN" --library-id "ID"

# Run specific step
openaudible-to-abs --step scan-abs --server-url "http://abs.example.com" --abs-api-token "TOKEN"

# MCP server, bare-metal (Libation only)
cd abs-mcp && python mcp_server.py

# MCP server, container (Libation bundled in image)
docker pull ghcr.io/stratus-ss/mcps/audiobook-ingestion-mcp:latest
docker compose -f docker/docker-compose.yml up -d  # uses docker/docker-compose.yml + docker/docker-compose.override.yml

# Tests
pytest tests/ -v
```

## Deployment Options

The project has two equivalent deployment paths. Choose based on your constraints:

| Path | Best for | Prerequisites | Setup time | Isolation |
|------|----------|---------------|------------|-----------|
| **Python + venv + systemd** (bare-metal) | Single host, long-lived service, full control | Python 3.12+, Libation installed natively, NFS mounts configured | ~30 min | None (shares host env) |
| **Container (Docker / Compose)** | Reproducible deploys, multiple libraries, no host pollution | Docker 20.10+, Docker Compose v2, Libation config + books + ABS audiobooks on host | ~10 min | Full (image bundles Libation + .NET + Python) |

**Pick Docker if:** you want one command to deploy, you're running multiple libraries (kids/adult/podcasts), or you don't want to install Libation + .NET on the host.

**Pick bare-metal if:** you have an existing systemd-managed host with Libation already installed, or you need to debug Python code directly without rebuilding the image.

Both paths expose the same MCP tools and read the same `libraries.yaml` / `.env` files. The container path does NOT require modifying any code -- the image uses the same `MCP_ENV_FILE` env var and `LIBATION_CLI=/libation/LibationCli` setting.

For full setup instructions, see [abs-mcp/README.md](../abs-mcp/README.md) Step 5c (container) or Steps 1-6 (bare-metal).

## Scope

Agents may:
- Edit `src/openaudible_to_audiobookshelf/*.py` with verified test evidence
- Update `abs-mcp/mcp_server.py` or `library_parser.py`
- Fix config parsing or path handling
- Add YAML argument support

## Ask First

- Changes to `src/openaudible_to_audiobookshelf/audio_bookshelf.py` ABS API call structure
- Step ordering or new step introduction
- Changes to JSON normalization in `process_*_book_json` functions
- Anything that modifies file movement / deletion logic

## Never

- **Never** commit real `abs-api-token`, `api_key.txt`, or credentials
- **Never** run mass file deletion without explicit user confirmation
- **Never** assume shared filesystem is writable -- verify before file operations
- **Never** use OpenAudible for MCP workflows -- MCP requires Libation's CLI

## MCP Usage

The MCP server (`abs-mcp/mcp_server.py`) exposes 20+ pipeline tools for AI agents. For the full tool list with parameters, see [abs-mcp/README.md](../abs-mcp/README.md). Tool categories:

- **Discovery & status:** `list_libraries`, `get_status`, `list_library`, `list_abs_library`, `search_abs_library`, `get_source_status`
- **Ingestion pipeline:** `scan_audible`, `download_books`, `export_library`, `organize_books`, `scan_audiobookshelf`, `match_audiobookshelf`, `ingest_books` (all-in-one), `set_book_status`
- **Library management:** `delete_library_items`
- **Podcasts:** `search_podcasts`, `add_podcast`, `list_podcasts`, `get_podcast_episodes`, `download_podcast_episodes`, `fetch_podcast_feed`, `download_podcast_files`
- **Metrics:** `get_tool_metrics`, `query_tool_metrics_history`

For Docker deployment of the MCP server (combined Libation + MCP image), see [abs-mcp/README.md](../abs-mcp/README.md) "Docker Deployment" section. See "Deployment Options" above for the full Python-vs-Container comparison.

MCP requires Libation (not OpenAudible) due to Libation's `libationcli` CLI automation.

## Testing

```bash
pytest tests/ -v
pytest tests/ -k audio_bookshelf
```

Integration tests may require a real ABS instance and Libation library.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) -- Module map and pipeline
- [CODEFLOW.md](CODEFLOW.md) -- Runtime flow diagrams
- [CONFIGURATION.md](CONFIGURATION.md) -- Full CLI flag reference
- [abs-mcp/README.md](../abs-mcp/README.md) -- MCP server setup, tools, and Docker deployment
