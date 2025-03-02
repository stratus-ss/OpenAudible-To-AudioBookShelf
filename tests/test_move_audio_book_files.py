import pytest
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
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


@pytest.mark.parametrize(
    "text,expected_transformed_text",
    [
        ("some text", "some_text"),
        ("other.,text", "other.text"),
        ("valid_text", "valid_text"),
    ],
)
def test_sanitize_name(text, expected_transformed_text):
    sanitzed_text = sanitize_name(text)
    assert sanitzed_text == expected_transformed_text


@pytest.mark.parametrize(
    "author,series,title,abs_folder,expected_dir",
    [
        (
            "Frank_Sin",
            "Into_the_Beyond",
            "An_Intro",
            "/tmp/audio_books",
            "/tmp/audio_books/Frank_Sin/Into_the_Beyond/An_Intro",
        )
    ]
)
def test_make_directory_structure(author: str, series: str, title: str,
                                  abs_folder: str, expected_dir: str):
    output = make_directory_structure(author, series, title, abs_folder)
    assert output == expected_dir
    assert os.path.exists(output)


@pytest.mark.parametrize(
    "book_data,expected_book_data",
    [
        (
            {
                "asin": 1234,
                "author": "Frank Sin",
                "summary": "This is a fake book",
                "filename": "Some book.m4b",
                "purchase_date": "2025-01-01",
                "series_name": "An Interesting Series",
                "title_short": "Just A Book",
                "title": "Just A Book: Volume 1",
                "series_sequence": "1",
            },
            {
                "asin": 1234,
                "author": "Frank_Sin",
                "description": "This is a fake book",
                "filename": "Some book.m4b",
                "purchase_date": "2025-01-01",
                "series": "An Interesting Series",
                "short_title": "Just A Book",
                "title": "Just A Book: Volume 1",
                "volumeNumber": "1",
            },
        )
    ],
)
def test_process_open_audible_book_json(book_data, expected_book_data):
    processed_book = process_open_audible_book_json(book_data.copy())
    assert processed_book == expected_book_data


