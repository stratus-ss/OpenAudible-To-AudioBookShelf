import subprocess
import time
from datetime import datetime, timedelta, timezone

import requests


def scan_library_for_books(server_url: str, library_id: str, abs_api_token: str, log_file=None) -> requests.Response:
    """
    Scan the library for books using the provided server URL, library ID, and API token.

    Args:
        server_url (str): The base URL of the server.
        library_id (str): The unique identifier of the library.
        abs_api_token (str): The authentication token for API access.

    Returns:
        requests.Response: The response object containing the scan results.
    """
    if log_file:
        log_file.write("Starting the scan of Audio Book Shelf...\n")
    response = requests.post(
        f"{server_url}/api/libraries/{library_id}/scan",
        headers={"Authorization": f"Bearer {abs_api_token}"},
    )
    if log_file:
        log_file.write(f"Scan_results: {response}")
    return response


def get_all_books(server_url: str, library_id: str, abs_api_token: str, log_file=None) -> requests.Response:
    """
    Retrieve all books from the specified library using the provided server URL, library ID, and API token.

    Args:
        server_url (str): The base URL of the server.
        library_id (str): The unique identifier of the library.
        abs_api_token (str): The authentication token for API access.

    Returns:
        requests.Response: The response object containing all book items.
    """
    if log_file:
        log_file.write("Fetching the library from Audio BookShelf...\n")
    return requests.get(
        f"{server_url}/api/libraries/{library_id}/items?sort=addedAt",
        headers={"Authorization": f"Bearer {abs_api_token}"},
    )


def get_audio_bookshelf_recent_books(
    json_response: requests.Response,
    log_file=None,
    days_ago: int = 0,
    book_list: list = [],
) -> list[dict]:
    """
    Filter recent audio books from the provided JSON response based on the number of days ago.

    Args:
        json_response (requests.Response): The response object containing book data.
        days_ago (int): Number of days to consider as "recent" (default is 0)
        book_list (list): A list of book titles

    Returns:
        list[dict]: A list of dictionaries representing recent audio books.
    """
    # If we get a book_list with items, we want to only update those items
    # and not all items in the last N days
    if book_list:
        days_ago = 0
    if days_ago > 0:
        target_date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date()
        if log_file:
            log_file.write(f"Getting the list of books from the last {days_ago} days\n")
        recent_items = [
            item
            for item in json_response.json()["results"]
            if datetime.fromtimestamp(item["addedAt"] / 1000, timezone.utc).date() >= target_date
        ]
    else:
        recent_items = []
        for book in book_list:
            if log_file:
                log_file.write(f"Fetching {book} information from Audio BookShelf\n")
            match_title = book.get("short_title", book["title"])
            for item in json_response.json()["results"]:
                if match_title in item["media"]["metadata"]["title"]:
                    item["media"]["metadata"]["asin"] = book["asin"]
                    item["_original_series"] = book.get("series", "")
                    item["_original_volume"] = book.get("volumeNumber", "")
                    recent_items.append(item)
    return recent_items


def update_book_series(
    item_id: str,
    series_name: str,
    volume_number: str,
    server_url: str,
    abs_api_token: str,
    log_file=None,
) -> requests.Response | None:
    """
    Update a library item's series metadata via the ABS API.

    Args:
        item_id: The ABS library item ID.
        series_name: The human-readable series name (e.g. "All Trades").
        volume_number: The book's position in the series (e.g. "1").
        server_url: The base URL of the ABS server.
        abs_api_token: The authentication token for API access.
        log_file: Optional file handle for logging.

    Returns:
        The response from the PATCH request, or None if no series name provided.
    """
    if not series_name:
        return None
    payload = {
        "metadata": {
            "series": [{"name": series_name, "sequence": volume_number}]
        }
    }
    response = requests.patch(
        f"{server_url}/api/items/{item_id}/media",
        json=payload,
        headers={"Authorization": f"Bearer {abs_api_token}"},
    )
    if log_file:
        status = "success" if response.ok else f"failed ({response.status_code})"
        log_file.write(f"Series update for {item_id} -> '{series_name}' #{volume_number}: {status}\n")
    return response


def process_audio_books(todays_items: list[dict], server_url: str, abs_api_token: str, log_file) -> list[dict]:
    """
    Process each audio book item by attempting to match it with the server.

    Args:
        todays_items (list[dict]): List of dictionaries containing today's audio book items.
        server_url (str): The base URL of the server.
        abs_api_token (str): The authentication token for API access.
    """
    results = []
    for item in todays_items:  # Check last 5 items
        match_payload = {
            "author": item["media"]["metadata"]["authorName"],
            "provider": "audible",
            "asin": item["media"]["metadata"]["asin"],
            "title": item["media"]["metadata"]["title"],
            "overrideDefaults": "true",
        }
        api_url = f"{server_url}/api/items/{item['id']}/match"
        output = requests.post(
            api_url,
            json=match_payload,
            headers={"Authorization": f"Bearer {abs_api_token}"},
        )
        results.append(output.json())
        if output.ok:
            log_file.write(f"Finished Matching {item['media']['metadata']['title']} using the Audible Provider\n")
            series_name = item.get("_original_series", "")
            volume_number = item.get("_original_volume", "")
            if series_name:
                update_book_series(item["id"], series_name, volume_number, server_url, abs_api_token, log_file)
            subprocess.run(
                [
                    "notify-send",
                    "Audio Bookself",
                    f"Processing {item['media']['metadata']['title']}",
                ]
            )
        else:
            subprocess.run(
                [
                    "notify-send",
                    "Error",
                    f"Error with {item['media']['metadata']['title']}",
                ]
            )
        time.sleep(2)
    return results
