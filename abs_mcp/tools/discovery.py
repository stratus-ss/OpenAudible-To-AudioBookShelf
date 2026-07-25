"""Library discovery and management MCP tools.

Extracted from mcp_server.py as part of Plan 3 (DR-1 module decomposition).
Tool registration happens when this module is imported from mcp_server.py
(Task 7); the @mcp.tool() decorators below cause FastMCP to auto-discover
and register all 7 discovery tools.
"""

import json
import time
from pathlib import Path

import requests

from ..config import (
    _abs_headers,
    _env,
    _load_libraries,
    _log_buffer,
    _r,
    _record_tool_result,
    _resolve_library,
    LOGGER,
    PARSER,
)
from ..abs_cache import (
    abs_cache_path as _abs_cache_path,
)
from ..abs_cache import (
    flatten_abs_item as _flatten_abs_item,
)
from ..abs_cache import (
    load_or_refresh_abs_cache as _load_or_refresh_abs_cache,
)
from ..cleanup import (
    cleanup_item_files as _cleanup_item_files,
)
from ..cleanup import (
    scan_directory as _scan_directory,
)

from openaudible_to_audiobookshelf.utils import (
    generate_libation_json,
    resolve_books_json_path,
)


from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Audiobook Discovery Tools")


@mcp.tool()
def list_libraries() -> dict:
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
    return _record_tool_result("list_libraries", _tool_start, result)


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
) -> dict:
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
    json_path = resolve_books_json_path(src, "")

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
        {
            "total_in_library": len(books),
            "total_matches": len(filtered),
            "limit": effective_limit,
            "offset": safe_offset,
            "next_offset": next_offset,
            "paginated": paginated,
            "books": summary,
        },
    )


@mcp.tool()
def get_status(
    library: str | None = None,
    source_dir: str | None = None,
    destination_dir: str | None = None,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
) -> dict:
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

    return _record_tool_result("get_status", _tool_start, status)


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
) -> dict:
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
    cache_path = _abs_cache_path(
        lib["name"], lib_id, _env("ABS_CACHE_DIR", "/tmp/abs-cache")
    )
    try:
        raw_items, cache_meta = _load_or_refresh_abs_cache(
            cache_path=cache_path,
            refresh=refresh,
            max_age_seconds=max(cache_max_age_seconds, 60),
            url=url,
            library_id=lib_id,
            token=token,
            library_name=lib["name"],
            headers_builder=_abs_headers,
            logger=LOGGER,
        )
    except Exception as e:
        return _record_tool_result(
            "list_abs_library",
            _tool_start, {"error": "cache_refresh_failed", "detail": str(e)},
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
            },
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
) -> dict:
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
            _tool_start, {"error": f"HTTP {resp.status_code}", "body": resp.text},
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
        {
            "library": lib["name"] or "(default)",
            "query": query,
            "total_matches": len(flattened),
            "limit": effective_limit,
            "offset": safe_offset,
            "next_offset": next_offset,
            "paginated": paginated,
            "items": paged,
        },
    )


@mcp.tool()
def get_source_status(
    source_dir: str | None = None,
    destination_dir: str | None = None,
    library: str | None = None,
    detail: bool = False,
) -> dict:
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
    result["source"] = _scan_directory(src, include_folders=True)
    if dest:
        result["destination"] = _scan_directory(dest, include_folders=detail)
        result["destination"]["detail"] = detail
    return _record_tool_result("get_source_status", _tool_start, result)


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
) -> dict:
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
    return _record_tool_result("delete_library_items", _tool_start, output)
