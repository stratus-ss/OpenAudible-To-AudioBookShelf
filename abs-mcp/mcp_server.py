#!/usr/bin/env python3
"""Audiobook Ingestion MCP Server.

Exposes tools for downloading audiobooks via Libation, organizing them,
and ingesting them into AudioBookShelf. Supports multiple libraries
(including podcasts) via a YAML library registry.

Designed for SSE transport to be consumed by Moltis or other MCP clients.

Calls directly into the openaudible_to_audiobookshelf package --
no duplication of logic.
"""

import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import feedparser
import requests
import yaml

ABS_MCP_DIR = Path(__file__).resolve().parent


def _load_dotenv(env_file: Path) -> None:
    """Load a .env file into os.environ (does not override existing vars)."""
    if not env_file.is_file():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


_env_path = os.environ.get("MCP_ENV_FILE", "")
if _env_path:
    _load_dotenv(Path(_env_path))
else:
    _load_dotenv(ABS_MCP_DIR / ".env")

from library_parser import LibraryDataParser
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import EventStore
from mcp.types import JSONRPCMessage
from tool_metrics import ToolMetricsRecorder

from openaudible_to_audiobookshelf.audio_bookshelf import scan_library_for_books
from openaudible_to_audiobookshelf.config import Config
from openaudible_to_audiobookshelf.pipeline import (
    step_download,
    step_export,
    step_match,
    step_organize,
    step_scan,
    step_scan_abs,
)
from openaudible_to_audiobookshelf.utils import generate_libation_json

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
LOGGER = logging.getLogger("audiobook-ingestion-mcp")
PARSER = LibraryDataParser()

# Module-level handoff: download_books stores the ASINs it just downloaded,
# organize_books reads (and clears) them so only those books are processed.
# Falls back to a disk-scan if the list is empty (e.g. after a process restart).
_last_downloaded_asins: list[str] = []


class InMemoryEventStore(EventStore):
    """Simple in-memory event store for SSE/streamable-http session resumability."""

    def __init__(self, max_events: int = 500):
        self._events: dict[str, tuple[str, JSONRPCMessage | None]] = {}
        self._streams: dict[str, list[str]] = {}
        self._counter = 0
        self._max_events = max_events

    async def store_event(self, stream_id, message):
        self._counter += 1
        event_id = f"evt-{self._counter}"
        self._events[event_id] = (stream_id, message)
        self._streams.setdefault(stream_id, []).append(event_id)
        if len(self._events) > self._max_events:
            oldest = next(iter(self._events))
            sid, _ = self._events.pop(oldest)
            if sid in self._streams:
                self._streams[sid] = [e for e in self._streams[sid] if e != oldest]
        return event_id

    async def replay_events_after(self, last_event_id, send_callback):
        if last_event_id not in self._events:
            return None
        stream_id, _ = self._events[last_event_id]
        event_list = self._streams.get(stream_id, [])
        found = False
        for eid in event_list:
            if eid == last_event_id:
                found = True
                continue
            if found:
                _, msg = self._events[eid]
                if msg is not None:
                    await send_callback(msg)
        return stream_id if found else None


