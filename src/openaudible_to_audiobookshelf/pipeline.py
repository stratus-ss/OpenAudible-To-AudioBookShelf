#!/usr/bin/env python3
import io
import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone

from openaudible_to_audiobookshelf.audio_bookshelf import (
    get_all_books,
    get_audio_bookshelf_recent_books,
    process_audio_books,
    scan_library_for_books,
)
from openaudible_to_audiobookshelf.audio_cleaner import AudioCleaner, AudioCleaningError
from openaudible_to_audiobookshelf.config import Config
from openaudible_to_audiobookshelf.utils import (
    _parse_date,
    find_existing_series_folder,
    generate_libation_json,
    get_timestamped_log_path,
    make_directory_structure,
    sanitize_name,
)

LOGGER = logging.getLogger(__name__)

VALID_STEPS = ("scan", "download", "export", "organize", "scan-abs", "match")


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


def process_libation_book_json(book_data: dict, file_locations: dict = None) -> dict:
    """
    Map the keys of the libation data dictionary to a standardized format.
    Libation has the following relevent keys:
    'Account', 'AudibleProductId', 'AudioFormat', 'AuthorNames', 'BookStatus', 'CategoriesNames',
    'ContentType', 'DateAdded', 'DatePublished', 'Description', 'HasPdf', 'Language',
    'LengthInMinutes', 'Locale', 'NarratorNames', 'Publisher', 'SeriesNames', 'SeriesOrder',
    'Subtitle', 'Title'

    NOTE: file path/name can be extracted from FileLocationsV2.json if provided,
    otherwise we construct it from the book metadata.

    Args:
        book_data (dict): The input dictionary containing book information.
        file_locations (dict): Optional FileLocationsV2.json data to extract file paths.

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

    purchase_date = book_data.get("DateAdded")
    formatted_purchase_date = _parse_date(purchase_date)

    result = {
        "asin": book_data.get("AudibleProductId"),
        "author": book_data.get("AuthorNames"),
        "description": book_data.get("Description"),
        "filename": filename,
        "purchase_date": formatted_purchase_date,
        "series": book_data.get("SeriesNames", ""),
        "short_title": book_data.get("Title"),
        "title": full_title,
        "volumeNumber": series_sequence,
    }

    # Extract file path from FileLocationsV2.json if available
    if file_locations and "Dictionary" in file_locations:
        asin = book_data.get("AudibleProductId")
        if asin in file_locations["Dictionary"]:
            for location in file_locations["Dictionary"][asin]:
                if location.get("FileType") == 1:
                    candidate = location["Path"]["Path"]
                    if os.path.exists(candidate):
                        result["file_path"] = candidate
                        break

    # If no file path was found in FileLocationsV2.json, construct it the old way
    if "file_path" not in result:
        # Libation creates directory names using only the part before the first colon
        title_for_folder = book_data.get("Title").split(":")[0].strip()
        book_folder = f"{title_for_folder} [{book_data.get('AudibleProductId')}]"
        result["libation_book_folder"] = book_folder

    return result


def _resolve_destination(
    book_data: dict, destination_dir: str, audio_file_name: str
) -> tuple[str, str, str, str]:
    """
    Resolve the author/series directory names and create the destination
    directory tree for a book.

    Args:
        book_data: Standardized book metadata dict.
        destination_dir: Base destination directory for organized books.
        audio_file_name: The audio filename (with extension) for this book.

    Returns:
        A tuple of (author_dir, series_dir, audio_book_destination_dir,
        target_audio_file_path).
    """
    author_dir = sanitize_name(book_data["author"])
    series_dir = (
        find_existing_series_folder(author_dir, book_data["series"], destination_dir)
        if book_data["series"]
        else ""
    )
    book_title_dir = sanitize_name(book_data["title"])
    audio_book_destination_dir = make_directory_structure(
        author_dir, series_dir, book_title_dir, destination_dir
    )
    target_audio_file_path = os.path.join(
        audio_book_destination_dir, audio_file_name
    )
    return author_dir, series_dir, audio_book_destination_dir, target_audio_file_path


def _handle_existing_file_conflict(
    target_audio_file_path: str,
    downloaded_audio_file_path: str,
    book_data: dict,
    log_file,
) -> bool:
    """
    Check whether a file already exists at the destination and decide whether
    to skip this book.

    Args:
        target_audio_file_path: Destination path the file would be moved/copied to.
        downloaded_audio_file_path: Source path of the downloaded audio file.
        book_data: Standardized book metadata dict.
        log_file: File handle for logging.

    Returns:
        True if the existing file should be kept and this book skipped.
        False if there is no conflict, or the existing file will be replaced.
    """
    if not os.path.exists(target_audio_file_path):
        return False

    existing_file_size = os.path.getsize(target_audio_file_path)
    downloaded_file_size = os.path.getsize(downloaded_audio_file_path)
    if downloaded_file_size < existing_file_size:
        log_file.write(
            f"{datetime.now()} - INFO - No change for book: {book_data['title']}\n"
        )
        return True

    log_file.write(
        f"{book_data['title']} has an existing file but it will be replaced! \n"
    )
    log_file.write(
        f"The downloaded file is larger ({downloaded_file_size}) than the existing file \
            ({existing_file_size}).\n"
    )
    print(f"Processing: {book_data['title']}")
    log_file.write(f"{datetime.now()} - INFO - Processing: {book_data['title']}\n")
    return False


def _clean_audio_if_enabled(
    audio_cleaner,
    downloaded_audio_file_path: str,
    book_data: dict,
    _tracking: dict | None,
) -> str | None:
    """
    Run profanity cleaning on the downloaded file if an audio_cleaner is
    configured.

    Args:
        audio_cleaner: Optional AudioCleaner instance, or None to skip cleaning.
        downloaded_audio_file_path: Path to the downloaded (uncleaned) audio file.
        book_data: Standardized book metadata dict.
        _tracking: Optional dict for recording cleaning failures.

    Returns:
        The path to the file that should be moved/copied, or None if cleaning
        failed and this book should be skipped.
    """
    if not audio_cleaner:
        return downloaded_audio_file_path

    try:
        return audio_cleaner.process_audio_file(downloaded_audio_file_path, book_data)
    except AudioCleaningError as e:
        LOGGER.error(
            "Profanity cleaning failed for %s: %s",
            book_data.get("title", "Unknown"),
            e,
        )
        if _tracking is not None:
            _tracking.setdefault("failed_books", []).append({
                "asin": book_data.get("asin", ""),
                "title": book_data.get("title", "Unknown"),
                "error": str(e),
            })
        return None


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
    libation_file_locations_path: str = "",
    audio_cleaner=None,
    asins: list[str] | None = None,
    _tracking: dict | None = None,
) -> list:
    """
    This function reads the books JSON file, processes each book, and logs the results.

    Args:
        audio_file_extension: File extension for audio books (e.g., '.m4b')
        books_json_path: Path to the books JSON file
        copy_instead_of_move: If True, copy files instead of moving them
        destination_dir: Destination directory for organized books
        download_program: Either 'OpenAudible' or 'Libation'
        libation_folder_cleanup: If True, delete source folders after moving
        log_file: File handle for logging
        purchased_how_long_ago: Process books purchased within this many days
        source_dir: Source directory containing audio book files
        libation_file_locations_path: Optional path to Libation's FileLocationsV2.json
        audio_cleaner: Optional AudioCleaner instance for profanity cleaning

    Returns:
        list: List of processed books
    """
    try:
        with open(books_json_path, "r") as file:
            books: list[dict] = json.load(file)
    except (IOError, json.JSONDecodeError) as e:
        log_file.write(f"{datetime.now()} - Error reading JSON file: {e}")
        raise RuntimeError(f"Error reading JSON file: {e}") from e

    # Load file locations if provided (for Libation)
    file_locations = None
    if libation_file_locations_path and os.path.exists(libation_file_locations_path):
        try:
            with open(libation_file_locations_path, "r") as file:
                file_locations = json.load(file)
        except (IOError, json.JSONDecodeError) as e:
            log_file.write(
                f"{datetime.now()} - Warning: Could not read FileLocationsV2.json: {e}\n"
            )
            log_file.write(f"{datetime.now()} - Will use constructed paths instead\n")
    # If set to zero go back in time as a way to say go back infinity
    # 9125 days is 25 years
    if purchased_how_long_ago == 0:
        target_date = (datetime.now(timezone.utc) - timedelta(days=9125)).date()
    else:
        target_date = (
            datetime.now(timezone.utc) - timedelta(days=purchased_how_long_ago)
        ).date()
    books_to_process_in_audio_bookself = []
    total_in_source = len(books)
    skipped_reasons: dict[str, str] = {}
    if audio_cleaner:
        audio_cleaner.set_book_count(total_in_source)
    for book in books:
        try:
            if download_program == "OpenAudible":
                book_data = process_open_audible_book_json(book)
            else:
                book_data = process_libation_book_json(book, file_locations)

            purchase_date = book_data["purchase_date"]
            # we don't want to process books in the library older than a specific date
            # it's too intensive
            if datetime.strptime(purchase_date, "%Y-%m-%d").date() < target_date:
                continue
            # ASIN filter: when provided, only process books in the requested set
            if asins:
                book_asin = book_data.get("asin", "")
                if book_asin not in asins:
                    skipped_reasons[book_data.get("title", "Unknown")] = (
                        f"ASIN {book_asin} not in requested set"
                    )
                    continue
            audio_file_name = book_data["filename"] + audio_file_extension

            if download_program == "OpenAudible":
                downloaded_audio_file_path = os.path.join(source_dir, audio_file_name)
            else:
                # Use file_path from FileLocationsV2.json if available
                if "file_path" in book_data:
                    downloaded_audio_file_path = book_data["file_path"]
                    # Extract the directory for cleanup purposes
                    libation_source_dir = os.path.dirname(downloaded_audio_file_path)
                else:
                    # Fall back to constructed path
                    libation_source_dir = (
                        source_dir + os.sep + book_data["libation_book_folder"]
                    )
                    downloaded_audio_file_path = os.path.join(
                        libation_source_dir, audio_file_name
                    )

            if not (os.path.exists(downloaded_audio_file_path)):
                continue

            (
                author_dir,
                series_dir,
                audio_book_destination_dir,
                target_audio_file_path,
            ) = _resolve_destination(book_data, destination_dir, audio_file_name)

            if _handle_existing_file_conflict(
                target_audio_file_path, downloaded_audio_file_path, book_data, log_file
            ):
                continue
            books_to_process_in_audio_bookself.append(book_data)

            # Clean audio file if profanity cleaning is enabled
            file_to_process = _clean_audio_if_enabled(
                audio_cleaner, downloaded_audio_file_path, book_data, _tracking
            )
            if file_to_process is None:
                continue

            if os.path.exists(file_to_process):
                if copy_instead_of_move:
                    shutil.copy2(file_to_process, audio_book_destination_dir)
                    action = "copied"
                else:
                    shutil.move(file_to_process, audio_book_destination_dir)
                    action = "moved"
                log_file.write(
                    f"{datetime.now()} - INFO - Processed and {action} files for book: {book_data['title']} under \
                        '{author_dir}/{series_dir}'\n"
                )
            if libation_folder_cleanup and not copy_instead_of_move:
                shutil.rmtree(libation_source_dir)
        except Exception as e:
            error_title = (
                book_data.get("title", "Unknown Book")
                if "book_data" in locals()
                else "Unknown Book"
            )
            log_file.write(
                f"{datetime.now()} - ERROR - An error occurred while processing {error_title}: {e}\n"
            )

    if _tracking is not None:
        _tracking["skipped"] = skipped_reasons
        _tracking["total"] = total_in_source
        _tracking["applied_asins"] = asins

    return books_to_process_in_audio_bookself


# ---------------------------------------------------------------------------
# Step functions — each runs one phase and returns a result dict
# ---------------------------------------------------------------------------


def _run_libationcli(
    args: list[str], timeout: int = 600, cli_path: str = "libationcli"
) -> dict:
    """Run a libationcli subcommand and return structured results."""
    cmd = [cli_path] + args
    LOGGER.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=timeout
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def step_scan(config: Config) -> dict:
    """Refresh the Audible library list via libationcli scan."""
    cli = getattr(config, "libation_cli", "libationcli")
    result = _run_libationcli(["scan"], cli_path=cli)
    return {"step": "scan", "success": result["success"], "detail": result}


def step_download(config: Config) -> dict:
    """Download/decrypt books via libationcli liberate."""
    cli = getattr(config, "libation_cli", "libationcli")
    args = ["liberate"]
    asins = getattr(config, "asins", None)
    if asins:
        args.extend(asins)
    result = _run_libationcli(args, timeout=3600, cli_path=cli)
    return {
        "step": "download",
        "success": result["success"],
        "asins": asins or [],
        "source_dir": config.source_audio_book_directory,
        "detail": result,
    }


def step_export(config: Config) -> dict:
    """Export Libation library metadata to libation.json."""
    cli = getattr(config, "libation_cli", "libationcli")
    json_path = getattr(config, "books_json_path", "")
    if not json_path or "OpenAudible" in json_path:
        json_path = os.path.join(config.source_audio_book_directory, "libation.json")
    result = _run_libationcli(["export", "--path", json_path, "--json"], cli_path=cli)
    return {
        "step": "export",
        "success": result["success"],
        "json_path": json_path,
        "detail": result,
    }


def step_organize(
    config: Config, log_file=None, asins: list[str] | None = None
) -> dict:
    """Move/copy audio files into the Author/Series/Title directory tree.

    Args:
        config: Pipeline configuration.
        log_file: Optional file-like object for logging.
        asins: Optional list of ASINs to restrict processing to. When provided,
            books whose ASIN is not in this list are skipped and recorded in
            the return dict's ``skipped`` / ``skipped_reasons`` fields. When
            None or empty, all date-filtered books are processed (backward
            compatible).
    """
    if log_file is None:
        log_file = io.StringIO()

    json_path = getattr(config, "books_json_path", "")
    if not json_path or "OpenAudible" in json_path:
        json_path = os.path.join(config.source_audio_book_directory, "libation.json")

    audio_cleaner = None
    if getattr(config, "enable_profanity_cleaning", False):
        audio_cleaner = AudioCleaner(config, log_file)

    tracking: dict = {}
    processed = move_audio_book_files(
        audio_file_extension=config.audio_file_extension,
        books_json_path=json_path,
        copy_instead_of_move=config.copy_instead_of_move,
        destination_dir=config.destination_book_directory,
        download_program=getattr(config, "download_program", "Libation"),
        libation_folder_cleanup=getattr(config, "libation_folder_cleanup", False),
        log_file=log_file,
        purchased_how_long_ago=getattr(config, "purchased_how_long_ago", 0),
        source_dir=config.source_audio_book_directory,
        libation_file_locations_path=getattr(
            config, "libation_file_locations_path", ""
        ),
        audio_cleaner=audio_cleaner,
        asins=asins,
        _tracking=tracking,
    )

    if audio_cleaner:
        audio_cleaner.log_statistics()
        audio_cleaner.cleanup_working_directory()

    cleaning_stats: dict = {}
    if audio_cleaner:
        cleaning_stats = {
            "total_cleaned": audio_cleaner.total_processed,
            "total_failed": audio_cleaner.total_failed,
            "total_profanities": audio_cleaner.total_profanities,
        }

    moved = [b.get("title", "Unknown") for b in processed]
    log_content = log_file.getvalue() if isinstance(log_file, io.StringIO) else ""

    failed_books = tracking.get("failed_books", [])
    cleaning_failures_capped = failed_books[:50]
    if len(failed_books) > 50:
        cleaning_failures_capped.append({
            "asin": "",
            "title": f"+{len(failed_books) - 50} more failures",
            "error": "",
        })

    return {
        "step": "organize",
        "success": True,
        "processed_count": len(processed),
        "moved": moved,
        "skipped": list(tracking.get("skipped", {}).keys()),
        "skipped_reasons": tracking.get("skipped", {}),
        "total_in_source": tracking.get("total", 0),
        "applied_asins": tracking.get("applied_asins") or [],
        "destination_dir": config.destination_book_directory,
        "cleaning": cleaning_stats,
        "cleaning_failures": cleaning_failures_capped,
        "log": log_content,
        "_book_list": processed,
    }


def step_scan_abs(config: Config, log_file=None, wait: int = 15) -> dict:
    """Trigger an ABS library scan and wait for it to settle."""
    if log_file is None:
        log_file = io.StringIO()
    resp = scan_library_for_books(
        config.server_url, config.library_id, config.abs_api_token, log_file
    )
    time.sleep(wait)
    return {
        "step": "scan-abs",
        "success": resp.ok,
        "status_code": resp.status_code,
        "wait_seconds": wait,
    }


def step_match(config: Config, book_list: list | None = None, log_file=None) -> dict:
    """Match ABS library items to Audible metadata."""
    if log_file is None:
        log_file = io.StringIO()
    all_books = get_all_books(
        config.server_url, config.library_id, config.abs_api_token, log_file
    )
    if not all_books.ok:
        log_file.write(f"Failed to fetch library from ABS: {all_books.status_code}\n")
        return {
            "step": "match",
            "success": False,
            "error": f"ABS request failed with status {all_books.status_code}",
            "status_code": all_books.status_code,
            "log": log_file.getvalue() if isinstance(log_file, io.StringIO) else "",
        }
    recent = get_audio_bookshelf_recent_books(
        all_books,
        log_file,
        days_ago=getattr(config, "purchased_how_long_ago", 7),
        book_list=book_list or [],
    )
    results = process_audio_books(
        recent, config.server_url, config.abs_api_token, log_file
    )
    log_content = log_file.getvalue() if isinstance(log_file, io.StringIO) else ""
    return {
        "step": "match",
        "success": True,
        "matched_count": len(results),
        "log": log_content,
    }


_STEP_DISPATCH = {
    "scan": step_scan,
    "download": step_download,
    "export": step_export,
    "organize": step_organize,
    "scan-abs": step_scan_abs,
    "match": step_match,
}


def run_step(step_name: str, config: Config, **kwargs) -> dict:
    """Run a single named pipeline step and return its result dict."""
    fn = _STEP_DISPATCH.get(step_name)
    if fn is None:
        raise ValueError(
            f"Unknown step: {step_name!r}. Valid: {', '.join(VALID_STEPS)}"
        )
    return fn(config, **kwargs)


# ---------------------------------------------------------------------------
# main — full pipeline or single step via --step
# ---------------------------------------------------------------------------


def main(*args: str):
    args = Config.from_args(*args)
    if args.generate_yaml:
        args.generate_yaml_from_parser(file_path="/tmp/arguments.yaml")
        return

    step = getattr(args, "step", None)
    if step:
        result = run_step(step, args)
        clean = {k: v for k, v in result.items() if not k.startswith("_")}
        print(json.dumps(clean, indent=2))
        return

    timestamped_log_path = get_timestamped_log_path(args.log_file_path)
    try:
        log_file = open(timestamped_log_path, "a")
        print(f"Logging to: {timestamped_log_path}")
    except IOError as e:
        raise RuntimeError(f"Error opening log file: {e}") from e

    if args.download_program == "Libation" and not os.path.exists(args.books_json_path):
        log_file.write(
            f"{datetime.now()} - INFO - Libation JSON file not found at {args.books_json_path}. "
            "Attempting to generate it...\n"
        )
        log_file.flush()

        if "OpenAudible" in args.books_json_path:
            args.books_json_path = os.path.join(
                args.source_audio_book_directory, "libation.json"
            )
            log_file.write(
                f"{datetime.now()} - INFO - Using source audio book directory for libation.json: "
                f"{args.books_json_path}\n"
            )
            log_file.flush()

        success = generate_libation_json(args.books_json_path, log_file)
        if not success:
            log_file.write(
                f"{datetime.now()} - ERROR - Failed to generate libation.json. "
                "Please generate it manually using: "
                f"libationcli export --path {args.books_json_path} --json\n"
            )
            log_file.close()
            raise RuntimeError(
                f"Failed to auto-generate libation.json. "
                f"Please run: libationcli export --path {args.books_json_path} --json"
            )

    audio_cleaner = None
    if getattr(args, "enable_profanity_cleaning", False):
        audio_cleaner = AudioCleaner(args, log_file)
        log_file.write(f"{datetime.now()} - INFO - Profanity cleaning enabled\n")
        log_file.flush()

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
        args.libation_file_locations_path,
        audio_cleaner,
    )

    scan_library_for_books(
        args.server_url, args.library_id, args.abs_api_token, log_file
    )
    time.sleep(15)

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

    if audio_cleaner:
        audio_cleaner.log_statistics()
        audio_cleaner.cleanup_working_directory()

    log_file.close()


if __name__ == "__main__":
    main()
