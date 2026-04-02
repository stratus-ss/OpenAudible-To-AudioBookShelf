#!/usr/bin/env python3
"""Audiobook Ingestion MCP Server.

Exposes tools for downloading audiobooks via Libation, organizing them,
and ingesting them into AudioBookShelf. Supports multiple libraries
(including podcasts) via a YAML library registry.

Designed for SSE transport to be consumed by Moltis or other MCP clients.

Calls directly into the existing openaudible_to_ab.py pipeline and
modules/ functions -- no duplication of logic.
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
PROJECT_ROOT = str(ABS_MCP_DIR.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


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

from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import EventStore
from mcp.types import JSONRPCMessage

from modules.audio_bookshelf import scan_library_for_books
from modules.config import Config
from modules.utils import generate_libation_json
from openaudible_to_ab import (
    step_scan, step_download, step_export, step_organize,
    step_scan_abs, step_match,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger("audiobook-ingestion-mcp")


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
        "- list_library — filter by status/author/title/duration to find specific books\n"
        "- get_source_status — inspect source/destination directories (file counts, extensions)\n"
        "- list_abs_library — verify what's currently in AudioBookShelf\n"
        "- delete_library_items — remove items with optional cleanup_files to delete disk files\n\n"
        "Always specify library= to target the correct ABS instance (e.g. 'adult', 'kids')."
    ),
    event_store=InMemoryEventStore(),
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8765")),
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _r(override: str | None, env_key: str, default: str = "") -> str:
    """Resolve a value: per-call override > env var > default."""
    if override is not None:
        return override
    return os.environ.get(env_key, default)


def _log_buffer() -> io.StringIO:
    return io.StringIO()


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
        "destination_dir": lib_entry.get("destination_dir", _env("DESTINATION_BOOK_DIRECTORY")),
        "media_type": lib_entry.get("media_type", "book"),
        "folder_id": lib_entry.get("folder_id", ""),
        "abs_server_url": lib_entry.get("abs_server_url", _env("ABS_SERVER_URL")),
        "abs_api_token": lib_entry.get("abs_api_token", _env("ABS_API_TOKEN")),
    }


def _abs_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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
        copy_instead_of_move if copy_instead_of_move is not None
        else _env("COPY_INSTEAD_OF_MOVE", "false").lower() == "true"
    )
    cfg.libation_folder_cleanup = (
        libation_folder_cleanup if libation_folder_cleanup is not None
        else _env("LIBATION_FOLDER_CLEANUP", "false").lower() == "true"
    )
    cfg.libation_file_locations_path = _r(libation_file_locations_path, "LIBATION_FILE_LOCATIONS_PATH")
    cfg.enable_profanity_cleaning = (
        enable_profanity_cleaning if enable_profanity_cleaning is not None
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
    return cfg


@mcp.tool()
def list_libraries() -> str:
    """List all configured libraries with their names, types, and IDs.

    Use this to discover available libraries before calling other tools.
    Libraries of media_type 'book' support audiobook ingestion tools.
    Libraries of media_type 'podcast' support podcast tools.
    """
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
    return json.dumps(result, indent=2)


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
    title: str | None = None,
    max_duration: int | None = None,
    min_duration: int | None = None,
    limit: int = 0,
    sort_by: str | None = None,
) -> str:
    """List books in the Audible library from Libation's export with filtering.

    Args:
        source_dir: Libation books directory (default: from .env).
        status: Filter by BookStatus (e.g. 'NotLiberated', 'Liberated').
        author: Filter by author name (case-insensitive substring match).
        title: Filter by title (case-insensitive substring match).
        max_duration: Only books shorter than this many minutes.
        min_duration: Only books longer than this many minutes.
        limit: Max results to return (0 = all).
        sort_by: Sort field: 'duration', 'title', 'author', 'date_added' (default: none).
    """
    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    json_path = str(Path(src) / "libation.json")

    if not Path(json_path).exists():
        log = _log_buffer()
        generate_libation_json(json_path, log)

    with open(json_path) as f:
        books = json.load(f)

    filtered = _filter_library_books(books, status, author, title, min_duration, max_duration)

    if sort_by:
        filtered = _sort_library_books(filtered, sort_by)

    if limit > 0:
        filtered = filtered[:limit]

    summary = [
        {
            "asin": b.get("AudibleProductId", ""),
            "title": b.get("Title", ""),
            "author": b.get("AuthorNames", ""),
            "series": b.get("SeriesNames", ""),
            "duration_minutes": b.get("LengthInMinutes", 0),
            "status": b.get("BookStatus", ""),
            "date_added": b.get("DateAdded", ""),
        }
        for b in filtered
    ]
    return json.dumps({"total": len(summary), "books": summary}, indent=2)


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
    cfg = _build_config(libation_cli=libation_cli)
    result = step_scan(cfg)
    return json.dumps(result, indent=2)


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
    cli = libation_cli or _env("LIBATION_CLI", "libationcli")
    flag = "--not-downloaded" if status == "not-downloaded" else "--downloaded"
    cmd = [cli, "set-status", flag]
    if force:
        cmd.append("--force")
    cmd.extend(asins)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return json.dumps({
        "success": result.returncode == 0,
        "asins": asins,
        "status_set": status,
        "force": force,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }, indent=2)


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
    cfg = _build_config(libation_cli=libation_cli, asins=asins)
    result = step_download(cfg)
    return json.dumps(result, indent=2)


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
    cfg = _build_config(source_dir=source_dir, libation_cli=libation_cli)
    result = step_export(cfg)
    return json.dumps(result, indent=2)


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
    user_specified_ext = audio_file_extension is not None

    cfg = _build_config(
        library=library, source_dir=source_dir, destination_dir=destination_dir,
        audio_file_extension=audio_file_extension, copy_instead_of_move=copy_instead_of_move,
        libation_folder_cleanup=libation_folder_cleanup,
        libation_file_locations_path=libation_file_locations_path,
        enable_profanity_cleaning=enable_profanity_cleaning,
        purchased_how_long_ago=purchased_how_long_ago,
    )

    if not user_specified_ext:
        detected = _detect_audio_extension(Path(cfg.source_audio_book_directory))
        if detected and detected != cfg.audio_file_extension:
            LOGGER.info(
                "Auto-detected audio extension %s (configured: %s)",
                detected, cfg.audio_file_extension,
            )
            cfg.audio_file_extension = detected

    result = step_organize(cfg)
    clean = {k: v for k, v in result.items() if not k.startswith("_")}
    clean["audio_file_extension"] = cfg.audio_file_extension
    return json.dumps(clean, indent=2)


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
    cfg = _build_config(
        library=library, abs_server_url=abs_server_url,
        abs_library_id=abs_library_id, abs_api_token=abs_api_token,
    )
    result = step_scan_abs(cfg, wait=wait)
    return json.dumps(result, indent=2)


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
        delete_all: If True, delete every item in the library (use with caution).
        cleanup_files: Also remove audio files from the destination and source directories.
        library: Library name from libraries.yaml (e.g. 'kids', 'adult').
        source_dir: Libation source directory to clean (default: from .env).
        abs_server_url: Override ABS server URL.
        abs_library_id: Override ABS library ID.
        abs_api_token: Override ABS API token.
    """
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
            items_data, lib["destination_dir"],
            _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY"),
        )

    output: dict = {"deleted": len(results), "results": results}
    if cleanup_files:
        output["files_cleaned"] = files_cleaned
    return json.dumps(output, indent=2)


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
    filenames = {Path(af.get("metadata", {}).get("filename", "")).stem.lower() for af in audio_files}
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
    cfg = _build_config(
        library=library, abs_server_url=abs_server_url,
        abs_library_id=abs_library_id, abs_api_token=abs_api_token,
        purchased_how_long_ago=days_ago,
    )
    result = step_match(cfg, book_list=book_list)
    return json.dumps(result, indent=2)


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
    cfg = _build_config(
        library=library, source_dir=source_dir, destination_dir=destination_dir,
        audio_file_extension=audio_file_extension, copy_instead_of_move=copy_instead_of_move,
        libation_folder_cleanup=libation_folder_cleanup,
        libation_file_locations_path=libation_file_locations_path,
        enable_profanity_cleaning=enable_profanity_cleaning,
        purchased_how_long_ago=purchased_how_long_ago,
        abs_server_url=abs_server_url, abs_library_id=abs_library_id,
        abs_api_token=abs_api_token, libation_cli=libation_cli, asins=asins,
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
    results["organize"] = {k: v for k, v in organize_result.items() if not k.startswith("_")}
    book_list = organize_result.get("_book_list", [])

    if book_list:
        LOGGER.info("Step 5/6: Scanning ABS")
        results["scan_abs"] = step_scan_abs(cfg)

        LOGGER.info("Step 6/6: Matching in ABS")
        results["match"] = step_match(cfg, book_list=book_list)
    else:
        results["scan_abs"] = "skipped (no new books)"
        results["match"] = "skipped (no new books)"

    return json.dumps(results, indent=2)


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
            status["abs_status"] = resp.json() if resp.ok else f"HTTP {resp.status_code}"
        except Exception as e:
            status["abs_status"] = f"unreachable: {e}"
    else:
        status["abs_status"] = "not configured"

    return json.dumps(status, indent=2)


@mcp.tool()
def list_abs_library(
    library: str | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """List all items currently in an AudioBookShelf library.

    Use after scan_audiobookshelf or match_audiobookshelf to verify
    books are present with correct metadata.

    Args:
        library: Library name from libraries.yaml (e.g. 'kids', 'adult').
        abs_server_url: Override ABS server URL.
        abs_library_id: Override ABS library ID.
        abs_api_token: Override ABS API token.
    """
    lib = _resolve_library(library)
    url = abs_server_url or lib["abs_server_url"]
    lib_id = abs_library_id or lib["library_id"]
    token = abs_api_token or lib["abs_api_token"]
    headers = _abs_headers(token)

    resp = requests.get(
        f"{url}/api/libraries/{lib_id}/items",
        headers=headers,
        params={"limit": 0, "sort": "addedAt"},
        timeout=30,
    )
    if not resp.ok:
        return json.dumps({"error": f"HTTP {resp.status_code}", "body": resp.text})

    items = resp.json().get("results", [])
    summary = [
        {
            "id": item["id"],
            "title": item.get("media", {}).get("metadata", {}).get("title", ""),
            "author": item.get("media", {}).get("metadata", {}).get("authorName", ""),
            "series": _extract_series_names(item),
            "duration": round(item.get("media", {}).get("duration", 0) / 60, 1),
            "added_at": item.get("addedAt", ""),
            "has_audio": bool(item.get("media", {}).get("audioFiles")),
        }
        for item in items
    ]
    return json.dumps({"total": len(summary), "items": summary}, indent=2)


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
) -> str:
    """Inspect the Libation source and ABS destination directories.

    Shows file counts, extensions present, total sizes, and individual
    book folders. Use to verify downloads completed and detect extension
    mismatches before calling organize_books.

    Args:
        source_dir: Libation books directory (default: from .env).
        destination_dir: ABS audiobooks directory (default: from library config).
        library: Library name to resolve destination_dir.
    """
    lib = _resolve_library(library)
    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    dest = destination_dir or lib["destination_dir"]

    result: dict = {}
    result["source"] = _scan_directory(src, "source")
    if dest:
        result["destination"] = _scan_directory(dest, "destination")
    return json.dumps(result, indent=2)


def _scan_directory(dir_path: str, label: str) -> dict:
    """Scan a directory for audio book folders, files, and extensions."""
    base = Path(dir_path)
    info: dict = {"path": dir_path, "exists": base.is_dir()}
    if not base.is_dir():
        return info

    audio_exts = {".m4b", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wma", ".aac"}
    folders = []
    ext_counts: dict[str, int] = {}
    total_size = 0

    for child in sorted(base.iterdir()):
        if child.is_dir():
            folder_info = _scan_book_folder(child, audio_exts, ext_counts)
            total_size += folder_info.get("size", 0)
            folders.append(folder_info)

    info["folder_count"] = len(folders)
    info["extensions"] = ext_counts
    info["total_audio_size_mb"] = round(total_size / (1024 * 1024), 1)
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
        return json.dumps({"error": f"HTTP {resp.status_code}", "body": resp.text})

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
    return json.dumps(summary, indent=2)


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
        return json.dumps({
            "success": True,
            "id": data.get("id"),
            "title": data.get("media", {}).get("metadata", {}).get("title"),
        }, indent=2)
    return json.dumps({"success": False, "status": resp.status_code, "body": resp.text})


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
        return json.dumps({"error": f"HTTP {resp.status_code}"})

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
    return json.dumps(summary, indent=2)


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
    lib = _resolve_library(None)
    url = abs_server_url or lib["abs_server_url"]
    token = abs_api_token or lib["abs_api_token"]

    resp = requests.get(
        f"{url}/api/items/{podcast_id}",
        headers=_abs_headers(token),
        timeout=15,
    )
    if not resp.ok:
        return json.dumps({"error": f"HTTP {resp.status_code}"})

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
    return json.dumps({
        "podcast_title": data.get("media", {}).get("metadata", {}).get("title", ""),
        "total_episodes": len(episodes),
        "episodes": summary,
    }, indent=2)


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
        return json.dumps({"error": f"HTTP {resp.status_code}", "body": resp.text})

    data = resp.json()
    episodes = data.get("episodes", [])
    return json.dumps({
        "new_episodes_found": len(episodes),
        "episodes": [
            {"title": ep.get("title", ""), "published": ep.get("pubDate", "")}
            for ep in episodes
        ],
    }, indent=2)


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
    resolved_feed = feed_url

    if not resolved_feed and apple_url:
        pid = _extract_apple_podcast_id(apple_url)
        if pid:
            resolved_feed = _itunes_feed_url(pid)
        if not resolved_feed:
            return json.dumps({"error": f"Could not extract feed URL from {apple_url}"})

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
            return json.dumps({"error": f"No podcast feed found for '{search_term}'"})

    if not resolved_feed:
        return json.dumps({"error": "Provide search_term, apple_url, or feed_url"})

    parsed = feedparser.parse(resolved_feed)
    if parsed.bozo and not parsed.entries:
        return json.dumps({"error": f"Failed to parse feed: {parsed.bozo_exception}"})

    episodes = []
    for entry in parsed.entries[:max_episodes]:
        enc = entry.enclosures[0] if entry.enclosures else {}
        episodes.append({
            "title": entry.get("title", ""),
            "published": entry.get("published", ""),
            "duration": entry.get("itunes_duration", ""),
            "download_url": enc.get("href", ""),
            "mime_type": enc.get("type", ""),
            "size_bytes": enc.get("length", ""),
        })

    return json.dumps({
        "feed_url": resolved_feed,
        "podcast_title": parsed.feed.get("title", ""),
        "total_episodes_in_feed": len(parsed.entries),
        "episodes": episodes,
    }, indent=2)


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
    lib = _resolve_library(library)
    dest_base = lib["destination_dir"]
    if not dest_base:
        return json.dumps({"error": "No destination_dir configured for this library"})

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
            scan_result = {"success": scan_resp.ok, "status_code": scan_resp.status_code}

    return json.dumps({
        "downloaded": len(downloaded),
        "errors": len(errors),
        "files": downloaded,
        "error_details": errors,
        "scan_result": scan_result,
    }, indent=2)


if __name__ == "__main__":
    transport = _env("MCP_TRANSPORT", "streamable-http")
    LOGGER.info("Starting Audiobook Ingestion MCP (%s)", transport)
    mcp.run(transport=transport)
