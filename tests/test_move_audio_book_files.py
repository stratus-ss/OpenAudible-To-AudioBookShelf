# import pytest
# import os
# import shutil
# import json
# from openaudible_to_ab import (
#     move_audio_book_files,
#     sanitize_name,
#     make_directory_structure,
#     process_open_audible_book_json,
#     process_libation_book_json,
# )
# from datetime import datetime, timedelta, timezone


# @pytest.fixture
# def test_data():
#     books_json = [
#         {
#             "asin": "B07M82V9CG",
#             "author": "Craig Smith",
#             "summary": "A very interesting book",
#             "filename": "test_book1",
#             "purchase_date": "2024-07-24",
#             "series_name": "Test Series",
#             "title_short": "Test Book 1",
#             "title": "Test Book 1: The Beginning",
#             "series_sequence": 1,
#         },
#         {
#             "asin": "B08N57W3PZ",
#             "author": "Jane Doe",
#             "summary": "Another captivating novel",
#             "filename": "test_book2",
#             "purchase_date": (datetime.now(timezone.utc)).strftime("%Y-%m-%d"),
#             "series_name": "",
#             "title_short": "Test Book 2",
#             "title": "Test Book 2: The Sequel",
#             "series_sequence": None,
#         },
#     ]

#     libation_books_json = [
#         {
#             "Account": "account",
#             "AudibleProductId": "B07M82V9CG",
#             "AudioFormat": "format",
#             "AuthorNames": "Craig Smith",
#             "BookStatus": "status",
#             "CategoriesNames": "categories",
#             "ContentType": "type",
#             "DateAdded": "2024-07-24T12:00:00.0",
#             "DatePublished": "date",
#             "Description": "A very interesting book",
#             "HasPdf": True,
#             "Language": "language",
#             "LengthInMinutes": 120,
#             "Locale": "locale",
#             "NarratorNames": "narrator",
#             "Publisher": "publisher",
#             "SeriesNames": "Test Series",
#             "SeriesOrder": "1 of 3",
#             "Subtitle": "The Beginning",
#             "Title": "Test Book 1",
#         },
#         {
#             "Account": "account",
#             "AudibleProductId": "B08N57W3PZ",
#             "AudioFormat": "format",
#             "AuthorNames": "Jane Doe",
#             "BookStatus": "status",
#             "CategoriesNames": "categories",
#             "ContentType": "type",
#             "DateAdded": "2024-07-25T12:00:00.0",
#             "DatePublished": "date",
#             "Description": "Another captivating novel",
#             "HasPdf": False,
#             "Language": "language",
#             "LengthInMinutes": 150,
#             "Locale": "locale",
#             "NarratorNames": "narrator",
#             "Publisher": "publisher",
#             "SeriesNames": None,
#             "SeriesOrder": None,
#             "Subtitle": "The Sequel",
#             "Title": "Test Book 2",
#         },
#     ]
#     return books_json, libation_books_json


# @pytest.fixture
# def test_environment():
#     source_dir = "test_source"
#     destination_dir = "test_destination"
#     audio_file_extension = ".m4b"
#     books_json_path = "test_books.json"
#     log_file_path = "test_log.txt"
#     purchased_how_long_ago = 7

#     os.makedirs(source_dir, exist_ok=True)
#     os.makedirs(destination_dir, exist_ok=True)

#     yield source_dir, destination_dir, audio_file_extension, books_json_path, log_file_path, purchased_how_long_ago

#     shutil.rmtree(source_dir, ignore_errors=True)
#     shutil.rmtree(destination_dir, ignore_errors=True)
#     if os.path.exists(log_file_path):
#         os.remove(log_file_path)


# @pytest.mark.parametrize("download_program", ["OpenAudible", "Libation"])
# def test_move_audio_book_files_empty_json(mocker, test_environment, download_program):
#     (
#         source_dir,
#         destination_dir,
#         audio_file_extension,
#         books_json_path,
#         log_file_path,
#         purchased_how_long_ago,
#     ) = test_environment

#     mocker.patch("builtins.open", mocker.mock_open(read_data=json.dumps([])))
#     mocker.patch("os.path.exists", return_value=True)
#     mock_move = mocker.patch("shutil.move")
#     with open(log_file_path, "w") as log_file:
#         book_list = move_audio_book_files(
#             audio_file_extension,
#             books_json_path,
#             destination_dir,
#             download_program,
#             False,
#             log_file,
#             purchased_how_long_ago,
#             source_dir,
#         )
#     assert book_list == []
#     mock_move.assert_not_called()


# def test_move_audio_book_files_invalid_json(mocker, test_environment):
#     (
#         source_dir,
#         destination_dir,
#         audio_file_extension,
#         books_json_path,
#         log_file_path,
#         purchased_how_long_ago,
#     ) = test_environment
#     with mocker.patch("builtins.open", mocker.mock_open(read_data="invalid json")):
#         # Create the log file *before* patching 'open'
#         open(log_file_path, "w").close()
#         with open(log_file_path, "a") as log_file:
#             with pytest.raises(SystemExit):
#                 move_audio_book_files(
#                     audio_file_extension,
#                     books_json_path,
#                     destination_dir,
#                     "OpenAudible",
#                     False,
#                     log_file,
#                     purchased_how_long_ago,
#                     source_dir,
#                 )


# def test_sanitize_name():
#     assert sanitize_name("Test, Name with spaces") == "Test_Name_with_spaces"
#     assert sanitize_name("Name.with.dots") == "Name.with.dots"
#     assert sanitize_name("Name with invalid : / \\ ? * |") == "Name_with_invalid"


