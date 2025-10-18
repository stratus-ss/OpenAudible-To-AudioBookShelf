#!/usr/bin/env python3
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

from modules.audio_bookshelf import (get_all_books, get_audio_bookshelf_recent_books, process_audio_books,
                                     scan_library_for_books)
from modules.config import Config
from modules.utils import _parse_date, make_directory_structure, sanitize_name


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


def process_libation_book_json(book_data: dict) -> dict:
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

    series_sequence = book_data.get("SeriesOrder").split()[0] if book_data.get("SeriesOrder") else ""

    filename = (
        f"{book_data.get('Title')}: {book_data.get('Subtitle')} [{book_data.get('AudibleProductId')}]"
        if book_data.get("Subtitle")
        else f"{book_data.get('Title')} [{book_data.get('AudibleProductId')}]"
    )

    # Libation creates directory names using only the part before the first colon
    # e.g., "Disney Agent Stitch: The M-Files" becomes "Disney Agent Stitch [ASIN]"
    title_for_folder = book_data.get("Title").split(":")[0].strip()
    book_folder = f"{title_for_folder} [{book_data.get('AudibleProductId')}]"
    purchase_date = book_data.get("DateAdded")
    # Split on timezone offset indicator and take the first part
    formatted_purchase_date = _parse_date(purchase_date)

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


def move_audio_book_files(
    audio_file_extension: str,
    books_json_path: str,
    copy_instead_of_move: bool,
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
    # If set to zero go back in time as a way to say go back infinity
    # 9125 days is 25 years
    if purchased_how_long_ago == 0:
        target_date = (datetime.now(timezone.utc) - timedelta(days=9125)).date()
    else:
        target_date = (datetime.now(timezone.utc) - timedelta(days=purchased_how_long_ago)).date()
    books_to_process_in_audio_bookself = []
    for book in books:
        try:
            if download_program == "OpenAudible":
                book_data = process_open_audible_book_json(book)
            else:
                book_data = process_libation_book_json(book)
                # Need to override the source_dir as Libation puts
                # Books/{book_title}/filename
                libation_source_dir = source_dir + os.sep + book_data["libation_book_folder"]
            purchase_date = book_data["purchase_date"]
            # we don't want to process books in the library older than a specific date
            # it's too intensive
            if datetime.strptime(purchase_date, "%Y-%m-%d").date() < target_date:
                continue
            author_dir = sanitize_name(book_data["author"])
            series_dir = sanitize_name(book_data["series"]) if book_data["series"] else ""
            book_title_dir = sanitize_name(book_data["title"])
            audio_file_name = book_data["filename"] + audio_file_extension
            if download_program == "OpenAudible":
                downloaded_audio_file_path = os.path.join(source_dir, audio_file_name)
            else:
                downloaded_audio_file_path = os.path.join(libation_source_dir, audio_file_name)

            if not (os.path.exists(downloaded_audio_file_path)):
                continue
            audio_book_destination_dir = make_directory_structure(
                author_dir, series_dir, book_title_dir, destination_dir
            )
            target_audio_file_path = os.path.join(audio_book_destination_dir, audio_file_name)

            if os.path.exists(target_audio_file_path):
                existing_file_size = os.path.getsize(target_audio_file_path)
                downloaded_file_size = os.path.getsize(downloaded_audio_file_path)
                if downloaded_file_size < existing_file_size:
                    log_file.write(f"{datetime.now()} - INFO - No change for book: {book_data['title']}\n")
                    continue
                else:
                    log_file.write(f"{book_data['title']} has an existing file but it will be replaced! \n")
                    log_file.write(
                        f"The downloaded file is larger ({downloaded_file_size}) than the existing file \
                            ({existing_file_size}).\n"
                    )
                print(f"Processing: {book_data['title']}")
                log_file.write(
                f"{datetime.now()} - INFO - Processing: {book_data['title']}\n"
                )
            books_to_process_in_audio_bookself.append(book_data)
            if os.path.exists(downloaded_audio_file_path):
                if copy_instead_of_move:
                    shutil.copy2(downloaded_audio_file_path, audio_book_destination_dir)
                    action = "copied"
                else:
                    shutil.move(downloaded_audio_file_path, audio_book_destination_dir)
                    action = "moved"
            if libation_folder_cleanup and not copy_instead_of_move:
                shutil.rmtree(libation_source_dir)
            log_file.write(
                f"{datetime.now()} - INFO - Processed and {action} files for book: {book_data['title']} under \
                    '{author_dir}/{series_dir}'\n"
            )
        except Exception as e:
            error_title = book_data.get("title", "Unknown Book") if "book_data" in locals() else "Unknown Book"
            log_file.write(f"{datetime.now()} - ERROR - An error occurred while processing {error_title}: {e}\n")

    return books_to_process_in_audio_bookself


def main(*args: str):
    # Parse command line arguments
    args = Config.from_args(*args)
    if args.generate_yaml:
        args.generate_yaml_from_parser(file_path="/tmp/arguments.yaml")
        exit()
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
        args.copy_instead_of_move,
        args.destination_book_directory,
        args.download_program,
        args.libation_folder_cleanup,
        log_file,
        args.purchased_how_long_ago,
        args.source_audio_book_directory,
    )

    # Now that the files have been moved, we want to kick off the AudioBookShelf scanner
    scan_library_for_books(args.server_url, args.library_id, args.abs_api_token, log_file)

    # We often need a back-off time in order to allow the scan to complete
    time.sleep(15)

    # Sometimes the scanner does not identify the books correctly
    # In my case I buy books from audible so I want to force the match with audible content
    books_from_audiobookshelf = get_all_books(args.server_url, args.library_id, args.abs_api_token, log_file)
    most_recent_books = get_audio_bookshelf_recent_books(
        books_from_audiobookshelf,
        log_file,
        days_ago=args.purchased_how_long_ago,
        book_list=book_list,
    )
    _ = process_audio_books(most_recent_books, args.server_url, args.abs_api_token, log_file)
    log_file.close()


if __name__ == "__main__":
    main()
