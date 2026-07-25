# ARCHITECTURE.md

## Overview

OpenAudible-To-AudioBookShelf (being renamed to Import-To-AudioBookShelf) is a Python CLI that ingests audiobook files from OpenAudible or Libation into an AudioBookShelf (ABS) server. It normalizes two different JSON schemas into a common format, writes files to an Author/Series/Title directory structure, then triggers an ABS library scan and metadata match. An optional `abs_mcp/` FastMCP server exposes pipeline tools for AI agent orchestration.

The pipeline is step-based: `scan` → `download` → `export` → `organize` → `scan-abs` → `match`. Each step is independently runnable via `--step`.

## Codemap

```mermaid
flowchart TD
    CLI["openaudible-to-abs
(pipeline:main)"]
    
    subgraph Modules["src/openaudible_to_audiobookshelf/"]
        CFG["config.py\nArgparse + YAML"]
        UTILS["utils.py\nPaths, structure, dates"]
        ABS["audio_bookshelf.py\nABS REST client"]
        CLEAN["audio_cleaner.py\nProfanity cleaning"]
        SEARCH["search_ai.py\nAI search (optional)"]
    end
    
    subgraph MCP["abs_mcp/"]
        MCP["mcp_server.py\nFastMCP tools"]
        BRIDGE["_mcp_bridge.py\nMCP bridge layer"]
        METRICS["tool_metrics.py\nResponse efficiency"]
        PAR["library_parser.py\nLibrary JSON parsing"]
    end
    
    CLI --> CFG
    CLI --> UTILS
    CLI --> ABS
    CLI --> CLEAN
    MCP --> BRIDGE
    MCP --> METRICS
    BRIDGE --> UTILS
    BRIDGE --> ABS
    MCP --> PAR
```

### `src/openaudible_to_audiobookshelf/pipeline.py`

CLI entry point. Defines `VALID_STEPS` = `scan`, `download`, `export`, `organize`, `scan-abs`, `match`. Routes to step implementations. Two JSON normalizers: `process_open_audible_book_json` and `process_libation_book_json` map each source format to a shared internal dict shape.

### `src/openaudible_to_audiobookshelf/config.py`

`Config` class wraps argparse + optional YAML file. All CLI flags defined here with defaults: `--abs-api-token`, `--books-json-path`, `--destination-book-directory`, `--download-program` (OpenAudible or Libation), `--source-audio-book-directory`, and others.

### `src/openaudible_to_audiobookshelf/audio_bookshelf.py`

ABS REST client. Functions: `scan_library_for_books`, `get_all_books`, `get_audio_bookshelf_recent_books`, `process_audio_books`. Uses `requests` with Bearer token auth.

### `src/openaudible_to_audiobookshelf/utils.py`

`generate_libation_json`, `make_directory_structure`, `find_existing_series_folder`, `sanitize_name`, `get_timestamped_log_path`.

### `abs_mcp/`

FastMCP server (`mcp_server.py`) exposing 27+ pipeline tools for AI agents across six categories: discovery, ingestion, library management, podcasts, metrics, and profanity cleaning. See [abs_mcp/README.md](../abs_mcp/README.md) for the full tool list. Supports multiple libraries via `libraries.yaml`. MCP tools parse full library JSON server-side and return filtered matches with compact JSON output to minimize token usage. **Requires Libation only** -- OpenAudible lacks CLI automation for MCP use.

**Async job pattern (DR-6).** `organize_books` and `ingest_books` are async MCP tools: they wrap `asyncio.create_task(asyncio.to_thread(...))` around the sync implementation and return `{"job_id": str, "status": "started"}` in ~4ms. Callers poll `get_job_result(job_id)` for the final response. A module-level `_active_job` dict enforces a single-slot guard — concurrent calls return `{"error": str, "active_job_id": str}`. The new `get_cleaning_progress` tool is callable concurrently with the running job (returns per-book `stage`, `books_done`, `books_total` counters in 2–5ms).

Supporting modules in `abs_mcp/`:

- **`_mcp_bridge.py`** -- Bridge layer between FastMCP tool wrappers and the parent `openaudible_to_audiobookshelf` package. Isolates MCP protocol concerns from pipeline logic.
- **`tool_metrics.py`** -- Response efficiency tracking. Records in-memory metrics (last 50 calls) and persists to a JSONL file (auto-rotates at 2000 lines, keeps most recent 1000). Used by `get_tool_metrics` and `query_tool_metrics_history` tools.
- **`library_parser.py`** -- Parser-first library lookups. Parses full library JSON server-side, applies filters, returns compact results. Used by `list_library`, `list_abs_library`, and `search_abs_library`.

For Docker deployment (combined Libation + MCP image at `ghcr.io/stratus-ss/mcps/audiobook-ingestion-mcp`), see [abs_mcp/README.md](../abs_mcp/README.md) "Docker Deployment" section.

## Architectural Invariants

1. Single Config source: `src/openaudible_to_audiobookshelf/config.py` -- argparse + YAML. No ad-hoc env var reading.
2. JSON normalization is one-way: `process_open_audible_book_json` / `process_libation_book_json` produce internal dicts, never read them back.
3. Step order matters: `export`/`organize` must run before `scan-abs`/`match`.
4. MCP requires Libation: OpenAudible cannot be automated via MCP (no CLI).
5. Shared filesystem: ABS and Libation must have a shared directory path for file ingestion.

## Layer Boundaries

- **CLI layer:** `openaudible-to-abs` / `pipeline.py` (`src/openaudible_to_audiobookshelf/pipeline.py`) -- argparse, step dispatch
- **Normalization layer:** `process_*_book_json` functions -- schema mapping
- **Filesystem layer:** `src/openaudible_to_audiobookshelf/utils.py`, `move_audio_book_files` -- disk I/O. Since commit `9566541`, `move_audio_book_files` wraps `audio_cleaner.process_audio_file()` calls in `try/except AudioCleaningError`. Failed books are appended to `_tracking["failed_books"]` (surface as `cleaning_failures[]` in the `organize_books` result). The batch continues to the next book — failed books are absent from `moved[]`.
- **API layer:** `src/openaudible_to_audiobookshelf/audio_bookshelf.py` -- ABS REST calls
- **Agent layer:** `abs_mcp/` -- FastMCP server, imports modules directly

## Cross-Cutting Concerns

- **Logging:** `logging` module; `get_timestamped_log_path` writes dated logs; `notify-send` for desktop notifications
- **Errors:** ABS HTTP errors raise `requests.HTTPError`; file ops raise `OSError`
- **Testing:** `pytest` in `tests/` covering config, ABS client, Libation processing, audio cleaner
- **Optional deps:** `openai` for AI search; `monkeyplug` for profanity cleaning
