import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

import requests


def sanitize_cache_key(name: str) -> str:
    """Sanitize cache key for filesystem safety."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "default")
    return cleaned[:64] or "default"


def abs_cache_path(library_name: str, library_id: str, cache_root: str) -> Path:
    """Build deterministic ABS cache file path."""
    root = Path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    key = sanitize_cache_key(library_name or library_id or "default")
    return root / f"{key}.json"


def fetch_abs_library_items(
    url: str,
    library_id: str,
    token: str,
    headers_builder: Callable[[str], dict],
) -> list[dict]:
    """Fetch the complete ABS library item set."""
    resp = requests.get(
        f"{url}/api/libraries/{library_id}/items",
        headers=headers_builder(token),
        params={"limit": 0, "sort": "addedAt"},
        timeout=45,
    )
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    return resp.json().get("results", [])


def fetch_abs_series_map(
    url: str,
    library_id: str,
    token: str,
    headers_builder: Callable[[str], dict],
    logger: logging.Logger,
) -> dict[str, str]:
    """Build a book_id -> series names mapping from the ABS series endpoint."""
    resp = requests.get(
        f"{url}/api/libraries/{library_id}/series",
        headers=headers_builder(token),
        params={"limit": 500},
        timeout=30,
    )
    if not resp.ok:
        logger.warning("Failed to fetch series data: HTTP %s", resp.status_code)
        return {}
    series_map: dict[str, str] = {}
    results = resp.json().get("results", [])
    for series in results:
        name = series.get("name", "")
        if not name:
            continue
        for book in series.get("books", []):
            book_id = book.get("id", "")
            if book_id:
                existing = series_map.get(book_id, "")
                series_map[book_id] = f"{existing}, {name}" if existing else name
    logger.info("Series map: %d books across %d series", len(series_map), len(results))
    return series_map


def enrich_items_with_series(items: list[dict], series_map: dict[str, str]) -> None:
    """Inject series metadata into minified ABS items in-place."""
    if not series_map:
        return
    for item in items:
        book_id = item.get("id", "")
        if book_id in series_map:
            metadata = item.setdefault("media", {}).setdefault("metadata", {})
            if not metadata.get("series"):
                names = series_map[book_id]
                metadata["series"] = [{"name": n.strip()} for n in names.split(", ")]


def load_or_refresh_abs_cache(
    cache_path: Path,
    refresh: bool,
    max_age_seconds: int,
    url: str,
    library_id: str,
    token: str,
    library_name: str,
    headers_builder: Callable[[str], dict],
    logger: logging.Logger,
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
            logger.info("Cache missing series enrichment, forcing refresh")
            is_stale = True
    if refresh or not cache_exists or is_stale:
        items = fetch_abs_library_items(url, library_id, token, headers_builder)
        series_map = fetch_abs_series_map(
            url, library_id, token, headers_builder, logger
        )
        enrich_items_with_series(items, series_map)
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


def extract_series_names(item: dict) -> str:
    """Extract series names from an ABS library item."""
    series_list = item.get("media", {}).get("metadata", {}).get("series", [])
    if isinstance(series_list, list):
        return ", ".join(
            series.get("name", "") for series in series_list if series.get("name")
        )
    return ""


def flatten_abs_item(item: dict) -> dict:
    """Flatten ABS item into compact MCP-friendly fields."""
    media = item.get("media", {})
    metadata = media.get("metadata", {})
    return {
        "id": item.get("id", ""),
        "title": metadata.get("title", ""),
        "author": metadata.get("authorName", ""),
        "series": extract_series_names(item),
        "duration": round(media.get("duration", 0) / 60, 1),
        "added_at": item.get("addedAt", ""),
        "has_audio": bool(media.get("audioFiles")),
    }
