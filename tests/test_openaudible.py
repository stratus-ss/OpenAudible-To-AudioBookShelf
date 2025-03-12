import pytest
import json
import os

from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import mock_open, patch
from openaudible_to_ab import (
    move_audio_book_files,
    sanitize_name,
    make_directory_structure,
    process_open_audible_book_json,
    process_libation_book_json,
)


@pytest.fixture
def setup_test_environment(tmp_path):
    # Create temporary directories
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "dest"
    source_dir.mkdir()
    dest_dir.mkdir()

    return {
        "source_dir": str(source_dir),
        "dest_dir": str(dest_dir),
        "tmp_path": str(tmp_path),
    }


def test_open_audible_processing(setup_test_environment):
    # Create test JSON data
    test_books = [
        {
            "author": "Test Author, Jr.",
            "title": "Sample Book",
            "title_short": "Sample Book",
            "asin": "TEST123",
            "filename": "sample_book",
            "purchase_date": datetime.now(timezone.utc).date().isoformat(),
            "series_name": "Test Series",
        }
    ]

    # Create test files using Path
    source_dir = Path(setup_test_environment["source_dir"])
    source_file = source_dir / "sample_book.m4b"
    source_file.touch()

    # Test parameters
    args = {
        "audio_file_extension": ".m4b",
        "books_json_path": Path(setup_test_environment["tmp_path"]) / "books.json",
        "destination_dir": setup_test_environment["dest_dir"],
        "download_program": "OpenAudible",
        "libation_folder_cleanup": False,
        "log_file": open(Path(setup_test_environment["tmp_path"]) / "test.log", "a"),
        "purchased_how_long_ago": 7,
        "source_dir": str(source_dir),  # Ensure string path for code compatibility
    }

    # Write test JSON file
    with open(args["books_json_path"], "a") as f:
        json.dump(test_books, f)

    result = move_audio_book_files(**args)
    args["log_file"].close()
    # Verify path construction
    expected_path = (
        Path(args["destination_dir"])
        / "Test_Author"  # Sanitized from "Test Author, Jr."
        / "Test_Series"
        / "Sample_Book"
        / "sample_book.m4b"
    )
    assert expected_path.exists()
    assert "Sample Book" in result
