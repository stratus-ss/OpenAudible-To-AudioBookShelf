import pytest
import json
import os

from pathlib import Path
from datetime import datetime, timezone
from openaudible_to_ab import (
    move_audio_book_files,
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


@pytest.mark.parametrize(
    "test_books, expected_path_suffix",
    [
        (
            [
                {
                    "AudibleProductId": "LIB456",
                    "AuthorNames": "Libation Author",
                    "Title": "Libation Test",
                    "Subtitle": "A Demo",
                    "SeriesOrder": "1",
                    "DateAdded": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "Libation_Author/Libation_Test__A_Demo/Libation Test: A Demo [LIB456].m4b",
        ),
        (
            [
                {
                    "AudibleProductId": "LIB789",
                    "AuthorNames": "Another Author",
                    "Title": "Another Book",
                    "DateAdded": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "Another_Author/Another_Book/Another Book [LIB789].m4b",
        ),
    ],
)
def test_libation_processing(setup_test_environment, test_books, expected_path_suffix):
    source_dir = Path(setup_test_environment["source_dir"])
    # Dynamically create subfolders and files based on test data
    for book in test_books:
        book_folder_name = f"{book['Title']} [{book['AudibleProductId']}]"
        source_dir_with_subfolder = source_dir / book_folder_name
        os.makedirs(source_dir_with_subfolder)

        subtitle = book.get("Subtitle", "")
        print(subtitle)
        if subtitle:
            file_name = f"{book['Title']}: {subtitle} [{book['AudibleProductId']}].m4b"
        else:
            file_name = f"{book['Title']} [{book['AudibleProductId']}].m4b"
        print(file_name)
        source_file = source_dir_with_subfolder / file_name
        source_file.touch()
    assert source_file.exists(), f"Source file {source_file} was not created."

    args = {
        "audio_file_extension": ".m4b",
        "books_json_path": Path(setup_test_environment["tmp_path"]) / "books.json",
        "destination_dir": setup_test_environment["dest_dir"],
        "download_program": "Libation",
        "libation_folder_cleanup": True,
        "log_file": open(Path(setup_test_environment["tmp_path"]) / "test.log", "a"),
        "purchased_how_long_ago": 7,
        "source_dir": str(source_dir),
    }

    with open(
        args["books_json_path"], "w"
    ) as f:  # Use 'w' to overwrite for each test case
        json.dump(test_books, f)

    result = move_audio_book_files(**args)
    args["log_file"].close()

    expected_path = Path(args["destination_dir"]) / expected_path_suffix
    assert expected_path.exists()
    assert test_books[0]["Title"] in result
