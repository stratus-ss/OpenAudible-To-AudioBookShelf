import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO


def get_timestamped_log_path(base_log_path: str) -> str:
    """
    Insert a timestamp before the file extension.

    Args:
        base_log_path (str): The original log file path.

    Returns:
        str: The log file path with a YYYYMMDD_HHMMSS timestamp inserted
            before the file extension.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(base_log_path)
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    
    timestamped_name = f"{stem}_{timestamp}{suffix}"
    return str(parent / timestamped_name)


def resolve_books_json_path(source_dir: str, explicit_path: str = "") -> str:
    """
    Resolve the Libation JSON export path.

    An explicit override (e.g. a per-call MCP parameter or CLI flag) wins;
    otherwise defaults to '<source_dir>/libation.json'.

    Args:
        source_dir (str): The Libation source/library directory.
        explicit_path (str): Optional explicit override path.

    Returns:
        str: The resolved path to the libation.json export.
    """
    if explicit_path:
        return explicit_path
    return str(Path(source_dir) / "libation.json")


def log_message(log_file: TextIO, message: str, level: str = "INFO"):
    """
    Write a consistently formatted log line to the given file handle.

    Format: YYYY-MM-DD HH:MM:SS.mmmmmm - LEVEL - message

    Args:
        log_file (TextIO): Open file handle to write the log line to.
        message (str): The message to log.
        level (str): The log level label (default: "INFO").

    Returns:
        None
    """
    timestamp = datetime.now()
    log_file.write(f"{timestamp} - {level} - {message}\n")
    log_file.flush()


def _parse_date(date_str: str) -> str:
    """
    Parse a date string and return it in YYYY-MM-DD format.

    Args:
        date_str (str): The input date string.

    Returns:
        str: The parsed date string in YYYY-MM-DD format.
    """
    # The UTC designation is not important to the purchase date so
    # remove it if it exists
    if date_str.endswith("Z"):
        date_str = date_str[:-1]
    if "+" in date_str:
        split_symbol = "+"
    else:
        split_symbol = "-"
    purchase_date = date_str
    if date_str.rindex(split_symbol) >= 19:
        purchase_date = date_str.rsplit(split_symbol, 1)[0]

    if "." not in purchase_date:
        purchase_date = purchase_date + ".0"
    purchase_dt = datetime.strptime(purchase_date, "%Y-%m-%dT%H:%M:%S.%f")
    return purchase_dt.strftime("%Y-%m-%d")


def make_directory_structure(author_dir: str, series_dir: str, book_title_dir: str, destination_dir: str) -> str:
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
        audio_book_destination_dir = os.path.join(audio_book_destination_dir, series_dir)
    audio_book_destination_dir = os.path.join(audio_book_destination_dir, book_title_dir)
    if not os.path.exists(audio_book_destination_dir):
        os.makedirs(audio_book_destination_dir)
    return audio_book_destination_dir


def sanitize_name(name: str) -> str:
    """
    Sanitize a name by replacing special characters for Unix-safe filenames.
    
    Replaces ampersands with 'and', removes commas, and replaces spaces with underscores.
    Only keeps alphanumeric characters, underscores, and periods.

    Args:
        name (str): The input name to sanitize.

    Returns:
        str: The sanitized name.
    """
    # Replace ampersands with the word "and"
    name_with_and = name.replace("&", "and")
    # Remove commas
    name_without_commas = name_with_and.replace(",", "")
    # Replace spaces with underscores
    name_with_underscores = name_without_commas.replace(" ", "_")
    # Keep only alphanumeric, underscores, and periods
    sanitized = "".join([c for c in name_with_underscores if c.isalnum() or c in ("_", ".")])
    return sanitized.rstrip()


def find_existing_series_folder(author_dir: str, series_name: str, destination_dir: str) -> str:
    """
    Find existing series folder that matches the given series name.

    Checks for existing folders by comparing normalized names to avoid
    creating duplicate folders due to metadata inconsistencies (e.g.,
    "Series-Name" vs "Series Name").

    Args:
        author_dir (str): Sanitized author directory name.
        series_name (str): Original series name from metadata.
        destination_dir (str): Base destination directory.

    Returns:
        str: Existing folder name if found, otherwise sanitized series name.
    """
    if not series_name:
        return ""
    
    author_path = os.path.join(destination_dir, author_dir)
    
    # If author folder doesn't exist, normalize hyphens to spaces for consistency
    # This creates "Series_Name" format: "Series-Name" and "Series Name" both -> "Series_Name"
    if not os.path.exists(author_path):
        normalized_series = series_name.replace("-", " ")
        return sanitize_name(normalized_series)
    
    # Normalize the series name for comparison - remove all separators
    normalized_target = series_name.lower().replace("-", "").replace(" ", "").replace("_", "")
    
    for existing_folder in os.listdir(author_path):
        folder_path = os.path.join(author_path, existing_folder)
        if not os.path.isdir(folder_path):
            continue
        
        normalized_existing = existing_folder.lower().replace("-", "").replace(" ", "").replace("_", "")
        
        # If they match when normalized, use the existing folder name
        if normalized_existing == normalized_target:
            return existing_folder
    
    # No match found, normalize hyphens to spaces for consistency
    # This creates "Series_Name" format: "Series-Name" and "Series Name" both -> "Series_Name"
    normalized_series = series_name.replace("-", " ")
    return sanitize_name(normalized_series)


def generate_libation_json(output_path: str, log_file) -> bool:
    """
    Generate libation.json file using libationcli export command.

    Args:
        output_path (str): Path where the libation.json file should be created.
        log_file: File handle for logging.

    Returns:
        bool: True if the export was successful, False otherwise.
    """
    try:
        log_file.write(f"{datetime.now()} - INFO - Generating libation.json using libationcli...\n")
        log_file.flush()

        # Run libationcli export command
        result = subprocess.run(
            ["libationcli", "export", "--path", output_path, "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0:
            log_file.write(f"{datetime.now()} - INFO - Successfully generated {output_path}\n")
            log_file.flush()
            return True
        else:
            log_file.write(f"{datetime.now()} - ERROR - Failed to generate libation.json\n")
            log_file.write(f"{datetime.now()} - ERROR - Command output: {result.stderr}\n")
            log_file.flush()
            return False

    except FileNotFoundError:
        log_file.write(
            f"{datetime.now()} - ERROR - libationcli not found. Please ensure Libation is installed "
            "and libationcli is in your PATH\n"
        )
        log_file.flush()
        return False
    except Exception as e:
        log_file.write(f"{datetime.now()} - ERROR - Unexpected error generating libation.json: {e}\n")
        log_file.flush()
        return False
