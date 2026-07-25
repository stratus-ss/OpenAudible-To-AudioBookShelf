import logging
import re
from pathlib import Path
from typing import Callable

import feedparser
import requests


ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
APPLE_PODCAST_ID_RE = re.compile(r"/id(\d+)")


def sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filesystems.

    Kept local (not in openaudible_to_audiobookshelf.utils) since it must
    match mcp_server.py's original `_sanitize_filename` behavior exactly
    (filesystem-unsafe chars only, preserves spaces/unicode) rather than
    utils.sanitize_name's more aggressive alnum-only stripping, which would
    change existing podcast folder names.
    """
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")


def search_podcasts_data(
    term: str,
    url: str,
    token: str,
    headers_builder: Callable[[str], dict],
) -> tuple[bool, list[dict] | dict]:
    """Search for podcasts via the ABS podcast search endpoint."""
    resp = requests.get(
        f"{url}/api/search/podcast",
        headers=headers_builder(token),
        params={"term": term},
        timeout=15,
    )
    if not resp.ok:
        return False, {"error": f"HTTP {resp.status_code}", "body": resp.text}
    podcasts = resp.json()
    summary = [
        {
            "id": podcast.get("id"),
            "title": podcast.get("title", ""),
            "artist": podcast.get("artistName", ""),
            "feed_url": podcast.get("feedUrl", ""),
            "genres": podcast.get("genres", []),
            "track_count": podcast.get("trackCount", 0),
            "description": (podcast.get("description") or "")[:200],
        }
        for podcast in podcasts
    ]
    return True, summary


def add_podcast_data(
    feed_url: str,
    lib: dict,
    title: str | None,
    url: str,
    token: str,
    headers_builder: Callable[[str], dict],
) -> tuple[bool, dict]:
    """Add a podcast to an ABS podcast library by RSS feed URL."""
    folder_id = lib["folder_id"]
    if not folder_id:
        lib_resp = requests.get(
            f"{url}/api/libraries/{lib['library_id']}",
            headers=headers_builder(token),
            timeout=10,
        )
        if lib_resp.ok:
            folders = lib_resp.json().get("folders", [])
            folder_id = folders[0]["id"] if folders else ""

    feed_title = title or ""
    if not feed_title:
        parsed = feedparser.parse(feed_url)
        feed_title = parsed.feed.get("title", "Unknown Podcast")

    safe_name = sanitize_filename(feed_title)
    lib_resp2 = requests.get(
        f"{url}/api/libraries/{lib['library_id']}",
        headers=headers_builder(token),
        timeout=10,
    )
    base_path = "/"
    if lib_resp2.ok:
        folders = lib_resp2.json().get("folders", [])
        if folders:
            base_path = folders[0]["fullPath"]

    payload = {
        "libraryId": lib["library_id"],
        "folderId": folder_id,
        "path": f"{base_path}/{safe_name}",
        "media": {"metadata": {"title": feed_title, "feedUrl": feed_url}},
    }

    resp = requests.post(
        f"{url}/api/podcasts",
        headers={**headers_builder(token), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if not resp.ok:
        return False, {"success": False, "status": resp.status_code, "body": resp.text}
    data = resp.json()
    return True, {
        "success": True,
        "id": data.get("id"),
        "title": data.get("media", {}).get("metadata", {}).get("title"),
    }


def list_podcasts_data(
    lib: dict,
    url: str,
    token: str,
    headers_builder: Callable[[str], dict],
) -> tuple[bool, list[dict] | dict]:
    """List all podcasts in a podcast library."""
    resp = requests.get(
        f"{url}/api/libraries/{lib['library_id']}/items",
        headers=headers_builder(token),
        params={"limit": 500},
        timeout=15,
    )
    if not resp.ok:
        return False, {"error": f"HTTP {resp.status_code}"}
    items = resp.json().get("results", [])
    return True, [
        {
            "id": item["id"],
            "title": item.get("media", {}).get("metadata", {}).get("title", ""),
            "author": item.get("media", {}).get("metadata", {}).get("author", ""),
            "episode_count": len(item.get("media", {}).get("episodes", [])),
        }
        for item in items
    ]


def get_podcast_episodes_data(
    podcast_id: str,
    url: str,
    token: str,
    headers_builder: Callable[[str], dict],
) -> tuple[bool, dict]:
    """Get episodes for a specific podcast in ABS."""
    resp = requests.get(
        f"{url}/api/items/{podcast_id}",
        headers=headers_builder(token),
        timeout=15,
    )
    if not resp.ok:
        return False, {"error": f"HTTP {resp.status_code}"}
    data = resp.json()
    episodes = data.get("media", {}).get("episodes", [])
    summary = [
        {
            "id": episode.get("id", ""),
            "title": episode.get("title", ""),
            "published_at": episode.get("publishedAt", 0),
            "duration": episode.get("duration", 0),
            "size": episode.get("size", 0),
        }
        for episode in episodes[:50]
    ]
    return True, {
        "podcast_title": data.get("media", {}).get("metadata", {}).get("title", ""),
        "total_episodes": len(episodes),
        "episodes": summary,
    }


def download_podcast_episodes_data(
    podcast_id: str,
    limit: int,
    url: str,
    token: str,
    headers_builder: Callable[[str], dict],
) -> tuple[bool, dict]:
    """Check for and download new episodes for a podcast via ABS."""
    resp = requests.get(
        f"{url}/api/podcasts/{podcast_id}/checknew",
        headers=headers_builder(token),
        params={"limit": limit},
        timeout=60,
    )
    if not resp.ok:
        return False, {"error": f"HTTP {resp.status_code}", "body": resp.text}
    data = resp.json()
    episodes = data.get("episodes", [])
    return True, {
        "new_episodes_found": len(episodes),
        "episodes": [
            {"title": episode.get("title", ""), "published": episode.get("pubDate", "")}
            for episode in episodes
        ],
    }


def extract_apple_podcast_id(apple_url: str) -> str | None:
    """Extract numeric podcast ID from an Apple Podcasts URL."""
    match = APPLE_PODCAST_ID_RE.search(apple_url)
    return match.group(1) if match else None


def itunes_feed_url(podcast_id: str) -> str | None:
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


def fetch_podcast_feed_data(
    search_term: str | None = None,
    apple_url: str | None = None,
    feed_url: str | None = None,
    max_episodes: int = 20,
) -> tuple[bool, dict]:
    """Find and parse a podcast RSS feed, returning episode download URLs."""
    resolved_feed = feed_url
    if not resolved_feed and apple_url:
        podcast_id = extract_apple_podcast_id(apple_url)
        if podcast_id:
            resolved_feed = itunes_feed_url(podcast_id)
        if not resolved_feed:
            return False, {"error": f"Could not extract feed URL from {apple_url}"}

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
            return False, {"error": f"No podcast feed found for '{search_term}'"}

    if not resolved_feed:
        return False, {"error": "Provide search_term, apple_url, or feed_url"}

    parsed = feedparser.parse(resolved_feed)
    if parsed.bozo and not parsed.entries:
        return False, {"error": f"Failed to parse feed: {parsed.bozo_exception}"}

    episodes = []
    for entry in parsed.entries[:max_episodes]:
        enclosure = entry.enclosures[0] if entry.enclosures else {}
        episodes.append(
            {
                "title": entry.get("title", ""),
                "published": entry.get("published", ""),
                "duration": entry.get("itunes_duration", ""),
                "download_url": enclosure.get("href", ""),
                "mime_type": enclosure.get("type", ""),
                "size_bytes": enclosure.get("length", ""),
            }
        )

    return True, {
        "feed_url": resolved_feed,
        "podcast_title": parsed.feed.get("title", ""),
        "total_episodes_in_feed": len(parsed.entries),
        "episodes": episodes,
    }


def download_podcast_files_data(
    urls: list[str],
    podcast_name: str,
    dest_base: str,
    logger: logging.Logger,
    episode_names: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Download audio files into the destination podcast directory."""
    safe_podcast = sanitize_filename(podcast_name)
    podcast_dir = Path(dest_base) / safe_podcast
    podcast_dir.mkdir(parents=True, exist_ok=True)

    downloaded: list[dict] = []
    errors: list[dict] = []
    names = episode_names or []

    for index, url_item in enumerate(urls):
        try:
            resp = requests.get(url_item, stream=True, timeout=300)
            resp.raise_for_status()

            if index < len(names) and names[index]:
                filename = sanitize_filename(names[index])
            else:
                filename = sanitize_filename(Path(url_item.split("?")[0]).stem)

            content_type = resp.headers.get("Content-Type", "")
            if "mpeg" in content_type or url_item.endswith(".mp3"):
                ext = ".mp3"
            elif "mp4" in content_type or "m4a" in content_type:
                ext = ".m4a"
            else:
                ext = ".mp3"

            if not filename.endswith(ext):
                filename += ext

            dest_path = podcast_dir / filename
            size = 0
            with open(dest_path, "wb") as file_handle:
                for chunk in resp.iter_content(chunk_size=65536):
                    file_handle.write(chunk)
                    size += len(chunk)

            downloaded.append({"file": str(dest_path), "size": size})
            logger.info("Downloaded %s (%d bytes)", dest_path, size)
        except Exception as e:
            errors.append({"url": url_item, "error": str(e)})
            logger.error("Failed to download %s: %s", url_item, e)

    return downloaded, errors
