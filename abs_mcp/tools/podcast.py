"""Podcast MCP tools (Layer 1: ABS-native, Layer 2: RSS feed direct download).

Extracted from mcp_server.py as part of Plan 3 (DR-1 module decomposition).
Tool registration happens when this module is imported from mcp_server.py
(Task 7); the @mcp.tool() decorators below cause FastMCP to auto-discover
and register all 7 podcast tools.
"""

import json
import time

from ..config import (
    _abs_headers,
    _record_tool_result,
    _resolve_library,
    LOGGER,
)
from ..podcast_tools import (
    add_podcast_data,
    download_podcast_episodes_data,
    download_podcast_files_data,
    fetch_podcast_feed_data,
    get_podcast_episodes_data,
    list_podcasts_data,
    search_podcasts_data,
)

from openaudible_to_audiobookshelf.audio_bookshelf import scan_library_for_books


from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Audiobook Podcast Tools")


@mcp.tool()
def search_podcasts(
    term: str,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> dict:
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

    ok, payload = search_podcasts_data(term, url, token, _abs_headers)
    if not ok:
        return _record_tool_result(
            "search_podcasts",
            _tool_start, payload,
            success=False,
        )
    return _record_tool_result("search_podcasts", _tool_start, payload)


@mcp.tool()
def add_podcast(
    feed_url: str,
    library: str | None = None,
    title: str | None = None,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> dict:
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
    ok, payload = add_podcast_data(
        feed_url, lib, title, url, token, _abs_headers
    )
    if ok:
        return _record_tool_result(
            "add_podcast", _tool_start, payload, success=True
        )
    return _record_tool_result(
        "add_podcast",
        _tool_start, payload,
        success=False,
    )


@mcp.tool()
def list_podcasts(
    library: str | None = None,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> dict:
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

    ok, payload = list_podcasts_data(lib, url, token, _abs_headers)
    if not ok:
        return _record_tool_result(
            "list_podcasts",
            _tool_start, payload,
            success=False,
        )
    return _record_tool_result("list_podcasts", _tool_start, payload)


@mcp.tool()
def get_podcast_episodes(
    podcast_id: str,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> dict:
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

    ok, payload = get_podcast_episodes_data(
        podcast_id, url, token, _abs_headers
    )
    if not ok:
        return _record_tool_result(
            "get_podcast_episodes",
            _tool_start, payload,
            success=False,
        )
    return _record_tool_result(
        "get_podcast_episodes", _tool_start, payload)


@mcp.tool()
def download_podcast_episodes(
    podcast_id: str,
    limit: int = 3,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> dict:
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

    ok, payload = download_podcast_episodes_data(
        podcast_id, limit, url, token, _abs_headers
    )
    if not ok:
        return _record_tool_result(
            "download_podcast_episodes",
            _tool_start,
            payload,
            success=False,
        )
    return _record_tool_result(
        "download_podcast_episodes",
        _tool_start, payload,
    )


@mcp.tool()
def fetch_podcast_feed(
    search_term: str | None = None,
    apple_url: str | None = None,
    feed_url: str | None = None,
    max_episodes: int = 20,
) -> dict:
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
    ok, payload = fetch_podcast_feed_data(
        search_term=search_term,
        apple_url=apple_url,
        feed_url=feed_url,
        max_episodes=max_episodes,
    )
    if not ok:
        return _record_tool_result(
            "fetch_podcast_feed",
            _tool_start, payload,
            success=False,
        )
    return _record_tool_result(
        "fetch_podcast_feed",
        _tool_start, payload,
    )


@mcp.tool()
def download_podcast_files(
    urls: list[str],
    podcast_name: str,
    library: str | None = None,
    episode_names: list[str] | None = None,
    trigger_scan: bool = True,
    abs_server_url: str | None = None,
    abs_api_token: str | None = None,
) -> dict:
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
            _tool_start, {"error": "No destination_dir configured for this library"},
            success=False,
        )

    downloaded, errors = download_podcast_files_data(
        urls, podcast_name, dest_base, LOGGER, episode_names
    )

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
        {
            "downloaded": len(downloaded),
            "errors": len(errors),
            "files": downloaded,
            "error_details": errors,
            "scan_result": scan_result,
        },
        success=len(errors) == 0,
    )
