import os
from datetime import datetime


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
