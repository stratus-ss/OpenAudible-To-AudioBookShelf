#!/usr/bin/env python3
"""Audiobook Ingestion MCP Server.

Exposes tools for downloading audiobooks via Libation, organizing them,
and ingesting them into AudioBookShelf. Designed for SSE transport
to be consumed by Moltis or other MCP clients.

Calls directly into the existing openaudible_to_ab.py pipeline and
modules/ functions -- no duplication of logic.
"""
import io
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

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
        "This server manages the audiobook ingestion pipeline: "
        "downloading from Audible via Libation, organizing files, "
        "and importing into AudioBookShelf."
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
        source_dir: Libation books directory (default: from .env).
        destination_dir: ABS audiobooks directory / NFS mount (default: from .env).
        audio_file_extension: File extension to look for, e.g. '.m4b' (default: from .env).
        copy_instead_of_move: Copy files instead of moving (default: from .env).
        libation_folder_cleanup: Delete Libation source folders after move (default: from .env).
        libation_file_locations_path: Path to Libation FileLocationsV2.json (default: from .env).
        enable_profanity_cleaning: Enable monkeyplug profanity filtering (default: from .env).
    """
    log = _log_buffer()
    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    dest = _r(destination_dir, "DESTINATION_BOOK_DIRECTORY")
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
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Trigger an AudioBookShelf library scan.

    Args:
        abs_server_url: Override ABS server URL (default: from .env).
        abs_library_id: Override ABS library ID (default: from .env).
        abs_api_token: Override ABS API token (default: from .env).
    """
    url = _r(abs_server_url, "ABS_SERVER_URL")
    lib_id = _r(abs_library_id, "ABS_LIBRARY_ID")
    token = _r(abs_api_token, "ABS_API_TOKEN")
    resp = scan_library_for_books(url, lib_id, token)
    return json.dumps({"success": resp.ok, "status_code": resp.status_code})


@mcp.tool()
def match_audiobookshelf(
    days_ago: int = 7,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Match recently added AudioBookShelf items to Audible metadata.

    Args:
        days_ago: Match items added within this many days.
        abs_server_url: Override ABS server URL (default: from .env).
        abs_library_id: Override ABS library ID (default: from .env).
        abs_api_token: Override ABS API token (default: from .env).
    """
    log = _log_buffer()
    url = _r(abs_server_url, "ABS_SERVER_URL")
    lib_id = _r(abs_library_id, "ABS_LIBRARY_ID")
    token = _r(abs_api_token, "ABS_API_TOKEN")

    all_books = get_all_books(url, lib_id, token)
    recent = get_audio_bookshelf_recent_books(all_books, log, days_ago=days_ago)
    results = process_audio_books(recent, url, token, log)
    return json.dumps({"matched_count": len(results), "log": log.getvalue()}, indent=2)


@mcp.tool()
def ingest_books(
    asins: list[str] | None = None,
    purchased_how_long_ago: int = 7,
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
        source_dir: Libation books directory (default: from .env).
        destination_dir: ABS audiobooks directory / NFS mount (default: from .env).
        audio_file_extension: File extension, e.g. '.m4b' (default: from .env).
        copy_instead_of_move: Copy files instead of moving (default: from .env).
        libation_folder_cleanup: Delete Libation source folders after move (default: from .env).
        libation_file_locations_path: Path to Libation FileLocationsV2.json (default: from .env).
        libation_cli: Path to libationcli binary (default: from .env).
        enable_profanity_cleaning: Enable monkeyplug profanity filtering (default: from .env).
        abs_server_url: ABS server URL (default: from .env).
        abs_library_id: ABS library UUID (default: from .env).
        abs_api_token: ABS API bearer token (default: from .env).
    """
    results = {}
    log = _log_buffer()
    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")
    dest = _r(destination_dir, "DESTINATION_BOOK_DIRECTORY")
    ext = _r(audio_file_extension, "AUDIO_FILE_EXTENSION", ".m4b")
    copy_mode = copy_instead_of_move if copy_instead_of_move is not None else _env("COPY_INSTEAD_OF_MOVE", "false").lower() == "true"
    cleanup = libation_folder_cleanup if libation_folder_cleanup is not None else _env("LIBATION_FOLDER_CLEANUP", "false").lower() == "true"
    file_loc = _r(libation_file_locations_path, "LIBATION_FILE_LOCATIONS_PATH")
    json_path = str(Path(src) / "libation.json")
    url = _r(abs_server_url, "ABS_SERVER_URL")
    lib_id = _r(abs_library_id, "ABS_LIBRARY_ID")
    token = _r(abs_api_token, "ABS_API_TOKEN")

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
    source_dir: str | None = None,
    destination_dir: str | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> str:
    """Get current pipeline status and configuration summary.

    Args:
        source_dir: Libation books directory (default: from .env).
        destination_dir: ABS audiobooks directory / NFS mount (default: from .env).
        abs_server_url: ABS server URL (default: from .env).
        abs_library_id: ABS library UUID (default: from .env).
        abs_api_token: ABS API bearer token (default: from .env).
    """
    import requests

    src = _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY", "(not set)")
    dest = _r(destination_dir, "DESTINATION_BOOK_DIRECTORY", "(not set)")
    url = _r(abs_server_url, "ABS_SERVER_URL")
    lib_id = _r(abs_library_id, "ABS_LIBRARY_ID")

    status = {
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


if __name__ == "__main__":
    transport = _env("MCP_TRANSPORT", "sse")
    LOGGER.info("Starting Audiobook Ingestion MCP (%s)", transport)
    mcp.run(transport=transport)
