#!/usr/bin/env python3
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
import requests
import time
import subprocess

from modules.config import Config


def scan_library_for_books(
    server_url: str, library_id: str, abs_api_token: str, log_file
) -> requests.Response:
    """
    Scan the library for books using the provided server URL, library ID, and API token.

    Args:
        server_url (str): The base URL of the server.
        library_id (str): The unique identifier of the library.
        abs_api_token (str): The authentication token for API access.

    Returns:
        requests.Response: The response object containing the scan results.
    """
    log_file.write("Starting the scan of Audio Book Shelf...\n")
    return requests.post(
        f"{server_url}/api/libraries/{library_id}/scan",
        headers={"Authorization": f"Bearer {abs_api_token}"},
    )


def get_all_books(
    server_url: str, library_id: str, abs_api_token: str, log_file
) -> requests.Response:
    """
    Retrieve all books from the specified library using the provided server URL, library ID, and API token.

    Args:
        server_url (str): The base URL of the server.
        library_id (str): The unique identifier of the library.
        abs_api_token (str): The authentication token for API access.

    Returns:
        requests.Response: The response object containing all book items.
    """
    log_file.write("Fetching the library from Audio BookShelf...\n")
    return requests.get(
        f"{server_url}/api/libraries/{library_id}/items?sort=addedAt",
        headers={"Authorization": f"Bearer {abs_api_token}"},
    )


