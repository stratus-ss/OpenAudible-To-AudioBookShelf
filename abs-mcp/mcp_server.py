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

from modules.audio_bookshelf import (
    get_all_books,
    get_audio_bookshelf_recent_books,
    process_audio_books,
    scan_library_for_books,
)
from modules.audio_cleaner import AudioCleaner
from modules.utils import generate_libation_json
from openaudible_to_ab import move_audio_book_files

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger("audiobook-ingestion-mcp")

mcp = FastMCP(
    "Audiobook Ingestion",
    instructions=(
        "This server manages audiobook and podcast ingestion: "
        "downloading audiobooks from Audible via Libation, organizing files, "
        "importing into AudioBookShelf, and managing podcasts across "
        "multiple libraries (e.g. kids, adult, podcasts). "
        "Use list_libraries to see available libraries and their types."
    ),
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


def _build_audio_cleaner(
    log_file,
    enable: bool | None = None,
    copy_mode: bool | None = None,
) -> AudioCleaner | None:
    """Build an AudioCleaner if profanity cleaning is enabled."""
    enabled = enable if enable is not None else _env("ENABLE_PROFANITY_CLEANING", "false").lower() == "true"
    if not enabled:
        return None

    copy_val = copy_mode if copy_mode is not None else _env("COPY_INSTEAD_OF_MOVE", "false").lower() == "true"

    class _EnvConfig:
        working_directory = _env("WORKING_DIRECTORY", "/tmp/monkeyplug-cleaning")
        copy_instead_of_move = copy_val
        save_transcripts = _env("SAVE_TRANSCRIPTS", "true").lower() == "true"
        swears_file = _env("SWEARS_FILE", "")
        remote_whisper_url = _env("REMOTE_WHISPER_URL", "")
        timeout = int(_env("TIMEOUT", "600"))
        confidence_threshold = float(_env("CONFIDENCE_THRESHOLD", "0.70"))
        beep_mode = _env("BEEP_MODE", "false").lower() == "true"

    return AudioCleaner(_EnvConfig(), log_file)


def _run_libationcli(args: list[str], timeout: int = 600, cli_path: str | None = None) -> dict:
    cli = cli_path or _env("LIBATION_CLI", "libationcli")
    cmd = [cli] + args
    LOGGER.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


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


@mcp.tool()
def list_library(source_dir: str | None = None) -> str:
    """List all books in the Audible library from Libation's export.

    Args:
        source_dir: Libation books directory (default: from .env).
    """
    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    json_path = str(Path(src) / "libation.json")

    if not Path(json_path).exists():
        log = _log_buffer()
        generate_libation_json(json_path, log)

    with open(json_path) as f:
        books = json.load(f)

    summary = [
        {
            "asin": b.get("AudibleProductId", ""),
            "title": b.get("Title", ""),
            "author": b.get("AuthorNames", ""),
            "series": b.get("SeriesNames", ""),
            "date_added": b.get("DateAdded", ""),
        }
        for b in books
    ]
    return json.dumps(summary, indent=2)


@mcp.tool()
def download_books(
    asins: list[str] | None = None,
    force: bool = False,
    source_dir: str | None = None,
    libation_cli: str | None = None,
) -> str:
    """Download audiobooks from Audible via Libation CLI.

    Args:
        asins: Optional list of ASINs to download. If empty, downloads all new books.
        force: Force re-download even if already downloaded.
        source_dir: Libation books directory (default: from .env).
        libation_cli: Path to libationcli binary (default: from .env).
    """
    _run_libationcli(["scan"], cli_path=libation_cli)

    args = ["liberate"]
    if force:
        args.append("--force")
    if asins:
        args.extend(asins)
    result = _run_libationcli(args, timeout=3600, cli_path=libation_cli)

    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    json_path = str(Path(src) / "libation.json")
    _run_libationcli(["export", "--path", json_path, "--json"], cli_path=libation_cli)

    return json.dumps(result, indent=2)


@mcp.tool()
def process_books(
    purchased_how_long_ago: int = 7,
    library: str | None = None,
    source_dir: str | None = None,
    destination_dir: str | None = None,
    audio_file_extension: str | None = None,
    copy_instead_of_move: bool | None = None,
    libation_folder_cleanup: bool | None = None,
    libation_file_locations_path: str | None = None,
    enable_profanity_cleaning: bool | None = None,
) -> str:
    """Organize downloaded audiobooks into the destination directory structure.

    Reads the Libation JSON export, filters by purchase date, and moves/copies
    audio files into Author/Series/Title folder hierarchy.

    Args:
        purchased_how_long_ago: Process books purchased within this many days. 0 means all.
        library: Library name from libraries.yaml (e.g. 'kids', 'adult'). Resolves destination_dir.
        source_dir: Libation books directory (default: from .env).
        destination_dir: ABS audiobooks directory / NFS mount (default: from .env or library config).
        audio_file_extension: File extension to look for, e.g. '.m4b' (default: from .env).
        copy_instead_of_move: Copy files instead of moving (default: from .env).
        libation_folder_cleanup: Delete Libation source folders after move (default: from .env).
        libation_file_locations_path: Path to Libation FileLocationsV2.json (default: from .env).
        enable_profanity_cleaning: Enable monkeyplug profanity filtering (default: from .env).
    """
    lib = _resolve_library(library)
    log = _log_buffer()
    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    dest = destination_dir or lib["destination_dir"]
    ext = _r(audio_file_extension, "AUDIO_FILE_EXTENSION", ".m4b")
    copy_mode = copy_instead_of_move if copy_instead_of_move is not None else _env("COPY_INSTEAD_OF_MOVE", "false").lower() == "true"
    cleanup = libation_folder_cleanup if libation_folder_cleanup is not None else _env("LIBATION_FOLDER_CLEANUP", "false").lower() == "true"
    file_loc = _r(libation_file_locations_path, "LIBATION_FILE_LOCATIONS_PATH")
    json_path = str(Path(src) / "libation.json")

    audio_cleaner = _build_audio_cleaner(log, enable=enable_profanity_cleaning, copy_mode=copy_mode)
    processed = move_audio_book_files(
        audio_file_extension=ext,
        books_json_path=json_path,
        copy_instead_of_move=copy_mode,
        destination_dir=dest,
        download_program="Libation",
        libation_folder_cleanup=cleanup,
        log_file=log,
        purchased_how_long_ago=purchased_how_long_ago,
        source_dir=src,
        libation_file_locations_path=file_loc,
        audio_cleaner=audio_cleaner,
    )
    return json.dumps({"processed_count": len(processed), "books": processed, "log": log.getvalue()}, indent=2)


@mcp.tool()
def scan_audiobookshelf(
    library: str | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Trigger an AudioBookShelf library scan.

    Args:
        library: Library name from libraries.yaml (e.g. 'kids', 'adult_podcasts').
        abs_server_url: Override ABS server URL (default: from .env or library config).
        abs_library_id: Override ABS library ID (default: from .env or library config).
        abs_api_token: Override ABS API token (default: from .env or library config).
    """
    lib = _resolve_library(library)
    url = abs_server_url or lib["abs_server_url"]
    lib_id = abs_library_id or lib["library_id"]
    token = abs_api_token or lib["abs_api_token"]
    resp = scan_library_for_books(url, lib_id, token)
    return json.dumps({"success": resp.ok, "status_code": resp.status_code})


@mcp.tool()
def delete_library_items(
    item_ids: list[str] | None = None,
    delete_all: bool = False,
    library: str | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Delete items from the AudioBookShelf library.

    Use to clean up test data or remove specific items. Provide either
    a list of item IDs or set delete_all=True to purge the library.

    Args:
        item_ids: Specific ABS library item IDs to delete.
        delete_all: If True, delete every item in the library (use with caution).
        library: Library name from libraries.yaml (e.g. 'kids', 'adult').
        abs_server_url: Override ABS server URL (default: from .env or library config).
        abs_library_id: Override ABS library ID (default: from .env or library config).
        abs_api_token: Override ABS API token (default: from .env or library config).
    """
    lib = _resolve_library(library)
    url = abs_server_url or lib["abs_server_url"]
    lib_id = abs_library_id or lib["library_id"]
    token = abs_api_token or lib["abs_api_token"]
    headers = _abs_headers(token)

    ids_to_delete = list(item_ids) if item_ids else []

    if delete_all and not ids_to_delete:
        resp = requests.get(
            f"{url}/api/libraries/{lib_id}/items",
            headers=headers,
            params={"limit": 5000},
        )
        if resp.ok:
            ids_to_delete = [item["id"] for item in resp.json().get("results", [])]

    results = []
    for item_id in ids_to_delete:
        r = requests.delete(f"{url}/api/items/{item_id}", headers=headers)
        results.append({"id": item_id, "status": r.status_code})

    return json.dumps({"deleted": len(results), "results": results}, indent=2)


@mcp.tool()
def match_audiobookshelf(
    days_ago: int = 7,
    library: str | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Match recently added AudioBookShelf items to Audible metadata.

    Args:
        days_ago: Match items added within this many days.
        library: Library name from libraries.yaml (e.g. 'kids', 'adult').
        abs_server_url: Override ABS server URL (default: from .env or library config).
        abs_library_id: Override ABS library ID (default: from .env or library config).
        abs_api_token: Override ABS API token (default: from .env or library config).
    """
    lib = _resolve_library(library)
    log = _log_buffer()
    url = abs_server_url or lib["abs_server_url"]
    lib_id = abs_library_id or lib["library_id"]
    token = abs_api_token or lib["abs_api_token"]

    all_books = get_all_books(url, lib_id, token)
    recent = get_audio_bookshelf_recent_books(all_books, log, days_ago=days_ago)
    results = process_audio_books(recent, url, token, log)
    return json.dumps({"matched_count": len(results), "log": log.getvalue()}, indent=2)


@mcp.tool()
def ingest_books(
    asins: list[str] | None = None,
    purchased_how_long_ago: int = 7,
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
    """End-to-end pipeline: download -> organize -> scan ABS -> match metadata.

    Args:
        asins: Optional list of ASINs to download. If empty, downloads all new books.
        purchased_how_long_ago: Process books purchased within this many days. 0 means all.
        library: Library name from libraries.yaml (e.g. 'kids', 'adult'). Resolves destination and ABS settings.
        source_dir: Libation books directory (default: from .env).
        destination_dir: ABS audiobooks directory / NFS mount (default: from .env or library config).
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
    lib = _resolve_library(library)
    results = {}
    log = _log_buffer()
    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    dest = destination_dir or lib["destination_dir"]
    ext = _r(audio_file_extension, "AUDIO_FILE_EXTENSION", ".m4b")
    copy_mode = copy_instead_of_move if copy_instead_of_move is not None else _env("COPY_INSTEAD_OF_MOVE", "false").lower() == "true"
    cleanup = libation_folder_cleanup if libation_folder_cleanup is not None else _env("LIBATION_FOLDER_CLEANUP", "false").lower() == "true"
    file_loc = _r(libation_file_locations_path, "LIBATION_FILE_LOCATIONS_PATH")
    json_path = str(Path(src) / "libation.json")
    url = abs_server_url or lib["abs_server_url"]
    lib_id = abs_library_id or lib["library_id"]
    token = abs_api_token or lib["abs_api_token"]

    LOGGER.info("Step 1: Scanning Audible library")
    results["scan_audible"] = _run_libationcli(["scan"], cli_path=libation_cli)

    LOGGER.info("Step 2: Downloading books")
    liberate_args = ["liberate"]
    if asins:
        liberate_args.extend(asins)
    results["download"] = _run_libationcli(liberate_args, timeout=3600, cli_path=libation_cli)

    LOGGER.info("Step 3: Exporting library metadata")
    results["export"] = _run_libationcli(["export", "--path", json_path, "--json"], cli_path=libation_cli)

    LOGGER.info("Step 4: Organizing files")
    audio_cleaner = _build_audio_cleaner(log, enable=enable_profanity_cleaning, copy_mode=copy_mode)
    processed = move_audio_book_files(
        audio_file_extension=ext,
        books_json_path=json_path,
        copy_instead_of_move=copy_mode,
        destination_dir=dest,
        download_program="Libation",
        libation_folder_cleanup=cleanup,
        log_file=log,
        purchased_how_long_ago=purchased_how_long_ago,
        source_dir=src,
        libation_file_locations_path=file_loc,
        audio_cleaner=audio_cleaner,
    )
    results["processed_books"] = [b.get("title", "Unknown") for b in processed]

    if processed:
        LOGGER.info("Step 5: Scanning ABS")
        scan_library_for_books(url, lib_id, token, log)
        time.sleep(15)

        LOGGER.info("Step 6: Matching in ABS")
        all_books = get_all_books(url, lib_id, token, log)
        recent = get_audio_bookshelf_recent_books(all_books, log, book_list=processed)
        match_results = process_audio_books(recent, url, token, log)
        results["abs_matches"] = len(match_results)
    else:
        results["abs_matches"] = "skipped (no new books)"

    results["log"] = log.getvalue()
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
    transport = _env("MCP_TRANSPORT", "sse")
    LOGGER.info("Starting Audiobook Ingestion MCP (%s)", transport)
    mcp.run(transport=transport)