mcp = FastMCP(
    "Audiobook Ingestion",
    instructions=(
        "This server manages audiobook and podcast ingestion into AudioBookShelf. "
        "WORKFLOW FOR AUDIOBOOK INGESTION (use individual step tools for reliability):\n"
        "1. list_libraries — discover available libraries and their types\n"
        "2. list_library — browse all books in the Audible/Libation library\n"
        "3. scan_audible — refresh the Audible library list (~10s)\n"
        "4. download_books — download/decrypt books via Libation (pass ASINs or omit for all)\n"
        "5. export_library — export metadata to libation.json (~2s)\n"
        "6. organize_books — move files into Author/Series/Title tree\n"
        "7. scan_audiobookshelf — trigger ABS library scan (~20s)\n"
        "8. match_audiobookshelf — match ABS items to Audible metadata\n\n"
        "The ingest_books tool runs all steps sequentially but can timeout on "
        "long-running operations. Prefer individual step tools for LLM orchestration.\n\n"
        "DISCOVERY & VERIFICATION TOOLS:\n"
        "- list_library — filter Libation library by author/series/title for exact add-targeting\n"
        "- get_source_status — inspect source/destination directories (file counts, extensions)\n"
        "- list_abs_library — query ABS library from cache using targeted author/series/title filters\n"
        "- search_abs_library — direct ABS text search for quick lookups\n"
        "- delete_library_items — remove items with optional cleanup_files to delete disk files\n"
        "- get_tool_metrics — inspect recent response bytes/tokens for MCP tool calls\n"
        "- query_tool_metrics_history — query persisted tool metrics with time/tool filters\n\n"
        "For low-token precision lookups, avoid broad list calls. "
        "Always pass author/series/title/query and set limit=0 to return all matches in one response.\n\n"
        "Always specify library= to target the correct ABS instance (e.g. 'adult', 'kids')."
    ),
    event_store=InMemoryEventStore(),
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8765")),
    streamable_http_path=os.environ.get("MCP_STREAMABLE_PATH", "/mcp"),
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


METRICS = ToolMetricsRecorder(
    history_path=_env(
        "TOOL_METRICS_PATH", str(ABS_MCP_DIR / "data" / "tool-metrics.jsonl")
    ),
)


def _r(override: str | None, env_key: str, default: str = "") -> str:
    """Resolve a value: per-call override > env var > default."""
    if override is not None:
        return override
    return os.environ.get(env_key, default)


def _log_buffer() -> io.StringIO:
    return io.StringIO()


def _elapsed_ms(start_time: float) -> int:
    """Milliseconds elapsed since start time."""
    return int((time.monotonic() - start_time) * 1000)


def _record_tool_result(
    tool_name: str, start_time: float, result: str, success: bool = True
) -> str:
    """Record response efficiency metrics and return the result unchanged."""
    return METRICS.record(
        tool_name=tool_name,
        result=result,
        duration_ms=_elapsed_ms(start_time),
        success=success,
    )


# ---------------------------------------------------------------------------
# Library registry
# ---------------------------------------------------------------------------

_LIBRARIES_CACHE: dict | None = None


def _load_libraries() -> dict:
    """Load the YAML library registry, caching after first read."""
    global _LIBRARIES_CACHE
    if _LIBRARIES_CACHE is not None:
        return _LIBRARIES_CACHE

    config_path = _env("LIBRARIES_CONFIG", "")
    if not config_path:
        config_path = str(ABS_MCP_DIR / "libraries.yaml")

    path = Path(config_path)
    if not path.is_file():
        LOGGER.warning("Libraries config not found at %s", config_path)
        _LIBRARIES_CACHE = {"default_library": "", "libraries": {}}
        return _LIBRARIES_CACHE

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    _LIBRARIES_CACHE = {
        "default_library": data.get("default_library", ""),
        "libraries": data.get("libraries", {}),
    }
    LOGGER.info(
        "Loaded %d libraries from %s (default: %s)",
        len(_LIBRARIES_CACHE["libraries"]),
        config_path,
        _LIBRARIES_CACHE["default_library"],
    )
    return _LIBRARIES_CACHE


def _resolve_library(library: str | None) -> dict:
    """Resolve a friendly library name to its full config dict.

    Returns a dict with keys: library_id, destination_dir, media_type,
    folder_id, abs_server_url, abs_api_token.  Missing per-library
    values fall back to env-var defaults.
    """
    cfg = _load_libraries()
    name = library or cfg.get("default_library", "")
    lib_entry = cfg["libraries"].get(name, {}) if name else {}

    return {
        "name": name,
        "library_id": lib_entry.get("library_id", _env("ABS_LIBRARY_ID")),
        "destination_dir": lib_entry.get(
            "destination_dir", _env("DESTINATION_BOOK_DIRECTORY")
        ),
        "media_type": lib_entry.get("media_type", "book"),
        "folder_id": lib_entry.get("folder_id", ""),
        "abs_server_url": lib_entry.get("abs_server_url", _env("ABS_SERVER_URL")),
        "abs_api_token": lib_entry.get("abs_api_token", _env("ABS_API_TOKEN")),
    }


def _abs_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _normalize_limit_offset(
    limit: int, offset: int, default_limit: int = 25, max_limit: int = 200
) -> tuple[int, int]:
    """Normalize pagination inputs with sane bounds for MCP payload size."""
    safe_limit = limit if limit and limit > 0 else default_limit
    safe_limit = min(safe_limit, max_limit)
    safe_offset = max(offset, 0)
    return safe_limit, safe_offset


def _sanitize_cache_key(name: str) -> str:
    """Sanitize cache key for filesystem safety."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "default")
    return cleaned[:64] or "default"


def _abs_cache_path(library_name: str, library_id: str) -> Path:
    """Build deterministic ABS cache file path."""
    cache_root = Path(_env("ABS_CACHE_DIR", "/tmp/abs-cache"))
    cache_root.mkdir(parents=True, exist_ok=True)
    key = _sanitize_cache_key(library_name or library_id or "default")
    return cache_root / f"{key}.json"


def _fetch_abs_library_items(url: str, library_id: str, token: str) -> list[dict]:
    """Fetch the complete ABS library item set."""
    resp = requests.get(
        f"{url}/api/libraries/{library_id}/items",
        headers=_abs_headers(token),
        params={"limit": 0, "sort": "addedAt"},
        timeout=45,
    )
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    return resp.json().get("results", [])


def _fetch_abs_series_map(url: str, library_id: str, token: str) -> dict[str, str]:
    """Build a book_id -> series names mapping from the ABS series endpoint.

    The /items endpoint returns minified data without series metadata.
    This fetches the /series endpoint and builds a lookup so we can
    inject series into each item before caching.
    """
    resp = requests.get(
        f"{url}/api/libraries/{library_id}/series",
        headers=_abs_headers(token),
        params={"limit": 500},
        timeout=30,
    )
    if not resp.ok:
        LOGGER.warning("Failed to fetch series data: HTTP %s", resp.status_code)
        return {}
    series_map: dict[str, str] = {}
    for s in resp.json().get("results", []):
        name = s.get("name", "")
        if not name:
            continue
        for book in s.get("books", []):
            bid = book.get("id", "")
            if bid:
                existing = series_map.get(bid, "")
                series_map[bid] = f"{existing}, {name}" if existing else name
    LOGGER.info(
        "Series map: %d books across %d series",
        len(series_map),
        len(resp.json().get("results", [])),
    )
    return series_map


def _enrich_items_with_series(items: list[dict], series_map: dict[str, str]) -> None:
    """Inject series metadata into minified ABS items in-place."""
    if not series_map:
        return
    for item in items:
        bid = item.get("id", "")
        if bid in series_map:
            metadata = item.setdefault("media", {}).setdefault("metadata", {})
            if not metadata.get("series"):
                names = series_map[bid]
                metadata["series"] = [{"name": n.strip()} for n in names.split(", ")]


def _load_or_refresh_abs_cache(
    cache_path: Path,
    refresh: bool,
    max_age_seconds: int,
    url: str,
    library_id: str,
    token: str,
    library_name: str,
) -> tuple[list[dict], dict]:
    """Load ABS items from cache, refreshing if requested or stale."""
    now = int(time.time())
    cache_exists = cache_path.is_file()
    cache_data: dict = {}
    is_stale = True
    if cache_exists:
        with open(cache_path) as f:
            cache_data = json.load(f)
        fetched_at = int(cache_data.get("fetched_at", 0) or 0)
        is_stale = (now - fetched_at) > max_age_seconds
        if not is_stale and not cache_data.get("series_enriched"):
            LOGGER.info("Cache missing series enrichment, forcing refresh")
            is_stale = True
    if refresh or not cache_exists or is_stale:
        items = _fetch_abs_library_items(url, library_id, token)
        series_map = _fetch_abs_series_map(url, library_id, token)
        _enrich_items_with_series(items, series_map)
        cache_data = {
            "library": library_name,
            "library_id": library_id,
            "fetched_at": now,
            "item_count": len(items),
            "series_enriched": True,
            "items": items,
        }
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)
        return items, {"refreshed": True, "fetched_at": now}
    return cache_data.get("items", []), {
        "refreshed": False,
        "fetched_at": int(cache_data.get("fetched_at", 0) or 0),
    }


def _flatten_abs_item(item: dict) -> dict:
    """Flatten ABS item into compact MCP-friendly fields."""
    media = item.get("media", {})
    metadata = media.get("metadata", {})
    return {
        "id": item.get("id", ""),
        "title": metadata.get("title", ""),
        "author": metadata.get("authorName", ""),
        "series": _extract_series_names(item),
        "duration": round(media.get("duration", 0) / 60, 1),
        "added_at": item.get("addedAt", ""),
        "has_audio": bool(media.get("audioFiles")),
    }


def _filter_abs_items(
    items: list[dict],
    query: str | None,
    title: str | None,
    author: str | None,
    series: str | None,
) -> list[dict]:
    """Filter ABS items by broad and field-specific substring checks."""
    result = items
    if query:
        q = query.lower()
        result = [
            item
            for item in result
            if q in item.get("title", "").lower()
            or q in item.get("author", "").lower()
            or q in item.get("series", "").lower()
        ]
    if title:
        t = title.lower()
        result = [item for item in result if t in item.get("title", "").lower()]
    if author:
        a = author.lower()
        result = [item for item in result if a in item.get("author", "").lower()]
    if series:
        s = series.lower()
        result = [item for item in result if s in item.get("series", "").lower()]
    return result


def _sort_abs_items(items: list[dict], sort_by: str | None) -> list[dict]:
    """Sort flattened ABS items by known fields."""
    if not sort_by:
        return items
    key_map = {
        "title": lambda i: i.get("title", "").lower(),
        "author": lambda i: i.get("author", "").lower(),
        "series": lambda i: i.get("series", "").lower(),
        "duration": lambda i: i.get("duration", 0),
        "added_at": lambda i: i.get("added_at", ""),
    }
    key_fn = key_map.get(sort_by)
    if not key_fn:
        return items
    return sorted(items, key=key_fn)


def _build_config(
    library: str | None = None,
    source_dir: str | None = None,
    destination_dir: str | None = None,
    audio_file_extension: str | None = None,
    copy_instead_of_move: bool | None = None,
    libation_folder_cleanup: bool | None = None,
    libation_file_locations_path: str | None = None,
    enable_profanity_cleaning: bool | None = None,
    purchased_how_long_ago: int = 7,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
    libation_cli: str | None = None,
    asins: list[str] | None = None,
) -> Config:
    """Build a Config object from MCP tool parameters + library registry + env."""
    lib = _resolve_library(library)
    cfg = Config()
    cfg.source_audio_book_directory = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    cfg.destination_book_directory = destination_dir or lib["destination_dir"]
    cfg.audio_file_extension = _r(audio_file_extension, "AUDIO_FILE_EXTENSION", ".m4b")
    cfg.copy_instead_of_move = (
        copy_instead_of_move
        if copy_instead_of_move is not None
        else _env("COPY_INSTEAD_OF_MOVE", "false").lower() == "true"
    )
    cfg.libation_folder_cleanup = (
        libation_folder_cleanup
        if libation_folder_cleanup is not None
        else _env("LIBATION_FOLDER_CLEANUP", "false").lower() == "true"
    )
    cfg.libation_file_locations_path = _r(
        libation_file_locations_path, "LIBATION_FILE_LOCATIONS_PATH"
    )
    cfg.enable_profanity_cleaning = (
        enable_profanity_cleaning
        if enable_profanity_cleaning is not None
        else _env("ENABLE_PROFANITY_CLEANING", "false").lower() == "true"
    )
    cfg.purchased_how_long_ago = purchased_how_long_ago
    cfg.server_url = abs_server_url or lib["abs_server_url"]
    cfg.library_id = abs_library_id or lib["library_id"]
    cfg.abs_api_token = abs_api_token or lib["abs_api_token"]
    cfg.libation_cli = libation_cli or _env("LIBATION_CLI", "libationcli")
    cfg.download_program = "Libation"
    cfg.books_json_path = str(Path(cfg.source_audio_book_directory) / "libation.json")
    cfg.asins = asins
    cfg.working_directory = _env("WORKING_DIRECTORY", "/tmp/monkeyplug-cleaning")
    cfg.save_transcripts = _env("SAVE_TRANSCRIPTS", "true").lower() == "true"
    cfg.swears_file = _env("SWEARS_FILE", "")
    cfg.remote_whisper_url = _env("REMOTE_WHISPER_URL", "")
    cfg.timeout = int(_env("TIMEOUT", "600"))
    cfg.confidence_threshold = float(_env("CONFIDENCE_THRESHOLD", "0.70"))
    cfg.beep_mode = _env("BEEP_MODE", "false").lower() == "true"
    _validate_profanity_config(cfg)
    return cfg


def _validate_profanity_config(cfg: Config) -> None:
    """Validate profanity cleaning config and auto-discover swears file.

    The MCP path constructs Config directly via _build_config and never reaches
    Config._validate(), so the MonkeyPlug checks that live there are bypassed.
    Without this guard, AudioCleaner proceeds with empty remoteUrl or empty
    swears_file and WhisperPlugger fails at init, after which process_audio_file
    silently falls back to the original file and the move step still runs.
    Fail loud, fail early.
    """
    if not getattr(cfg, "enable_profanity_cleaning", False):
        return
    url = (getattr(cfg, "remote_whisper_url", "") or "").strip()
    if not url:
        raise ValueError(
            "Profanity cleaning is enabled but REMOTE_WHISPER_URL is not set. "
            "Configure a Whisper-WebUI endpoint in .env "
            "(e.g. REMOTE_WHISPER_URL=http://whisper-host:8000) or disable "
            "profanity cleaning via ENABLE_PROFANITY_CLEANING=false."
        )
    swears = (getattr(cfg, "swears_file", "") or "").strip()
    if swears:
        return
    try:
        import monkeyplug
        monkeyplug_dir = os.path.dirname(monkeyplug.__file__)
        default_swears = os.path.join(monkeyplug_dir, "swears.txt")
        if os.path.exists(default_swears):
            cfg.swears_file = default_swears
            return
    except ImportError:
        pass
    raise ValueError(
        "Profanity cleaning is enabled but SWEARS_FILE is not set and "
        "MonkeyPlug's bundled swears.txt was not found. Install the "
        "monkeyplug package or set SWEARS_FILE in .env."
    )


@mcp.tool()
def list_libraries() -> str:
    """List all configured libraries with their names, types, and IDs.

    Use this to discover available libraries before calling other tools.
    Libraries of media_type 'book' support audiobook ingestion tools.
    Libraries of media_type 'podcast' support podcast tools.
    """
    _tool_start = time.monotonic()
    cfg = _load_libraries()
    result = {
        "default_library": cfg["default_library"],
        "libraries": {
            name: {
                "library_id": lib.get("library_id", ""),
                "media_type": lib.get("media_type", "book"),
                "destination_dir": lib.get("destination_dir", ""),
            }
            for name, lib in cfg["libraries"].items()
        },
    }
    return _record_tool_result("list_libraries", _tool_start, json.dumps(result))


def _filter_library_books(
    books: list[dict],
    status: str | None,
    author: str | None,
    title: str | None,
    min_duration: int | None,
    max_duration: int | None,
) -> list[dict]:
    """Apply filters to a list of Libation book dicts."""
    result = books
    if status:
        status_lower = status.lower()
        result = [b for b in result if b.get("BookStatus", "").lower() == status_lower]
    if author:
        author_lower = author.lower()
        result = [b for b in result if author_lower in b.get("AuthorNames", "").lower()]
    if title:
        title_lower = title.lower()
        result = [b for b in result if title_lower in b.get("Title", "").lower()]
    if min_duration is not None:
        result = [b for b in result if b.get("LengthInMinutes", 0) >= min_duration]
    if max_duration is not None:
        result = [b for b in result if b.get("LengthInMinutes", 0) <= max_duration]
    return result


def _sort_library_books(books: list[dict], sort_by: str) -> list[dict]:
    """Sort book list by a supported field."""
    key_map = {
        "duration": lambda b: b.get("LengthInMinutes", 0),
        "title": lambda b: b.get("Title", "").lower(),
        "author": lambda b: b.get("AuthorNames", "").lower(),
        "date_added": lambda b: b.get("DateAdded", ""),
    }
    key_fn = key_map.get(sort_by)
    if key_fn:
        return sorted(books, key=key_fn)
    return books


@mcp.tool()
def list_library(
    source_dir: str | None = None,
    status: str | None = None,
    author: str | None = None,
    series: str | None = None,
    title: str | None = None,
    max_duration: int | None = None,
    min_duration: int | None = None,
    limit: int = 0,
    offset: int = 0,
    sort_by: str | None = None,
) -> str:
    """List books in the Audible library from Libation's export with filtering.

    Args:
        source_dir: Libation books directory (default: from .env).
        status: Filter by BookStatus (e.g. 'NotLiberated', 'Liberated').
        author: Filter by author name (case-insensitive substring match).
        series: Filter by series name (case-insensitive substring match).
        title: Filter by title (case-insensitive substring match).
        max_duration: Only books shorter than this many minutes.
        min_duration: Only books longer than this many minutes.
        limit: Max results to return. With limit=0, returns all matches.
        offset: Number of filtered results to skip (default 0).
        sort_by: Sort field: 'duration', 'title', 'author', 'date_added' (default: none).
    """
    _tool_start = time.monotonic()
    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    json_path = str(Path(src) / "libation.json")

    if not Path(json_path).exists():
        log = _log_buffer()
        generate_libation_json(json_path, log)

    with open(json_path) as f:
        books = json.load(f)

    filtered = PARSER.filter_libation_books(
        books, status, author, series, title, min_duration, max_duration
    )

    if sort_by:
        filtered = PARSER.sort_libation_books(filtered, sort_by)

    filters_present = any(
        [
            status,
            author,
            series,
            title,
            min_duration is not None,
            max_duration is not None,
        ]
    )
    paged, effective_limit, safe_offset, next_offset, paginated = (
        PARSER.select_output_slice(
            filtered,
            limit,
            offset,
            filters_present=filters_present,
        )
    )

    summary = [
        {
            "asin": b.get("AudibleProductId", ""),
            "title": b.get("Title", ""),
            "subtitle": b.get("Subtitle", ""),
            "author": b.get("AuthorNames", ""),
            "series": b.get("SeriesNames", ""),
            "duration_minutes": b.get("LengthInMinutes", 0),
            "status": b.get("BookStatus", ""),
            "date_added": b.get("DateAdded", ""),
        }
        for b in paged
    ]
    return _record_tool_result(
        "list_library",
        _tool_start,
        json.dumps(
            {
                "total_in_library": len(books),
                "total_matches": len(filtered),
                "limit": effective_limit,
                "offset": safe_offset,
                "next_offset": next_offset,
                "paginated": paginated,
                "books": summary,
            }
        ),
    )


@mcp.tool()
def scan_audible(
    libation_cli: str | None = None,
) -> str:
    """Refresh the Audible library list via Libation.

    This is a fast operation (~10 seconds). Run before download_books
    to ensure the library listing is current.

    Args:
        libation_cli: Path to libationcli binary (default: from .env).
    """
    _tool_start = time.monotonic()
    cfg = _build_config(libation_cli=libation_cli)
    result = step_scan(cfg)
    return _record_tool_result("scan_audible", _tool_start, json.dumps(result))


@mcp.tool()
def set_book_status(
    asins: list[str],
    status: str = "not-downloaded",
    force: bool = True,
    libation_cli: str | None = None,
) -> str:
    """Set the download status of books in Libation's database.

    Use status='not-downloaded' to mark books as unliberated so they
    can be re-downloaded with download_books.

    Args:
        asins: Product IDs (ASINs) of books to update.
        status: 'not-downloaded' or 'downloaded'.
        force: Set status even if the audio file exists on disk.
        libation_cli: Path to libationcli binary (default: from .env).
    """
    _tool_start = time.monotonic()
    cli = libation_cli or _env("LIBATION_CLI", "libationcli")
    flag = "--not-downloaded" if status == "not-downloaded" else "--downloaded"
    cmd = [cli, "set-status", flag]
    if force:
        cmd.append("--force")
    cmd.extend(asins)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return _record_tool_result(
        "set_book_status",
        _tool_start,
        json.dumps(
            {
                "success": result.returncode == 0,
                "asins": asins,
                "status_set": status,
                "force": force,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        ),
        success=result.returncode == 0,
    )


@mcp.tool()
def download_books(
    asins: list[str] | None = None,
    libation_cli: str | None = None,
) -> str:
    """Download audiobooks from Audible via Libation CLI.

    Can take minutes for large books. Pass specific ASINs to download
    only those books, or omit to download all un-downloaded books.

    Args:
        asins: Optional list of ASINs to download. If empty, downloads all new books.
        libation_cli: Path to libationcli binary (default: from .env).
    """
    _tool_start = time.monotonic()
    cfg = _build_config(libation_cli=libation_cli, asins=asins)
    result = step_download(cfg)
    # Stash the just-downloaded ASINs so a subsequent organize_books call can
    # limit itself to those books.
    global _last_downloaded_asins
    _last_downloaded_asins = result.get("asins", []) or []
    return _record_tool_result(
        "download_books",
        _tool_start,
        json.dumps(result),
        success=not bool(result.get("error")),
    )


@mcp.tool()
def export_library(
    source_dir: str | None = None,
    libation_cli: str | None = None,
) -> str:
    """Export Libation library metadata to libation.json.

    Run after download_books so the JSON reflects newly downloaded titles.

    Args:
        source_dir: Libation books directory (default: from .env).
        libation_cli: Path to libationcli binary (default: from .env).
    """
    _tool_start = time.monotonic()
    cfg = _build_config(source_dir=source_dir, libation_cli=libation_cli)
    result = step_export(cfg)
    return _record_tool_result(
        "export_library",
        _tool_start,
        json.dumps(result),
        success=not bool(result.get("error")),
    )


@mcp.tool()
def organize_books(
    purchased_how_long_ago: int = 0,
    library: str | None = None,
    source_dir: str | None = None,
    destination_dir: str | None = None,
    audio_file_extension: str | None = None,
    copy_instead_of_move: bool | None = None,
    libation_folder_cleanup: bool | None = None,
    libation_file_locations_path: str | None = None,
    enable_profanity_cleaning: bool | None = None,
) -> str:
    """Organize downloaded audiobooks into the ABS directory structure.

    Reads the Libation JSON export, filters by purchase date, and moves/copies
    audio files into an Author/Series/Title folder hierarchy.

    Default audio format is .m4b. If no .m4b files are found in the source
    directory, the tool auto-detects the actual extension present. Pass
    audio_file_extension='.mp3' only if you specifically need mp3.

    Args:
        purchased_how_long_ago: Process books purchased within this many days. 0 means all.
        library: Library name from libraries.yaml (e.g. 'kids', 'adult').
        source_dir: Libation books directory (default: from .env).
        destination_dir: ABS audiobooks directory (default: from .env or library config).
        audio_file_extension: File extension (default: .m4b, auto-detects if no .m4b found).
        copy_instead_of_move: Copy files instead of moving (default: from .env).
        libation_folder_cleanup: Delete Libation source folders after move (default: from .env).
        libation_file_locations_path: Path to Libation FileLocationsV2.json (default: from .env).
        enable_profanity_cleaning: Enable monkeyplug profanity filtering (default: from .env).
    """
    _tool_start = time.monotonic()
    user_specified_ext = audio_file_extension is not None

    try:
        cfg = _build_config(
            library=library,
            source_dir=source_dir,
            destination_dir=destination_dir,
            audio_file_extension=audio_file_extension,
            copy_instead_of_move=copy_instead_of_move,
            libation_folder_cleanup=libation_folder_cleanup,
            libation_file_locations_path=libation_file_locations_path,
            enable_profanity_cleaning=enable_profanity_cleaning,
            purchased_how_long_ago=purchased_how_long_ago,
        )
    except ValueError as e:
        return _record_tool_result(
            "organize_books",
            _tool_start,
            json.dumps({"step": "organize", "success": False, "error": str(e)}),
            success=False,
        )

    if not user_specified_ext:
        detected = _detect_audio_extension(Path(cfg.source_audio_book_directory))
        if detected and detected != cfg.audio_file_extension:
            LOGGER.info(
                "Auto-detected audio extension %s (configured: %s)",
                detected,
                cfg.audio_file_extension,
            )
            cfg.audio_file_extension = detected

    # Resolve which ASINs to organize: prefer the in-memory handoff from the
    # most recent download_books call; fall back to scanning the source
    # directory for audiobook files (catches process-restart edge case).
    global _last_downloaded_asins
    download_asins = _last_downloaded_asins
    _last_downloaded_asins = []
    if not download_asins:
        download_asins = _extract_asins_from_dir(cfg.source_audio_book_directory)

    result = step_organize(cfg, asins=download_asins if download_asins else None)
    clean = {k: v for k, v in result.items() if not k.startswith("_")}
    clean["audio_file_extension"] = cfg.audio_file_extension
    return _record_tool_result(
        "organize_books",
        _tool_start,
        json.dumps(clean),
        success=not bool(clean.get("error")),
    )


def _detect_audio_extension(source_dir: Path) -> str:
    """Detect the most common audio extension in the source directory."""
    if not source_dir.is_dir():
        return ""
    audio_exts = {".m4b", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac"}
    counts: dict[str, int] = {}
    for child in source_dir.iterdir():
        if not child.is_dir():
            continue
        for f in child.rglob("*"):
            if f.is_file() and f.suffix.lower() in audio_exts:
                ext = f.suffix.lower()
                counts[ext] = counts.get(ext, 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda e: (e == ".m4b", counts[e]))


# Audible ASINs look like "B0" + 8 alphanumerics; Libation embeds them in
# filenames as e.g. "Title [B0F94NGCD1].m4b".
_ASIN_PATTERN = re.compile(r"\[(B0[A-Z0-9]{8})\]")


def _extract_asins_from_dir(source_dir: str) -> list[str]:
    """Scan source directory for audiobook files and extract ASINs from filenames."""
    asins: set[str] = set()
    if not os.path.isdir(source_dir):
        return []
    for root, _, files in os.walk(source_dir):
        for fname in files:
            if fname.endswith((".m4b", ".mp3")):
                m = _ASIN_PATTERN.search(fname)
                if m:
                    asins.add(m.group(1))
    return sorted(asins)


@mcp.tool()
def scan_audiobookshelf(
    library: str | None = None,
    wait: int = 15,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Trigger an AudioBookShelf library scan and wait for it to settle.

    Run after organize_books so ABS discovers the new files.

    Args:
        library: Library name from libraries.yaml (e.g. 'kids', 'adult').
        wait: Seconds to wait after triggering the scan (default: 15).
        abs_server_url: Override ABS server URL.
        abs_library_id: Override ABS library ID.
        abs_api_token: Override ABS API token.
    """
    _tool_start = time.monotonic()
    cfg = _build_config(
        library=library,
        abs_server_url=abs_server_url,
        abs_library_id=abs_library_id,
        abs_api_token=abs_api_token,
    )
    result = step_scan_abs(cfg, wait=wait)
    return _record_tool_result(
        "scan_audiobookshelf",
        _tool_start,
        json.dumps(result),
        success=not bool(result.get("error")),
    )


@mcp.tool()
def delete_library_items(
    item_ids: list[str] | None = None,
    delete_all: bool = False,
    cleanup_files: bool = False,
    library: str | None = None,
    source_dir: str | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Delete items from the AudioBookShelf library and optionally clean up files.

    Use to clean up test data or remove specific items. Provide either
    a list of item IDs or set delete_all=True to purge the library.

    Args:
        item_ids: Specific ABS library item IDs to delete.
        delete_all: If True, delete every book in the library (use with caution).
        cleanup_files: Also remove audio files from the destination and source
            directories. **Strongly recommended to set True.** Defaults to False,
            in which case only the ABS DB row is removed; the `.m4b` file remains
            in the destination directory, and ABS's file-system watcher
            (`disableWatcher=false` on both libraries) auto-rescans within ~5
            seconds and re-imports the orphan under a fresh UUID. The tool still
            returns ``success=true, deleted=1`` after a DB-only delete, so the
            resurrection is invisible at the MCP boundary and only surfaces when
            the user reopens the ABS UI.
        library: Library name from libraries.yaml (e.g. 'kids', 'adult').
        source_dir: Libation source directory to clean (default: from .env).
        abs_server_url: Override ABS server URL.
        abs_library_id: Override ABS library ID.
        abs_api_token: Override ABS API token.
    """
    _tool_start = time.monotonic()
    lib = _resolve_library(library)
    url = abs_server_url or lib["abs_server_url"]
    lib_id = abs_library_id or lib["library_id"]
    token = abs_api_token or lib["abs_api_token"]
    headers = _abs_headers(token)

    ids_to_delete = list(item_ids) if item_ids else []
    items_data = []

    if delete_all and not ids_to_delete:
        resp = requests.get(
            f"{url}/api/libraries/{lib_id}/items",
            headers=headers,
            params={"limit": 0},
            timeout=30,
        )
        if resp.ok:
            items_data = resp.json().get("results", [])
            ids_to_delete = [item["id"] for item in items_data]

    if cleanup_files and ids_to_delete:
        detailed = []
        for item_id in ids_to_delete:
            r = requests.get(f"{url}/api/items/{item_id}", headers=headers, timeout=30)
            if r.ok:
                detailed.append(r.json())
        items_data = detailed

    results = []
    for item_id in ids_to_delete:
        r = requests.delete(f"{url}/api/items/{item_id}", headers=headers, timeout=30)
        results.append({"id": item_id, "status": r.status_code})

    files_cleaned = []
    if cleanup_files:
        files_cleaned = _cleanup_item_files(
            items_data,
            lib["destination_dir"],
            _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY"),
        )

    output: dict = {"deleted": len(results), "results": results}
    if cleanup_files:
        output["files_cleaned"] = files_cleaned
    return _record_tool_result("delete_library_items", _tool_start, json.dumps(output))


def _cleanup_item_files(
    items_data: list[dict],
    destination_dir: str,
    source_dir: str,
) -> list[dict]:
    """Remove audio files from destination and source directories for deleted items."""
    cleaned = []
    dest_dirs_removed: set[str] = set()

    for item in items_data:
        item_path = item.get("path", "")
        rel_path = item.get("relPath", "")
        title = item.get("media", {}).get("metadata", {}).get("title", "unknown")
        entry: dict = {"title": title, "dest_removed": False, "source_removed": False}

        dest_path = _resolve_dest_path(item_path, rel_path, destination_dir)
        if dest_path and dest_path not in dest_dirs_removed:
            entry.update(_try_rmtree(dest_path, "dest"))
            if entry["dest_removed"]:
                dest_dirs_removed.add(dest_path)

        if source_dir:
            _clean_source_by_audio_files(Path(source_dir), item, entry)

        cleaned.append(entry)
    return cleaned


def _resolve_dest_path(item_path: str, rel_path: str, destination_dir: str) -> str:
    """Resolve the actual filesystem path for an ABS item's destination folder."""
    if item_path and Path(item_path).is_dir():
        return item_path
    if rel_path and destination_dir:
        candidate = Path(destination_dir) / rel_path
        if candidate.is_dir():
            return str(candidate)
    return ""


def _try_rmtree(path: str, prefix: str) -> dict:
    """Attempt to remove a directory tree, returning status dict."""
    result: dict = {f"{prefix}_removed": False}
    try:
        shutil.rmtree(path)
        result[f"{prefix}_removed"] = True
        result[f"{prefix}_path"] = path
    except Exception as e:
        result[f"{prefix}_error"] = str(e)
    return result


def _clean_source_by_audio_files(source: Path, item: dict, entry: dict) -> None:
    """Remove source folder matching by audio filenames or ASIN in folder name."""
    if not source.is_dir():
        return
    audio_files = item.get("media", {}).get("audioFiles", [])
    filenames = {
        Path(af.get("metadata", {}).get("filename", "")).stem.lower()
        for af in audio_files
    }
    filenames.discard("")

    for child in source.iterdir():
        if not child.is_dir():
            continue
        child_files = {f.stem.lower() for f in child.rglob("*") if f.is_file()}
        if filenames and filenames & child_files:
            entry.update(_try_rmtree(str(child), "source"))
            return

    title = item.get("media", {}).get("metadata", {}).get("title", "").lower()
    if not title:
        return
    for child in source.iterdir():
        if child.is_dir() and title in child.name.lower():
            entry.update(_try_rmtree(str(child), "source"))
            return


@mcp.tool()
def match_audiobookshelf(
    days_ago: int = 7,
    library: str | None = None,
    book_list: list[dict] | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Match AudioBookShelf items to Audible metadata.

    Run after scan_audiobookshelf to match newly added books.
    Either pass days_ago to match recent items, or pass book_list
    (output from organize_books) to match specific titles.

    Args:
        days_ago: Match items added within this many days (default: 7).
        library: Library name from libraries.yaml (e.g. 'kids', 'adult').
        book_list: Specific books to match (list of dicts with title/asin/series keys).
        abs_server_url: Override ABS server URL.
        abs_library_id: Override ABS library ID.
        abs_api_token: Override ABS API token.
    """
    _tool_start = time.monotonic()
    cfg = _build_config(
        library=library,
        abs_server_url=abs_server_url,
        abs_library_id=abs_library_id,
        abs_api_token=abs_api_token,
        purchased_how_long_ago=days_ago,
    )
    result = step_match(cfg, book_list=book_list)
    return _record_tool_result(
        "match_audiobookshelf",
        _tool_start,
        json.dumps(result),
        success=not bool(result.get("error")),
    )


@mcp.tool()
def ingest_books(
    asins: list[str] | None = None,
    purchased_how_long_ago: int = 0,
    library: str | None = None,
    source_dir: str | None = None,
    destination_dir: str | None = None,
    audio_file_extension: str | None = None,
    copy_instead_of_move: bool | None = None,
    libation_folder_cleanup: bool | None = None,
    libation_file_locations_path: str | None = None,
    libation_cli: str | None = None,
    enable_profanity_cleaning: bool | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """End-to-end pipeline: scan -> download -> export -> organize -> scan ABS -> match.

    WARNING: This runs all six steps sequentially and can take 30+ minutes.
    LLM agents should prefer calling individual step tools for reliability:
      scan_audible -> download_books -> export_library -> organize_books
      -> scan_audiobookshelf -> match_audiobookshelf

    Args:
        asins: Optional list of ASINs to download. If empty, downloads all new books.
        purchased_how_long_ago: Process books purchased within this many days. 0 means all.
        library: Library name from libraries.yaml (e.g. 'kids', 'adult').
        source_dir: Libation books directory (default: from .env).
        destination_dir: ABS audiobooks directory (default: from .env or library config).
        audio_file_extension: File extension, e.g. '.m4b' (default: from .env).
        copy_instead_of_move: Copy files instead of moving (default: from .env).
        libation_folder_cleanup: Delete Libation source folders after move (default: from .env).
        libation_file_locations_path: Path to Libation FileLocationsV2.json (default: from .env).
        libation_cli: Path to libationcli binary (default: from .env).
        enable_profanity_cleaning: Enable monkeyplug profanity filtering (default: from .env).
        abs_server_url: ABS server URL (default: from .env or library config).
        abs_library_id: ABS library UUID (default: from .env or library config).
        abs_api_token: ABS API bearer token (default: from .env or library config).
    """
    _tool_start = time.monotonic()
    try:
        cfg = _build_config(
            library=library,
            source_dir=source_dir,
            destination_dir=destination_dir,
            audio_file_extension=audio_file_extension,
            copy_instead_of_move=copy_instead_of_move,
            libation_folder_cleanup=libation_folder_cleanup,
            libation_file_locations_path=libation_file_locations_path,
            enable_profanity_cleaning=enable_profanity_cleaning,
            purchased_how_long_ago=purchased_how_long_ago,
            abs_server_url=abs_server_url,
            abs_library_id=abs_library_id,
            abs_api_token=abs_api_token,
            libation_cli=libation_cli,
            asins=asins,
        )
    except ValueError as e:
        return _record_tool_result(
            "ingest_books",
            _tool_start,
            json.dumps({"success": False, "error": str(e)}),
            success=False,
        )
    results = {}

    LOGGER.info("Step 1/6: Scanning Audible library")
    results["scan"] = step_scan(cfg)

    LOGGER.info("Step 2/6: Downloading books")
    results["download"] = step_download(cfg)

    LOGGER.info("Step 3/6: Exporting library metadata")
    results["export"] = step_export(cfg)

    LOGGER.info("Step 4/6: Organizing files")
    organize_result = step_organize(cfg)
    results["organize"] = {
        k: v for k, v in organize_result.items() if not k.startswith("_")
    }
    book_list = organize_result.get("_book_list", [])

    if book_list:
        LOGGER.info("Step 5/6: Scanning ABS")
        results["scan_abs"] = step_scan_abs(cfg)

        LOGGER.info("Step 6/6: Matching in ABS")
        results["match"] = step_match(cfg, book_list=book_list)
    else:
        results["scan_abs"] = "skipped (no new books)"
        results["match"] = "skipped (no new books)"

    return _record_tool_result("ingest_books", _tool_start, json.dumps(results))


@mcp.tool()
def get_status(
    library: str | None = None,
    source_dir: str | None = None,
    destination_dir: str | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Get current pipeline status and configuration summary.

    Args:
        library: Library name from libraries.yaml. Shows that library's config.
        source_dir: Libation books directory (default: from .env).
        destination_dir: ABS audiobooks directory / NFS mount (default: from .env or library config).
        abs_server_url: ABS server URL (default: from .env or library config).
        abs_library_id: ABS library UUID (default: from .env or library config).
        abs_api_token: ABS API bearer token (default: from .env or library config).
    """
    _tool_start = time.monotonic()
    lib = _resolve_library(library)
    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY", "(not set)")
    dest = destination_dir or lib["destination_dir"] or "(not set)"
    url = abs_server_url or lib["abs_server_url"]
    lib_id = abs_library_id or lib["library_id"]

    status = {
        "library": lib["name"] or "(default)",
        "media_type": lib["media_type"],
        "source_dir": src,
        "destination_dir": dest,
        "abs_server": url or "(not set)",
        "abs_library_id": lib_id or "(not set)",
        "download_engine": "Libation",
    }

    if url:
        try:
            resp = requests.get(f"{url}/status", timeout=5)
            status["abs_status"] = (
                resp.json() if resp.ok else f"HTTP {resp.status_code}"
            )
        except Exception as e:
            status["abs_status"] = f"unreachable: {e}"
    else:
        status["abs_status"] = "not configured"

    return _record_tool_result("get_status", _tool_start, json.dumps(status))


@mcp.tool()
def list_abs_library(
    library: str | None = None,
    query: str | None = None,
    title: str | None = None,
    author: str | None = None,
    series: str | None = None,
    limit: int = 0,
    offset: int = 0,
    sort_by: str | None = None,
    refresh: bool = False,
    cache_max_age_seconds: int = 3600,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """List items in an AudioBookShelf library with low-token pagination.

    Server-side cache stores full ABS response and this tool only returns
    compact, filtered slices.

    Args:
        library: Library name from libraries.yaml (e.g. 'kids', 'adult').
        query: Substring search across title/author/series.
        title: Title substring filter.
        author: Author substring filter.
        series: Series substring filter.
        limit: Max results to return. With limit=0, returns all matches.
        offset: Number of results to skip from filtered set.
        sort_by: Sort field: 'title', 'author', 'series', 'duration', 'added_at'.
        refresh: Force refresh cache from ABS API before query.
        cache_max_age_seconds: Auto-refresh cache if older than this many seconds.
        abs_server_url: Override ABS server URL.
        abs_library_id: Override ABS library ID.
        abs_api_token: Override ABS API token.
    """
    _tool_start = time.monotonic()
    lib = _resolve_library(library)
    url = abs_server_url or lib["abs_server_url"]
    lib_id = abs_library_id or lib["library_id"]
    token = abs_api_token or lib["abs_api_token"]
    cache_path = _abs_cache_path(lib["name"], lib_id)
    try:
        raw_items, cache_meta = _load_or_refresh_abs_cache(
            cache_path=cache_path,
            refresh=refresh,
            max_age_seconds=max(cache_max_age_seconds, 60),
            url=url,
            library_id=lib_id,
            token=token,
            library_name=lib["name"],
        )
    except Exception as e:
        return _record_tool_result(
            "list_abs_library",
            _tool_start,
            json.dumps({"error": "cache_refresh_failed", "detail": str(e)}),
            success=False,
        )

    flattened = [_flatten_abs_item(item) for item in raw_items]
    filtered = PARSER.filter_abs_items(flattened, query, title, author, series)
    sorted_items = PARSER.sort_abs_items(filtered, sort_by)
    filters_present = any([query, title, author, series])
    paged, effective_limit, safe_offset, next_offset, paginated = (
        PARSER.select_output_slice(
            sorted_items,
            limit,
            offset,
            filters_present=filters_present,
        )
    )
    return _record_tool_result(
        "list_abs_library",
        _tool_start,
        json.dumps(
            {
                "library": lib["name"] or "(default)",
                "cache": {
                    "path": str(cache_path),
                    "refreshed": cache_meta["refreshed"],
                    "fetched_at": cache_meta["fetched_at"],
                },
                "total_in_library": len(flattened),
                "total_matches": len(sorted_items),
                "limit": effective_limit,
                "offset": safe_offset,
                "next_offset": next_offset,
                "paginated": paginated,
                "items": paged,
            }
        ),
    )


@mcp.tool()
def search_abs_library(
    query: str,
    library: str | None = None,
    limit: int = 0,
    offset: int = 0,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Search an ABS library directly via /search endpoint with compact output."""
    _tool_start = time.monotonic()
    lib = _resolve_library(library)
    url = abs_server_url or lib["abs_server_url"]
    lib_id = abs_library_id or lib["library_id"]
    token = abs_api_token or lib["abs_api_token"]
    resp = requests.get(
        f"{url}/api/libraries/{lib_id}/search",
        headers=_abs_headers(token),
        params={"q": query, "limit": 0},
        timeout=30,
    )
    if not resp.ok:
        return _record_tool_result(
            "search_abs_library",
            _tool_start,
            json.dumps({"error": f"HTTP {resp.status_code}", "body": resp.text}),
            success=False,
        )

    payload = resp.json()
    if isinstance(payload, dict):
        raw_results = (
            payload.get("book", [])
            or payload.get("results", [])
            or payload.get("items", [])
        )
    elif isinstance(payload, list):
        raw_results = payload
    else:
        raw_results = []

    flattened = [
        _flatten_abs_item(item) for item in raw_results if isinstance(item, dict)
    ]
    paged, effective_limit, safe_offset, next_offset, paginated = (
        PARSER.select_output_slice(
            flattened,
            limit,
            offset,
            filters_present=True,
        )
    )
    return _record_tool_result(
        "search_abs_library",
        _tool_start,
        json.dumps(
            {
                "library": lib["name"] or "(default)",
                "query": query,
                "total_matches": len(flattened),
                "limit": effective_limit,
                "offset": safe_offset,
                "next_offset": next_offset,
                "paginated": paginated,
                "items": paged,
            }
        ),
    )


def _extract_series_names(item: dict) -> str:
    """Extract series names from an ABS library item."""
    series_list = item.get("media", {}).get("metadata", {}).get("series", [])
    if isinstance(series_list, list):
        return ", ".join(s.get("name", "") for s in series_list if s.get("name"))
    return ""


@mcp.tool()
def get_source_status(
    source_dir: str | None = None,
    destination_dir: str | None = None,
    library: str | None = None,
    detail: bool = False,
) -> str:
    """Inspect the Libation source and ABS destination directories.

    Shows file counts, extensions present, total sizes, and individual
    book folders. Use to verify downloads completed and detect extension
    mismatches before calling organize_books.

    Args:
        source_dir: Libation books directory (default: from .env).
        destination_dir: ABS audiobooks directory (default: from library config).
        library: Library name to resolve destination_dir.
        detail: Include per-folder file lists for destination when true.
    """
    _tool_start = time.monotonic()
    lib = _resolve_library(library)
    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    dest = destination_dir or lib["destination_dir"]

    result: dict = {}
    result["source"] = _scan_directory(src, "source", include_folders=True)
    if dest:
        result["destination"] = _scan_directory(
            dest, "destination", include_folders=detail
        )
        result["destination"]["detail"] = detail
    return _record_tool_result("get_source_status", _tool_start, json.dumps(result))


def _summarize_metric_records(records: list[dict]) -> dict:
    """Build per-tool aggregate summary for a metrics record list."""
    return ToolMetricsRecorder._aggregate(records)


@mcp.tool()
def get_tool_metrics(limit: int = 10) -> str:
    """Return recent in-memory response efficiency metrics."""
    safe_limit = max(min(limit, 50), 1)
    records = METRICS.get_recent(safe_limit)
    return json.dumps(
        {
            "limit": safe_limit,
            "records": records,
            "summary": _summarize_metric_records(records),
        }
    )


@mcp.tool()
def query_tool_metrics_history(
    tool_name: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Query persisted JSONL response efficiency metrics with filters."""
    records, summary = METRICS.query_history(
        tool_name=tool_name,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return json.dumps(
        {
            "filters": {
                "tool_name": tool_name,
                "since": since,
                "until": until,
                "limit": max(min(limit, 500), 1),
                "offset": max(offset, 0),
            },
            "records": records,
            "summary": summary,
        }
    )


def _scan_directory(dir_path: str, label: str, include_folders: bool = True) -> dict:
    """Scan a directory for audio book folders, files, and extensions."""
    base = Path(dir_path)
    info: dict = {"path": dir_path, "exists": base.is_dir()}
    if not base.is_dir():
        return info

    audio_exts = {".m4b", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wma", ".aac"}
    ext_counts: dict[str, int] = {}
    total_size = 0
    folders: list[dict] = []

    if include_folders:
        for child in sorted(base.iterdir()):
            if child.is_dir():
                folder_info = _scan_book_folder(child, audio_exts, ext_counts)
                total_size += folder_info.get("size", 0)
                folders.append(folder_info)
    else:
        for file_path in base.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in audio_exts:
                total_size += file_path.stat().st_size
                ext = file_path.suffix.lower()
                ext_counts[ext] = ext_counts.get(ext, 0) + 1

    info["folder_count"] = len([child for child in base.iterdir() if child.is_dir()])
    info["extensions"] = ext_counts
    info["total_audio_size_mb"] = round(total_size / (1024 * 1024), 1)
    if include_folders:
        info["folders"] = folders
    return info


def _scan_book_folder(
    folder: Path,
    audio_exts: set[str],
    ext_counts: dict[str, int],
) -> dict:
    """Scan a single book folder for audio files."""
    audio_files = []
    folder_size = 0
    for f in folder.rglob("*"):
        if f.is_file() and f.suffix.lower() in audio_exts:
            audio_files.append(f.name)
            folder_size += f.stat().st_size
            ext = f.suffix.lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
    return {
        "name": folder.name,
        "audio_files": audio_files,
        "size": folder_size,
        "size_mb": round(folder_size / (1024 * 1024), 1),
    }


# ---------------------------------------------------------------------------
# Podcast tools -- Layer 1: ABS-native
# ---------------------------------------------------------------------------


@mcp.tool()
def search_podcasts(
    term: str,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Search for podcasts via iTunes (through the ABS API).

    Args:
        term: Search term (e.g. 'Under The Hood', 'Jupiter Broadcasting').
        abs_server_url: Override ABS server URL.
        abs_api_token: Override ABS API token.
    """
    _tool_start = time.monotonic()
    lib = _resolve_library(None)
    url = abs_server_url or lib["abs_server_url"]
    token = abs_api_token or lib["abs_api_token"]

    resp = requests.get(
        f"{url}/api/search/podcast",
        headers=_abs_headers(token),
        params={"term": term},
        timeout=15,
    )
    if not resp.ok:
        return _record_tool_result(
            "search_podcasts",
            _tool_start,
            json.dumps({"error": f"HTTP {resp.status_code}", "body": resp.text}),
            success=False,
        )

    podcasts = resp.json()
    summary = [
        {
            "id": p.get("id"),
            "title": p.get("title", ""),
            "artist": p.get("artistName", ""),
            "feed_url": p.get("feedUrl", ""),
            "genres": p.get("genres", []),
            "track_count": p.get("trackCount", 0),
            "description": (p.get("description") or "")[:200],
        }
        for p in podcasts
    ]
    return _record_tool_result("search_podcasts", _tool_start, json.dumps(summary))


@mcp.tool()
def add_podcast(
    feed_url: str,
    library: str | None = None,
    title: str | None = None,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Add a podcast to an ABS podcast library by RSS feed URL.

    ABS will subscribe to the feed and manage episode downloads.

    Args:
        feed_url: The podcast RSS feed URL.
        library: Podcast library name (e.g. 'adult_podcasts', 'kids_podcasts').
        title: Podcast title (auto-detected from feed if omitted).
        abs_server_url: Override ABS server URL.
        abs_api_token: Override ABS API token.
    """
    _tool_start = time.monotonic()
    lib = _resolve_library(library)
    url = abs_server_url or lib["abs_server_url"]
    token = abs_api_token or lib["abs_api_token"]
    folder_id = lib["folder_id"]

    if not folder_id:
        lib_resp = requests.get(
            f"{url}/api/libraries/{lib['library_id']}",
            headers=_abs_headers(token),
            timeout=10,
        )
        if lib_resp.ok:
            folders = lib_resp.json().get("folders", [])
            folder_id = folders[0]["id"] if folders else ""

    feed_title = title or ""
    if not feed_title:
        parsed = feedparser.parse(feed_url)
        feed_title = parsed.feed.get("title", "Unknown Podcast")

    safe_name = re.sub(r'[<>:"/\\|?*]', "_", feed_title)
    lib_resp2 = requests.get(
        f"{url}/api/libraries/{lib['library_id']}",
        headers=_abs_headers(token),
        timeout=10,
    )
    base_path = "/"
    if lib_resp2.ok:
        folders = lib_resp2.json().get("folders", [])
        if folders:
            base_path = folders[0]["fullPath"]

    podcast_path = f"{base_path}/{safe_name}"

    payload = {
        "libraryId": lib["library_id"],
        "folderId": folder_id,
        "path": podcast_path,
        "media": {
            "metadata": {
                "title": feed_title,
                "feedUrl": feed_url,
            }
        },
    }

    resp = requests.post(
        f"{url}/api/podcasts",
        headers={**_abs_headers(token), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if resp.ok:
        data = resp.json()
        return _record_tool_result(
            "add_podcast",
            _tool_start,
            json.dumps(
                {
                    "success": True,
                    "id": data.get("id"),
                    "title": data.get("media", {}).get("metadata", {}).get("title"),
                }
            ),
            success=True,
        )
    return _record_tool_result(
        "add_podcast",
        _tool_start,
        json.dumps({"success": False, "status": resp.status_code, "body": resp.text}),
        success=False,
    )


@mcp.tool()
def list_podcasts(
    library: str | None = None,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """List all podcasts in a podcast library.

    Args:
        library: Podcast library name (e.g. 'adult_podcasts').
        abs_server_url: Override ABS server URL.
        abs_api_token: Override ABS API token.
    """
    _tool_start = time.monotonic()
    lib = _resolve_library(library)
    url = abs_server_url or lib["abs_server_url"]
    token = abs_api_token or lib["abs_api_token"]

    resp = requests.get(
        f"{url}/api/libraries/{lib['library_id']}/items",
        headers=_abs_headers(token),
        params={"limit": 500},
        timeout=15,
    )
    if not resp.ok:
        return _record_tool_result(
            "list_podcasts",
            _tool_start,
            json.dumps({"error": f"HTTP {resp.status_code}"}),
            success=False,
        )

    items = resp.json().get("results", [])
    summary = [
        {
            "id": item["id"],
            "title": item.get("media", {}).get("metadata", {}).get("title", ""),
            "author": item.get("media", {}).get("metadata", {}).get("author", ""),
            "episode_count": len(item.get("media", {}).get("episodes", [])),
        }
        for item in items
    ]
    return _record_tool_result("list_podcasts", _tool_start, json.dumps(summary))


@mcp.tool()
def get_podcast_episodes(
    podcast_id: str,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Get episodes for a specific podcast in ABS.

    Args:
        podcast_id: The ABS library item ID for the podcast.
        abs_server_url: Override ABS server URL.
        abs_api_token: Override ABS API token.
    """
    _tool_start = time.monotonic()
    lib = _resolve_library(None)
    url = abs_server_url or lib["abs_server_url"]
    token = abs_api_token or lib["abs_api_token"]

    resp = requests.get(
        f"{url}/api/items/{podcast_id}",
        headers=_abs_headers(token),
        timeout=15,
    )
    if not resp.ok:
        return _record_tool_result(
            "get_podcast_episodes",
            _tool_start,
            json.dumps({"error": f"HTTP {resp.status_code}"}),
            success=False,
        )

    data = resp.json()
    episodes = data.get("media", {}).get("episodes", [])
    summary = [
        {
            "id": ep.get("id", ""),
            "title": ep.get("title", ""),
            "published_at": ep.get("publishedAt", 0),
            "duration": ep.get("duration", 0),
            "size": ep.get("size", 0),
        }
        for ep in episodes[:50]
    ]
    return _record_tool_result(
        "get_podcast_episodes",
        _tool_start,
        json.dumps(
            {
                "podcast_title": data.get("media", {})
                .get("metadata", {})
                .get("title", ""),
                "total_episodes": len(episodes),
                "episodes": summary,
            }
        ),
    )


@mcp.tool()
def download_podcast_episodes(
    podcast_id: str,
    limit: int = 3,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Check for and download new episodes for a podcast via ABS.

    ABS fetches the RSS feed, finds new episodes, and downloads them.

    Args:
        podcast_id: The ABS library item ID for the podcast.
        limit: Max number of new episodes to download (0 = all).
        abs_server_url: Override ABS server URL.
        abs_api_token: Override ABS API token.
    """
    _tool_start = time.monotonic()
    lib = _resolve_library(None)
    url = abs_server_url or lib["abs_server_url"]
    token = abs_api_token or lib["abs_api_token"]

    resp = requests.get(
        f"{url}/api/podcasts/{podcast_id}/checknew",
        headers=_abs_headers(token),
        params={"limit": limit},
        timeout=60,
    )
    if not resp.ok:
        return _record_tool_result(
            "download_podcast_episodes",
            _tool_start,
            json.dumps({"error": f"HTTP {resp.status_code}", "body": resp.text}),
            success=False,
        )

    data = resp.json()
    episodes = data.get("episodes", [])
    return _record_tool_result(
        "download_podcast_episodes",
        _tool_start,
        json.dumps(
            {
                "new_episodes_found": len(episodes),
                "episodes": [
                    {"title": ep.get("title", ""), "published": ep.get("pubDate", "")}
                    for ep in episodes
                ],
            }
        ),
    )


# ---------------------------------------------------------------------------
# Podcast tools -- Layer 2: RSS feed direct download
# ---------------------------------------------------------------------------

ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
APPLE_PODCAST_ID_RE = re.compile(r"/id(\d+)")


def _extract_apple_podcast_id(apple_url: str) -> str | None:
    """Extract numeric podcast ID from an Apple Podcasts URL."""
    match = APPLE_PODCAST_ID_RE.search(apple_url)
    return match.group(1) if match else None


def _itunes_feed_url(podcast_id: str) -> str | None:
    """Look up an RSS feed URL via the iTunes Lookup API."""
    resp = requests.get(
        ITUNES_LOOKUP_URL,
        params={"id": podcast_id, "entity": "podcast"},
        timeout=10,
    )
    if resp.ok:
        results = resp.json().get("results", [])
        if results:
            return results[0].get("feedUrl")
    return None


@mcp.tool()
def fetch_podcast_feed(
    search_term: str | None = None,
    apple_url: str | None = None,
    feed_url: str | None = None,
    max_episodes: int = 20,
) -> str:
    """Find and parse a podcast RSS feed, returning episode download URLs.

    Provide ONE of: search_term, apple_url, or feed_url.
    - search_term: searches iTunes, finds the RSS feed URL, parses it.
    - apple_url: extracts the podcast ID, looks up the feed via iTunes, parses it.
    - feed_url: parses the RSS feed directly.

    Args:
        search_term: Podcast name to search iTunes for.
        apple_url: Apple Podcasts URL (e.g. https://podcasts.apple.com/.../id410937196).
        feed_url: Direct RSS feed URL.
        max_episodes: Maximum number of episodes to return (default 20).
    """
    _tool_start = time.monotonic()
    resolved_feed = feed_url

    if not resolved_feed and apple_url:
        pid = _extract_apple_podcast_id(apple_url)
        if pid:
            resolved_feed = _itunes_feed_url(pid)
        if not resolved_feed:
            return _record_tool_result(
                "fetch_podcast_feed",
                _tool_start,
                json.dumps({"error": f"Could not extract feed URL from {apple_url}"}),
                success=False,
            )

    if not resolved_feed and search_term:
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": search_term, "media": "podcast", "limit": 5},
            timeout=10,
        )
        if resp.ok:
            results = resp.json().get("results", [])
            if results:
                resolved_feed = results[0].get("feedUrl")
        if not resolved_feed:
            return _record_tool_result(
                "fetch_podcast_feed",
                _tool_start,
                json.dumps({"error": f"No podcast feed found for '{search_term}'"}),
                success=False,
            )

    if not resolved_feed:
        return _record_tool_result(
            "fetch_podcast_feed",
            _tool_start,
            json.dumps({"error": "Provide search_term, apple_url, or feed_url"}),
            success=False,
        )

    parsed = feedparser.parse(resolved_feed)
    if parsed.bozo and not parsed.entries:
        return _record_tool_result(
            "fetch_podcast_feed",
            _tool_start,
            json.dumps({"error": f"Failed to parse feed: {parsed.bozo_exception}"}),
            success=False,
        )

    episodes = []
    for entry in parsed.entries[:max_episodes]:
        enc = entry.enclosures[0] if entry.enclosures else {}
        episodes.append(
            {
                "title": entry.get("title", ""),
                "published": entry.get("published", ""),
                "duration": entry.get("itunes_duration", ""),
                "download_url": enc.get("href", ""),
                "mime_type": enc.get("type", ""),
                "size_bytes": enc.get("length", ""),
            }
        )

    return _record_tool_result(
        "fetch_podcast_feed",
        _tool_start,
        json.dumps(
            {
                "feed_url": resolved_feed,
                "podcast_title": parsed.feed.get("title", ""),
                "total_episodes_in_feed": len(parsed.entries),
                "episodes": episodes,
            }
        ),
    )


def _sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filesystems."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")


@mcp.tool()
def download_podcast_files(
    urls: list[str],
    podcast_name: str,
    library: str | None = None,
    episode_names: list[str] | None = None,
    trigger_scan: bool = True,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Download audio files from URLs into an ABS podcast directory.

    Use this when ABS cannot subscribe to a podcast natively. Place audio
    files in the correct directory structure and optionally trigger a scan.

    If the LLM found download URLs via browser tools, pass them here.

    Args:
        urls: List of audio file download URLs.
        podcast_name: Podcast name (used for the folder name in ABS).
        library: Podcast library name (e.g. 'adult_podcasts').
        episode_names: Optional display names for each URL (same order). Used for filenames.
        trigger_scan: Whether to trigger an ABS library scan after downloading.
        abs_server_url: Override ABS server URL.
        abs_api_token: Override ABS API token.
    """
    _tool_start = time.monotonic()
    lib = _resolve_library(library)
    dest_base = lib["destination_dir"]
    if not dest_base:
        return _record_tool_result(
            "download_podcast_files",
            _tool_start,
            json.dumps({"error": "No destination_dir configured for this library"}),
            success=False,
        )

    safe_podcast = _sanitize_filename(podcast_name)
    podcast_dir = Path(dest_base) / safe_podcast
    podcast_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    errors = []
    names = episode_names or []

    for i, url_item in enumerate(urls):
        try:
            resp = requests.get(url_item, stream=True, timeout=300)
            resp.raise_for_status()

            if i < len(names) and names[i]:
                fname = _sanitize_filename(names[i])
            else:
                fname = Path(url_item.split("?")[0]).stem
                fname = _sanitize_filename(fname)

            content_type = resp.headers.get("Content-Type", "")
            if "mpeg" in content_type or url_item.endswith(".mp3"):
                ext = ".mp3"
            elif "mp4" in content_type or "m4a" in content_type:
                ext = ".m4a"
            else:
                ext = ".mp3"

            if not fname.endswith(ext):
                fname += ext

            dest_path = podcast_dir / fname
            size = 0
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    size += len(chunk)

            downloaded.append({"file": str(dest_path), "size": size})
            LOGGER.info("Downloaded %s (%d bytes)", dest_path, size)
        except Exception as e:
            errors.append({"url": url_item, "error": str(e)})
            LOGGER.error("Failed to download %s: %s", url_item, e)

    scan_result = None
    if trigger_scan and downloaded:
        url = abs_server_url or lib["abs_server_url"]
        token = abs_api_token or lib["abs_api_token"]
        lib_id = lib["library_id"]
        if url and lib_id and token:
            scan_resp = scan_library_for_books(url, lib_id, token)
            scan_result = {
                "success": scan_resp.ok,
                "status_code": scan_resp.status_code,
            }

    return _record_tool_result(
        "download_podcast_files",
        _tool_start,
        json.dumps(
            {
                "downloaded": len(downloaded),
                "errors": len(errors),
                "files": downloaded,
                "error_details": errors,
                "scan_result": scan_result,
            }
        ),
        success=len(errors) == 0,
    )


if __name__ == "__main__":
    transport = _env("MCP_TRANSPORT", "streamable-http")
    LOGGER.info(
        "Starting Audiobook Ingestion MCP (%s, path=%s)",
        transport,
        mcp.settings.streamable_http_path,
    )
    mcp.run(transport=transport)