def get_audio_bookshelf_recent_books(
    json_response: requests.Response, log_file, days_ago: int = 0, book_list: list = []
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
        log_file.write(f"Getting the list of books from the last {days_ago} days\n")
        recent_items = [
            item
            for item in json_response.json()["results"]
            if datetime.fromtimestamp(item["addedAt"] / 1000, timezone.utc).date()
            >= target_date
        ]
    else:
        recent_items = []
        for book in book_list:
            log_file.write(f"Fetching {book} information from Audio BookShelf\n")
            for item in json_response.json()["results"]:
                if book["title"] in item["media"]["metadata"]["title"]:
                    # Update the nested 'asin' key with the book's asin
                    item["media"]["metadata"]["asin"] = book["asin"]
                    recent_items.append(item)
    return recent_items


def process_audio_books(
    todays_items: list[dict], server_url: str, abs_api_token: str, log_file
) -> None:
    """
    Process each audio book item by attempting to match it with the server.

    Args:
        todays_items (list[dict]): List of dictionaries containing today's audio book items.
        server_url (str): The base URL of the server.
        abs_api_token (str): The authentication token for API access.
    """
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
        if output.ok:
            log_file.write(
                f"Finished Matching {item["media"]["metadata"]["title"]} using the Audible Provider"
            )
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


def sanitize_name(name: str) -> str:
    """
    Sanitize a name by replacing commas with underscores and spaces with single underscores.

    Args:
        name (str): The input name to sanitize.

    Returns:
        str: The sanitized name.
    """
    name_without_commas = name.replace(",", "")
    name_with_underscores = name_without_commas.replace(" ", "_")
    sanitized = "".join(
        [c for c in name_with_underscores if c.isalnum() or c in ("_", ".")]
    )
    return sanitized.rstrip()


def process_open_audible_book_json(book_data: dict) -> dict:
    """
    Map the keys of the book data dictionary to a standardized format.

    Args:
        book_data (dict): The input dictionary containing book information.

    Returns:
        dict: A new dictionary with standardized key-value pairs.
    """
    author_name = book_data.get("author", "").split(",")[0].strip()
    return {
        "asin": book_data.get("asin", ""),
        "author": sanitize_name(author_name),
        "description": book_data.get("summary", ""),
        "filename": book_data.get("filename"),
        "purchase_date": book_data.get("purchase_date"),
        "series": book_data.get("series_name", ""),
        "short_title": book_data.get("title_short"),
        "title": book_data.get("title"),
        "volumeNumber": book_data.get("series_sequence", ""),
    }


def process_libation_book_json(book_data: dict, log_file) -> dict:
    """
    Map the keys of the libation data dictionary to a standardized format.
    Libation has the following relevent keys:
    'Account', 'AudibleProductId', 'AudioFormat', 'AuthorNames', 'BookStatus', 'CategoriesNames',
    'ContentType', 'DateAdded', 'DatePublished', 'Description', 'HasPdf', 'Language',
    'LengthInMinutes', 'Locale', 'NarratorNames', 'Publisher', 'SeriesNames', 'SeriesOrder',
    'Subtitle', 'Title'

    NOTE: file path/name are not included in Libation's generic info. We need to construct it

    Args:
        book_data (dict): The input dictionary containing book information.

    Returns:
        dict: A new dictionary with standardized key-value pairs.
    """
    full_title = (
        " - ".join([book_data.get("Title"), book_data.get("Subtitle")])
        if book_data.get("Subtitle")
        else book_data.get("Title")
    )

    series_sequence = (
        book_data.get("SeriesOrder").split()[0] if book_data.get("SeriesOrder") else ""
    )

    filename = (
        f"{book_data.get('Title')}: {book_data.get('Subtitle')} [{book_data.get('AudibleProductId')}]"
        if book_data.get("Subtitle")
        else f"{book_data.get('Title')} [{book_data.get('AudibleProductId')}]"
    )

    book_folder = f"{book_data.get('Title')} [{book_data.get('AudibleProductId')}]"
    purchase_date = book_data.get("DateAdded")
    # Split on timezone offset indicator and take the first part
    if "+" in purchase_date:
        purchase_date = purchase_date.split("+")[0]
    elif "-" in purchase_date:  # Handle negative offsets
        purchase_date = purchase_date.split("-")[0]
    # For consistency, add a microsecond to any book that does not have this
    if "." not in purchase_date:
        purchase_date += ".0"
    formatted_purchase_date = datetime.strptime(
        purchase_date, "%Y-%m-%dT%H:%M:%S.%f"
    ).strftime("%Y-%m-%d")

    return {
        "asin": book_data.get("AudibleProductId"),
        "author": book_data.get("AuthorNames"),
        "description": book_data.get("Description"),
        "filename": filename,
        "libation_book_folder": book_folder,
        "purchase_date": formatted_purchase_date,
        "series": book_data.get("SeriesNames", ""),
        "short_title": book_data.get("Title"),
        "title": full_title,
        "volumeNumber": series_sequence,
    }


def make_directory_structure(
    author_dir: str, series_dir: str, book_title_dir: str, destination_dir: str
) -> str:
    """
    Create a directory structure for organizing audio books.

    Args:
        author_dir (str): The directory name for the author.
        series_dir (str): The optional directory name for the series.
        book_title_dir (str): The directory name for the specific book title.
        destination_dir (str): The base destination directory.

    Returns:
        str: The full path of the created directory structure.
    """
    audio_book_destination_dir = os.path.join(destination_dir, author_dir)
    if series_dir:
        audio_book_destination_dir = os.path.join(
            audio_book_destination_dir, series_dir
        )
    audio_book_destination_dir = os.path.join(
        audio_book_destination_dir, book_title_dir
    )
    if not os.path.exists(audio_book_destination_dir):
        os.makedirs(audio_book_destination_dir)
    return audio_book_destination_dir


def move_audio_book_files(
    audio_file_extension: str,
    books_json_path: str,
    destination_dir: str,
    download_program: str,
    libation_folder_cleanup: bool,
    log_file,
    purchased_how_long_ago: int,
    source_dir: str,
) -> list:
    """
    This function reads the books JSON file, processes each book, and logs the results.
    """
    try:
        with open(books_json_path, "r") as file:
            books: list[dict] = json.load(file)
    except (IOError, json.JSONDecodeError) as e:

        log_file.write(f"{datetime.now()} - Error reading JSON file: {e}")
        exit(1)

    target_date = (
        datetime.now(timezone.utc) - timedelta(days=purchased_how_long_ago)
    ).date()
    books_to_process_in_audio_bookself = []
    for book in books:
        try:
            if download_program == "OpenAudible":
                book_data = process_open_audible_book_json(book)
            else:
                log_file.write(json.dumps(book))
                book_data = process_libation_book_json(book, log_file)
                log_file.write(json.dumps(book_data))
                # Need to override the source_dir as Libation puts
                # Books/{book_title}/filename
                libation_source_dir = (
                    source_dir + os.sep + book_data["libation_book_folder"]
                )
            purchase_date = book_data["purchase_date"]
            # we don't want to process books in the library older than a specific date
            # it's too intensive
            if datetime.strptime(purchase_date, "%Y-%m-%d").date() < target_date:
                continue
            author_dir = sanitize_name(book_data["author"])
            series_dir = (
                sanitize_name(book_data["series"]) if book_data["series"] else ""
            )
            book_title_dir = sanitize_name(book_data["title"])
            audio_file_name = book_data["filename"] + audio_file_extension
            if download_program == "OpenAudible":
                downloaded_audio_file_path = os.path.join(source_dir, audio_file_name)
            else:
                downloaded_audio_file_path = os.path.join(
                    libation_source_dir, audio_file_name
                )
            if not (os.path.exists(downloaded_audio_file_path)):
                continue
            audio_book_destination_dir = make_directory_structure(
                author_dir, series_dir, book_title_dir, destination_dir
            )
            target_audio_file_path = os.path.join(
                audio_book_destination_dir, audio_file_name
            )

            if os.path.exists(target_audio_file_path):
                existing_file_size = os.path.getsize(target_audio_file_path)
                downloaded_file_size = os.path.getsize(downloaded_audio_file_path)
                if downloaded_file_size < existing_file_size:
                    log_file.write(
                        f"{datetime.now()} - INFO - No change for book: {book_data['title']}\n"
                    )
                    continue
                else:
                    log_file.write(
                        f"{book_data['title']} has an existing file but it will be replaced! \n"
                    )
                    log_file.write(
                        f"The downloaded file is larger ({downloaded_file_size}) than the existing file \
                            ({existing_file_size}).\n"
                    )
            books_to_process_in_audio_bookself.append(book_data["short_title"])
            if os.path.exists(downloaded_audio_file_path):
                shutil.move(downloaded_audio_file_path, audio_book_destination_dir)
            if libation_folder_cleanup:
                shutil.rmtree(libation_source_dir)
            log_file.write(
                f"{datetime.now()} - INFO - Processed and moved files for book: {book_data['title']} under \
                    '{author_dir}/{series_dir}'\n"
            )
        except Exception as e:
            error_title = (
                book_data.get("title", "Unknown Book")
                if "book_data" in locals()
                else "Unknown Book"
            )
            log_file.write(
                f"{datetime.now()} - ERROR - An error occurred while processing {error_title}: {e}\n"
            )

    return books_to_process_in_audio_bookself


def main(*args: str):
    # Parse command line arguments
    args = Config.from_args(*args)

    try:
        log_file = open(args.log_file_path, "a")
    except IOError as e:
        print(f"Error opening log file: {e}")
        exit(1)

    # This will process any files in the OpenAudible directory that is 7 days or newer
    # According to current date as compared to the purchase date
    book_list = move_audio_book_files(
        args.audio_file_extension,
        args.books_json_path,
        args.destination_book_directory,
        args.download_program,
        args.libation_folder_cleanup,
        log_file,
        args.purchased_how_long_ago,
        args.source_audio_book_directory,
    )

    # Now that the files have been moved, we want to kick off the AudioBookShelf scanner
    scan_library_for_books(
        args.server_url, args.library_id, args.abs_api_token, log_file
    )

    # We often need a back-off time in order to allow the scan to complete
    time.sleep(15)
    
    # Sometimes the scanner does not identify the books correctly
    # In my case I buy books from audible so I want to force the match with audible content
    books_from_audiobookshelf = get_all_books(
        args.server_url, args.library_id, args.abs_api_token, log_file
    )
    most_recent_books = get_audio_bookshelf_recent_books(
        books_from_audiobookshelf,
        log_file,
        days_ago=args.purchased_how_long_ago,
        book_list=book_list,
    )
    process_audio_books(
        most_recent_books, args.server_url, args.abs_api_token, log_file
    )
    log_file.close()


if __name__ == "__main__":
    main()
