import pytest
import os
from modules.utils import _parse_date, make_directory_structure, sanitize_name


@pytest.mark.parametrize(
    "date_str, expected",
    [
        ("2024-04-24T14:35:02.174Z", "2024-04-24"),  # Basic test
        ("2024-04-24T14:35:02Z", "2024-04-24"),  # Test with no ms
        ("2024-04-24T14:35:02.174+04:00", "2024-04-24"),  # Test with timezone offset
        ("2024-04-24T14:35:02+02:00", "2024-04-24"),  # Timezone offset no ms
        ("2024-12-31T23:59:59.999Z", "2024-12-31"),  # Test with end of year
        ("2024-01-01T00:00:00.000Z", "2024-01-01"),  # Test with start of year
        ("2024-02-29T12:00:00.000Z", "2024-02-29"),  # Test with leap year
    ],
    ids=[
        "utc",
        "utc_no_ms",
        "timezone_offset",
        "timezone_no_ms",
        "end_of_year",
        "start_of_year",
        "leap_year",
    ],
)
def test_valid_date_formats(date_str, expected):
    # Act
    result = _parse_date(date_str)
    # Assert
    assert result == expected


@pytest.mark.parametrize(
    "author_dir, series_dir, book_title_dir, destination_dir",
    [
        ("Author1", "Series1", "Book1", "test_dir"),  # Basic test
        ("Author2", None, "Book2", "test_dir"),  # Test with no series
        ("Author3", "Series3", "Book3", ""),  # Test with empty destination
    ],
    ids=["basic", "no_series", "empty_destination"],
)
def test_make_directory_structure(
    author_dir, series_dir, book_title_dir, destination_dir, tmp_path
):

    # Arrange
    temp_dest = tmp_path / destination_dir

    # Act
    result = make_directory_structure(
        author_dir, series_dir, book_title_dir, str(temp_dest)
    )

    # Assert
    expected_dir = temp_dest / author_dir
    if series_dir:
        expected_dir = expected_dir / series_dir
    expected_dir = expected_dir / book_title_dir
    assert result == str(expected_dir)
    assert os.path.exists(expected_dir)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Name, with commas", "Name_with_commas"),  # Commas replaced
        ("Name with spaces", "Name_with_spaces"),  # Spaces replaced
        ("Name.with.periods", "Name.with.periods"),  # Periods preserved
        ("Name_with_underscores", "Name_with_underscores"),  # Underscores preserved
        (
            "Name invalid characters!",
            "Name_invalid_characters",
        ),  # Invalid characters removed
        (
            "Name with trailing spaces   ",
            "Name_with_trailing_spaces___",
        ),  # Trailing spaces removed
        ("", ""),  # Empty string
    ],
    ids=[
        "commas",
        "spaces",
        "periods",
        "underscores",
        "invalid_chars",
        "trailing_spaces",
        "empty_string",
    ],
)
def test_sanitize_name(name, expected):
    # Act
    result = sanitize_name(name)
    # Assert
    assert result == expected
