# CODEFLOW.md

## Table of Contents

1. [End-to-End Pipeline](#1-end-to-end-pipeline)
2. [Export and Organize](#2-export-and-organize)
3. [AudioBookShelf Integration](#3-audiobookshelf-integration)
4. [MCP Bridge](#4-mcp-bridge)
5. [Key Modules](#5-key-modules)

---

## 1. End-to-End Pipeline

The CLI `openaudible-to-abs` (via `pipeline.py`) dispatches to discrete steps. `VALID_STEPS` defines the full sequence.

```mermaid
sequenceDiagram
    participant CLI as openaudible_to_audiobookshelf.pipeline
    participant JSON as OpenAudible / Libation JSON
    participant DISK as Filesystem
    participant ABS as AudioBookShelf API
    
    CLI->>JSON: scan (read books.json)
    JSON-->>CLI: book list
    CLI->>CLI: download (OpenAudible only)
    CLI->>DISK: export (write files to Author/Series/Title)
    CLI->>DISK: organize (rename / restructure)
    CLI->>ABS: scan-abs (trigger ABS library rescan)
    CLI->>ABS: match (match books to Audible metadata)
```

Pipeline steps: `scan` → `download` → `export` → `organize` → `scan-abs` → `match`

---

## 2. Export and Organize

`process_open_audible_book_json` and `process_libation_book_json` normalize book metadata into a common format, then `move_audio_book_files` / `make_directory_structure` write to the filesystem.

```mermaid
flowchart TD
    OA["OpenAudible books.json"] --> NORM1["process_open_audible_book_json\n→ standardized dict"]
    LIB["Libation libation.json"] --> NORM2["process_libation_book_json\n→ standardized dict"]
    
    NORM1 --> STRUCT["make_directory_structure\nAuthor / Series / Title"]
    NORM2 --> STRUCT
    STRUCT --> DISK["Destination dir\nAuthor/Series/Title/"]
    
    NORM1 --> FILE["move_audio_book_files\ncopy or move .m4b"]
    NORM2 --> FILE
```

OpenAudible and Libation have different JSON schemas -- the two `process_*` functions map each to the same internal dict shape.

---

## 3. AudioBookShelf Integration

ABS API calls are in `src/openaudible_to_audiobookshelf/audio_bookshelf.py`. After files exist on disk, `scan-abs` triggers an ABS library rescan and `match` matches books to Audible metadata.

```mermaid
sequenceDiagram
    participant CLI as openaudible_to_audiobookshelf.pipeline
    participant MOD as openaudible_to_audiobookshelf.audio_bookshelf
    participant ABS as AudioBookShelf REST API
    
    CLI->>MOD: scan_library_for_books(server_url, library_id, token)
    MOD->>ABS: POST /api/libraries/{id}/scan
    ABS-->>MOD: scan result
    MOD-->>CLI: response
    
    CLI->>MOD: get_all_books(server_url, library_id, token)
    MOD->>ABS: GET /api/libraries/{id}/books
    ABS-->>MOD: book list with ABS IDs
    MOD-->>CLI: matched books
```

---

## 4. MCP Bridge

The `abs-mcp/mcp_server.py` FastMCP server exposes 20+ pipeline tools for AI agents. It imports `openaudible_to_audiobookshelf.*` modules and the abs-mcp helper modules (`_mcp_bridge.py`, `library_parser.py`, `tool_metrics.py`) directly. For the full tool list with parameters, see [abs-mcp/README.md](../abs-mcp/README.md).

```mermaid
flowchart LR
    MCP["abs-mcp/mcp_server.py\nFastMCP tools (20+)"]
    BRIDGE["abs-mcp/_mcp_bridge.py\nMCP bridge layer"]
    METRICS["abs-mcp/tool_metrics.py\nResponse efficiency"]
    PAR["abs-mcp/library_parser.py\nLibrary JSON parsing"]
    MOD["src/openaudible_to_audiobookshelf/\naudio_bookshelf, utils, config, audio_cleaner"]
    
    MCP --> BRIDGE
    MCP --> METRICS
    MCP --> PAR
    BRIDGE --> MOD
    PAR --> MOD
```

Tool categories:

- **Discovery & status:** `list_libraries`, `get_status`, `list_library`, `list_abs_library`, `search_abs_library`, `get_source_status`
- **Ingestion pipeline:** `scan_audible`, `download_books`, `export_library`, `organize_books`, `scan_audiobookshelf`, `match_audiobookshelf`, `ingest_books`, `set_book_status`
- **Library management:** `delete_library_items`
- **Podcasts:** `search_podcasts`, `add_podcast`, `list_podcasts`, `get_podcast_episodes`, `download_podcast_episodes`, `fetch_podcast_feed`, `download_podcast_files`
- **Metrics:** `get_tool_metrics`, `query_tool_metrics_history`

MCP server supports Libation only (not OpenAudible). For Docker deployment (combined Libation + MCP image), see [abs-mcp/README.md](../abs-mcp/README.md) "Docker Deployment" section.

---

## 5. Key Modules

| Module | Role |
|--------|------|
| `src/openaudible_to_audiobookshelf/pipeline.py` | CLI entry, step dispatch, process_*_book_json normalizers |
| `src/openaudible_to_audiobookshelf/config.py` | Argparse + YAML config, all CLI flags |
| `src/openaudible_to_audiobookshelf/audio_bookshelf.py` | ABS REST client: scan, list, match |
| `src/openaudible_to_audiobookshelf/audio_cleaner.py` | Post-processing / profanity cleaning |
| `src/openaudible_to_audiobookshelf/utils.py` | Path helpers, libation export, directory structure |
| `src/openaudible_to_audiobookshelf/search_ai.py` | Optional AI-assisted search |
| `abs-mcp/mcp_server.py` | FastMCP server with 20+ pipeline tools |
| `abs-mcp/_mcp_bridge.py` | Bridge layer between FastMCP tool wrappers and the parent package |
| `abs-mcp/tool_metrics.py` | Response efficiency tracking (in-memory + JSONL persistence) |
| `abs-mcp/library_parser.py` | Library metadata parsing for MCP tools |

## Test Coverage

| Test area | Location |
|-----------|---------|
| Config / argparse | `tests/` |
| ABS client | `tests/` |
| Libation processing | `tests/` |
| Audio cleaner | `tests/` |
| MCP server | `tests/` |

Run with: `pytest tests/ -v`