# def test_make_directory_structure(test_environment):
#     (
#         source_dir,
#         destination_dir,
#         audio_file_extension,
#         books_json_path,
#         log_file_path,
#         purchased_how_long_ago,
#     ) = test_environment

#     author_dir = "Test Author"
#     series_dir = "Test Series"
#     book_title_dir = "Test Book"
#     expected_path = os.path.join(
#         destination_dir, author_dir, series_dir, book_title_dir
#     )
#     result_path = make_directory_structure(
#         author_dir, series_dir, book_title_dir, destination_dir
#     )
#     assert result_path == expected_path
#     assert os.path.exists(expected_path)


# def test_process_open_audible_book_json(test_data):
#     books_json, _ = test_data
#     book_data = books_json[0]
#     processed_data = process_open_audible_book_json(book_data)
#     assert processed_data["author"] == "Craig Smith"
#     assert processed_data["series"] == "Test Series"
#     assert processed_data["volumeNumber"] == 1


# def test_process_libation_book_json(test_data):
#     _, libation_books_json = test_data
#     book_data = libation_books_json[0]
#     processed_data = process_libation_book_json(book_data)
#     assert processed_data["author"] == "Craig Smith"
#     assert processed_data["series"] == "Test Series"
#     assert processed_data["volumeNumber"] == "1"
#     assert processed_data["purchase_date"] == "2024-07-24"

import pytest
import json
import os
import shutil
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


def test_libation_processing(setup_test_environment):
    # Create test JSON data
    test_books = [
        {
            "AudibleProductId": "LIB456",
            "AuthorNames": "Libation Author",
            "Title": "Libation Test",
            "Subtitle": "A Demo",
            "SeriesOrder": "1",
            "DateAdded": datetime.now(timezone.utc).isoformat(),
        }
    ]

    source_dir = Path(setup_test_environment["source_dir"])
    source_dir_with_subfolder = source_dir / "Libation Test [LIB456]"
    os.makedirs(source_dir_with_subfolder)
    source_file = source_dir_with_subfolder / "Libation Test: A Demo [LIB456].m4b"
    source_file.touch()

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

    with open(args["books_json_path"], "a") as f:
        json.dump(test_books, f)

    result = move_audio_book_files(**args)
    args["log_file"].close()
    expected_path = (
        Path(args["destination_dir"])
        / "Libation_Author"
        / "Libation_Test__A_Demo"
        / "Libation Test: A Demo [LIB456].m4b"
    )
    print("Expected path:", expected_path)
    print("Destination dir contents:", os.listdir(args["destination_dir"]))
    print("Result from move_audio_book_files:", result)
    assert expected_path.exists()
    assert "Libation Test" in result


def test_date_filtering(setup_test_environment):
    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    test_books = [
        {
            "author": "Old Author",
            "title": "Old Book",
            "asin": "OLD789",
            "filename": "old_book",
            "purchase_date": old_date,
        }
    ]

    args = {
        "audio_file_extension": ".m4b",
        "books_json_path": os.path.join(
            setup_test_environment["tmp_path"], "books.json"
        ),
        "destination_dir": setup_test_environment["dest_dir"],
        "download_program": "OpenAudible",
        "libation_folder_cleanup": False,
        "log_file": open(
            os.path.join(setup_test_environment["tmp_path"], "test.log"), "a"
        ),
        "purchased_how_long_ago": 7,
        "source_dir": setup_test_environment["source_dir"],
    }

    with open(args["books_json_path"], "a") as f:
        json.dump(test_books, f)

    result = move_audio_book_files(**args)
    assert len(result) == 0


def test_existing_file_handling(setup_test_environment):
    # Create test data with different file sizes
    test_books = [
        {
            "author": "Conflict Author",
            "title": "Conflict Book",
            "asin": "CONF123",
            "filename": "conflict_book",
            "purchase_date": datetime.now(timezone.utc).date().isoformat(),
        }
    ]

    # Create source file
    source_path = os.path.join(
        setup_test_environment["source_dir"], "conflict_book.m4b"
    )
    with open(source_path, "wb") as f:
        f.write(b"smaller file")  # 12 bytes

    # Create existing destination file
    dest_path = os.path.join(
        setup_test_environment["dest_dir"],
        "Conflict_Author",
        "Conflict_Book",
        "conflict_book.m4b",
    )
    os.makedirs(os.path.dirname(dest_path))
    with open(dest_path, "wb") as f:
        f.write(b"larger existing file content")  # 28 bytes

    args = {
        "audio_file_extension": ".m4b",
        "books_json_path": os.path.join(
            setup_test_environment["tmp_path"], "books.json"
        ),
        "destination_dir": setup_test_environment["dest_dir"],
        "download_program": "OpenAudible",
        "libation_folder_cleanup": False,
        "log_file": open(
            os.path.join(setup_test_environment["tmp_path"], "test.log"), "a"
        ),
        "purchased_how_long_ago": 7,
        "source_dir": setup_test_environment["source_dir"],
    }

    with open(args["books_json_path"], "a") as f:
        json.dump(test_books, f)

    result = move_audio_book_files(**args)
    assert os.path.getsize(dest_path) == 28  # Should not replace
    assert len(result) == 0


def test_error_handling(setup_test_environment):
    # Test invalid JSON
    with pytest.raises(SystemExit):
        move_audio_book_files(
            audio_file_extension=".m4b",
            books_json_path="nonexistent.json",
            destination_dir=setup_test_environment["dest_dir"],
            download_program="OpenAudible",
            libation_folder_cleanup=False,
            log_file=open(
                os.path.join(setup_test_environment["tmp_path"], "test.log"), "a"
            ),
            purchased_how_long_ago=7,
            source_dir=setup_test_environment["source_dir"],
        )
