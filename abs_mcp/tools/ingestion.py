"""Audiobook ingestion pipeline MCP tools.

Extracted from mcp_server.py as part of Plan 3 (DR-1 module decomposition,
Task 4). Tool registration happens when this module is imported from
mcp_server.py (Task 7); the @mcp.tool() decorators below cause FastMCP to
auto-discover and register all tools.

Folds in the profanity_cleaning_mcp_friendly plan's DR-1/DR-2/DR-5/DR-6
changes (2026-07-24, applied to the monolith in commit 9566541) so this
module carries the same behavior as mcp_server.py's current ingestion
tools, not the pre-DR-6 baseline this extraction was originally scoped
against.
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

from ..config import (
    _build_config,
    _r,
    _abs_headers,
    _resolve_library,
    _env,
    _record_tool_result,
    _reset_cleaning_progress,
    _update_cleaning_progress,
    _cleaning_progress,
    _CLEANING_PROGRESS_LOCK,
    LOGGER,
    METRICS,
)
from ..cleanup import detect_audio_extension as _detect_audio_extension
from ..cleanup import extract_asins_from_dir as _extract_asins_from_dir

from openaudible_to_audiobookshelf.audio_bookshelf import scan_library_for_books
from openaudible_to_audiobookshelf.pipeline import (
    step_download,
    step_export,
    step_match,
    step_organize,
    step_scan,
    step_scan_abs,
)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Audiobook Ingestion Tools")

# Global ASIN handoff (Task 4 keeps this as-is; Task 8 replaces it with a
# file-based handoff so it survives process restarts). download_books
# stores the ASINs it just downloaded; organize_books reads (and clears)
# them so only those books are processed. Falls back to a disk-scan if
# the list is empty (e.g. after a process restart).
_last_downloaded_asins: list[str] = []

# Async job pattern (DR-6): long-running tools (organize_books, ingest_books)
# return a job_id immediately and run their work in the background via
# asyncio.create_task. Single-slot — only one job runs at a time.
# Accessed only from the event-loop thread; no lock needed.
_active_job: dict | None = None

_ASINS_FILENAME = "last_download.json"


def _write_downloaded_asins(source_dir: str, asins: list[str]) -> None:
    """Persist the just-downloaded ASINs to source_dir/last_download.json."""
    path = Path(source_dir) / _ASINS_FILENAME
    path.write_text(json.dumps(asins), encoding="utf-8")


def _read_downloaded_asins(source_dir: str) -> list[str]:
    """Read persisted ASINs from source_dir/last_download.json.

    Returns an empty list if the file is missing or contains invalid JSON
    rather than raising, since this is a best-effort handoff.
    """
    path = Path(source_dir) / _ASINS_FILENAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


@mcp.tool()
def scan_audible(
    libation_cli: str | None = None,
) -> dict:
    """Refresh the Audible library list via Libation.

    This is a fast operation (~10 seconds). Run before download_books
    to ensure the library listing is current.

    Args:
        libation_cli: Path to libationcli binary (default: from .env).
    """
    _tool_start = time.monotonic()
    cfg = _build_config(libation_cli=libation_cli)
    result = step_scan(cfg)
    return _record_tool_result("scan_audible", _tool_start, result)


@mcp.tool()
def set_book_status(
    asins: list[str],
    status: str = "not-downloaded",
    force: bool = True,
    libation_cli: str | None = None,
) -> dict:
    """Set the download status of books in Libation's database.

    Use status='not-downloaded' to mark books as unliberated so they
    can be re-downloaded with download_books.

    Args:
        asins: Product IDs (ASINs) of books to update.
        status: 'not-downloaded' or 'downloaded'.
        force: Set status even if the audio file exists on disk.
        libation_cli: Path to libationcli binary (default: from .env).
    """
    import subprocess

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
        {
            "success": result.returncode == 0,
            "asins": asins,
            "status_set": status,
            "force": force,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        },
        success=result.returncode == 0,
    )


@mcp.tool()
def download_books(
    asins: list[str] | None = None,
    libation_cli: str | None = None,
) -> dict:
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
        result,
        success=not bool(result.get("error")),
    )


@mcp.tool()
def export_library(
    source_dir: str | None = None,
    libation_cli: str | None = None,
) -> dict:
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
        result,
        success=not bool(result.get("error")),
    )


def _sync_organize_books(
    purchased_how_long_ago: int,
    library: str | None,
    source_dir: str | None,
    destination_dir: str | None,
    audio_file_extension: str | None,
    copy_instead_of_move: bool | None,
    libation_folder_cleanup: bool | None,
    libation_file_locations_path: str | None,
    enable_profanity_cleaning: bool | None,
    asins: list[str] | None,
) -> dict:
    """Synchronous organize_books implementation — runs in thread pool via asyncio.to_thread."""
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
            {"step": "organize", "success": False, "error": str(e)},
            success=False,
        )

    _reset_cleaning_progress()

    if not user_specified_ext:
        detected = _detect_audio_extension(Path(cfg.source_audio_book_directory))
        if detected and detected != cfg.audio_file_extension:
            LOGGER.info(
                "Auto-detected audio extension %s (configured: %s)",
                detected,
                cfg.audio_file_extension,
            )
            cfg.audio_file_extension = detected

    # Resolve which ASINs to organize: explicit asins= param takes priority,
    # then the in-memory handoff from the most recent download_books call,
    # then fall back to scanning the source directory.
    global _last_downloaded_asins
    if asins is not None:
        download_asins = asins
        _last_downloaded_asins = []  # clear stale handoff
    elif _last_downloaded_asins:
        download_asins = _last_downloaded_asins
        _last_downloaded_asins = []
    else:
        download_asins = _extract_asins_from_dir(cfg.source_audio_book_directory)

    result = step_organize(cfg, asins=download_asins if download_asins else None)
    clean = {k: v for k, v in result.items() if not k.startswith("_")}
    clean["audio_file_extension"] = cfg.audio_file_extension
    return _record_tool_result(
        "organize_books",
        _tool_start,
        clean,
        success=not bool(clean.get("error")),
    )


@mcp.tool()
async def organize_books(
    purchased_how_long_ago: int = 0,
    library: str | None = None,
    source_dir: str | None = None,
    destination_dir: str | None = None,
    audio_file_extension: str | None = None,
    copy_instead_of_move: bool | None = None,
    libation_folder_cleanup: bool | None = None,
    libation_file_locations_path: str | None = None,
    enable_profanity_cleaning: bool | None = None,
    asins: list[str] | None = None,
) -> dict:
    """Organize downloaded audiobooks into the ABS directory structure.

    ASYNC JOB PATTERN (DR-6): Returns immediately with a job handle.
    Poll progress and retrieve results using:
        1. get_cleaning_progress() — per-book/per-stage progress
        2. get_job_result(job_id)  — final result when job completes

    Returns immediately:
        {"job_id": str, "status": "started"}

    If another job is already running:
        {"error": str, "active_job_id": str}

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
        asins: Specific ASINs to organize (e.g. ["B0CVCYB19Y"]). When provided, only
            these books are processed — no other books are touched. If omitted,
            falls back to the ASINs from the most recent download_books call, then
            to scanning all files in the source directory. Always pass asins=
            explicitly when targeting specific books; do not rely on the implicit
            download_books → organize_books handoff.

        Note: When enable_profanity_cleaning=True, expect ~8 min per hour
        of audio content (empirically measured: 8.13 min/hour benchmark,
        7.72 min/hour functional-test confirmation — see
        agent_planning/execution/profanity_cleaning_mcp_friendly/artifacts/),
        heavily dependent on Whisper backend load.
        Audio files >150MB are split into ~145MB chunks and processed
        sequentially. Use get_cleaning_progress() to poll progress
        mid-operation.

    Response (via get_job_result) includes `cleaning_failures[]` when
    profanity cleaning is enabled and any book fails cleaning:
        {"asin": str, "title": str, "error": str}

    The `cleaning` key contains aggregate stats:
        {"total_cleaned": int, "total_failed": int, "total_profanities": int}

    Books that fail cleaning are absent from `moved[]` — the batch
    continues to the next book. Check `cleaning_failures[]` for why.
    """
    global _active_job

    if _active_job is not None and not _active_job["task"].done():
        return {
            "error": "A job is already running. Poll get_job_result() for its status.",
            "active_job_id": _active_job["id"],
        }

    job_id = uuid.uuid4().hex[:12]

    async def _run():
        result = await asyncio.to_thread(
            _sync_organize_books,
            purchased_how_long_ago, library, source_dir, destination_dir,
            audio_file_extension, copy_instead_of_move, libation_folder_cleanup,
            libation_file_locations_path, enable_profanity_cleaning, asins,
        )
        _active_job["result"] = result

    task = asyncio.create_task(_run())
    _active_job = {"id": job_id, "task": task, "result": None}

    return {"job_id": job_id, "status": "started"}


@mcp.tool()
def get_job_result(job_id: str) -> dict:
    """Get the result of a background organize_books or ingest_books job.

    Call this after organize_books returns {"job_id": "...", "status": "started"}.
    Returns the job status or the full result when complete.

    Args:
        job_id: The job_id returned by organize_books or ingest_books.

    Returns:
        {"status": "running", "job_id": str}  — job still in progress
        {"status": "completed", "job_id": str, "result": str}  — job finished
        {"status": "failed", "job_id": str, "error": str}  — job raised an exception
        {"error": "Unknown job", "job_id": str}  — no job with that ID (expired or server restarted)
    """
    if _active_job is None or _active_job["id"] != job_id:
        return {"error": "Unknown job", "job_id": job_id}

    if not _active_job["task"].done():
        return {"status": "running", "job_id": job_id}

    exc = _active_job["task"].exception()
    if exc is not None:
        return {"status": "failed", "job_id": job_id, "error": str(exc)}

    return {"status": "completed", "job_id": job_id, "result": _active_job["result"]}


@mcp.tool()
def scan_audiobookshelf(
    library: str | None = None,
    wait: int = 15,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> dict:
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
        result,
        success=not bool(result.get("error")),
    )


@mcp.tool()
def match_audiobookshelf(
    days_ago: int = 7,
    library: str | None = None,
    book_list: list[dict] | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> dict:
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
        result,
        success=not bool(result.get("error")),
    )


def _sync_ingest_books(
    asins: list[str] | None,
    purchased_how_long_ago: int,
    library: str | None,
    source_dir: str | None,
    destination_dir: str | None,
    audio_file_extension: str | None,
    copy_instead_of_move: bool | None,
    libation_folder_cleanup: bool | None,
    libation_file_locations_path: str | None,
    libation_cli: str | None,
    enable_profanity_cleaning: bool | None,
    abs_server_url: str | None,
    abs_library_id: str | None,
    abs_api_token: str | None,
) -> dict:
    """Synchronous ingest_books implementation — runs in thread pool via asyncio.to_thread."""
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
            {"success": False, "error": str(e)},
            success=False,
        )

    _reset_cleaning_progress()
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

    return _record_tool_result("ingest_books", _tool_start, results)


@mcp.tool()
async def ingest_books(
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
) -> dict:
    """End-to-end pipeline: scan -> download -> export -> organize -> scan ABS -> match.

    ASYNC JOB PATTERN (DR-6): Returns immediately with a job handle.
    Poll progress and retrieve results using:
        1. get_cleaning_progress() — per-book/per-stage progress
        2. get_job_result(job_id)  — final result when job completes

    Returns immediately:
        {"job_id": str, "status": "started"}

    If another job is already running:
        {"error": str, "active_job_id": str}

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

        Note: When enable_profanity_cleaning=True, expect ~8 min per hour
        of audio content (empirically measured: 8.13 min/hour benchmark,
        7.72 min/hour functional-test confirmation — see
        agent_planning/execution/profanity_cleaning_mcp_friendly/artifacts/),
        heavily dependent on Whisper backend load.
        Audio files >150MB are split into ~145MB chunks and processed
        sequentially. Use get_cleaning_progress() to poll progress
        mid-operation.

    The `organize` step response (via get_job_result) includes
    `cleaning_failures[]` when profanity cleaning is enabled and any book
    fails cleaning: {"asin": str, "title": str, "error": str}.
    """
    global _active_job

    if _active_job is not None and not _active_job["task"].done():
        return {
            "error": "A job is already running. Poll get_job_result() for its status.",
            "active_job_id": _active_job["id"],
        }

    job_id = uuid.uuid4().hex[:12]

    async def _run():
        result = await asyncio.to_thread(
            _sync_ingest_books,
            asins, purchased_how_long_ago, library, source_dir, destination_dir,
            audio_file_extension, copy_instead_of_move, libation_folder_cleanup,
            libation_file_locations_path, libation_cli, enable_profanity_cleaning,
            abs_server_url, abs_library_id, abs_api_token,
        )
        _active_job["result"] = result

    task = asyncio.create_task(_run())
    _active_job = {"id": job_id, "task": task, "result": None}

    return {"job_id": job_id, "status": "started"}


@mcp.tool()
def get_cleaning_progress() -> dict:
    """Get current profanity cleaning progress.

    Poll this mid-operation to check per-book status when
    enable_profanity_cleaning=True. Returns JSON with per-ASIN
    progress, per-stage state for the active book, and counters
    for ETA computation.

    Returns empty progress if no cleaning operation is active or
    if the last operation completed without a progress callback.

    Response schema (when active):
        {
            "asin": str,             # active book's ASIN (or last book's)
            "book_title": str,
            "status": str,           # processing|transcribing|done|failed|skipped
            "stage": str,            # staging|uploading|transcribing|done|failed|skipped
            "books_done": int,       # books completed so far
            "books_total": int,      # total books in the batch
            "chunk_done": int,       # chunks transcribed (only when stage=transcribing, >150MB files)
            "chunk_total": int,      # total chunks for the book
            "profanities": int,      # count if status=done
            "error": str             # if status=failed
        }

    ETA hint: derive as (elapsed_sec / books_done) * (books_total - books_done).
    If books_done == 0, ETA is null. When chunk_total > 0, prefer
    chunk-level ETA: (elapsed_sec / chunk_done) * (chunk_total - chunk_done)
    for much finer granularity. The MCP server does not compute ETA —
    calling agents should track wall-clock time externally.

    Note: This tool is callable concurrently while organize_books or
    ingest_books is in flight (DR-6 async job pattern).
    """
    with _CLEANING_PROGRESS_LOCK:
        snapshot = dict(_cleaning_progress)
    snapshot.pop("_updated_at", None)
    # Round-trip through json.dumps(default=str) to normalize any non-JSON-
    # serializable values (matches the safety net the old str-returning
    # implementation relied on) while still returning a dict.
    return json.loads(json.dumps(snapshot, default=str))
