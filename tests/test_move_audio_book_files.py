import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from modules.utils import sanitize_name
from openaudible_to_ab import make_directory_structure, move_audio_book_files, process_open_audible_book_json


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
    "test_data",
    [
        (
            {
                "author": "Old Author",
                "title": "Old Book",
                "asin": "OLD789",
                "filename": "old_book",
                "purchase_date": (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat(),
            }
        ),
        (
            {
                "author": "New Author",
                "title": "New Book",
                "asin": "NEW456",
                "filename": "new_book",
                "purchase_date": (datetime.now(timezone.utc) - timedelta(days=5)).date().isoformat(),
            }
        ),
    ],
)
def test_date_filtering(setup_test_environment, test_data):

    args = {
        "audio_file_extension": ".m4b",
        "books_json_path": os.path.join(setup_test_environment["tmp_path"], "books.json"),
        "copy_instead_of_move": False,
        "destination_dir": setup_test_environment["dest_dir"],
        "download_program": "OpenAudible",
        "libation_folder_cleanup": False,
        "log_file": open(os.path.join(setup_test_environment["tmp_path"], "test.log"), "a"),
        "purchased_how_long_ago": 7,
        "source_dir": setup_test_environment["source_dir"],
    }

    with open(args["books_json_path"], "w") as f:
        json.dump([test_data], f)

    result = move_audio_book_files(**args)
    assert len(result) == 0


@pytest.mark.parametrize(
    "test_data",
    [
        {
            "author": "Conflict Author",
            "title": "Conflict Book",
            "asin": "CONF123",
            "filename": "conflict_book",
            "purchase_date": datetime.now(timezone.utc).date().isoformat(),
        }
    ],
)
def test_existing_file_handling(setup_test_environment, test_data):
    # Create test data with different file sizes
    args = {
        "audio_file_extension": ".m4b",
        "books_json_path": os.path.join(setup_test_environment["tmp_path"], "books.json"),
        "copy_instead_of_move": False,
        "destination_dir": setup_test_environment["dest_dir"],
        "download_program": "OpenAudible",
        "libation_folder_cleanup": False,
        "log_file": open(os.path.join(setup_test_environment["tmp_path"], "test.log"), "a"),
        "purchased_how_long_ago": 7,
        "source_dir": setup_test_environment["source_dir"],
    }

    with open(args["books_json_path"], "w") as f:
        json.dump([test_data], f)

    # Create source file
    source_path = os.path.join(setup_test_environment["source_dir"], f"{test_data['filename']}.m4b")
    with open(source_path, "wb") as f:
        f.write(b"smaller file")  # 12 bytes

    # Create existing destination file
    dest_path = os.path.join(
        setup_test_environment["dest_dir"],
        test_data["author"].replace(" ", "_"),
        test_data["title"].replace(" ", "_"),
        f"{test_data['filename']}.m4b",
    )
    os.makedirs(os.path.dirname(dest_path))
    with open(dest_path, "wb") as f:
        f.write(b"larger existing file content")  # 28 bytes

    result = move_audio_book_files(**args)
    assert os.path.getsize(dest_path) == 28  # Should not replace
    assert len(result) == 0


@pytest.mark.parametrize(
    "invalid_input",
    [
        ("invalid_json.json",),
        ("missing_file.json",),
    ],
)
def test_error_handling(setup_test_environment, invalid_input):
    with pytest.raises(SystemExit):
        move_audio_book_files(
            audio_file_extension=".m4b",
            books_json_path=invalid_input[0],
            copy_instead_of_move=False,
            destination_dir=setup_test_environment["dest_dir"],
            download_program="OpenAudible",
            libation_folder_cleanup=False,
            log_file=open(os.path.join(setup_test_environment["tmp_path"], "test.log"), "a"),
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
    ],
)
def test_make_directory_structure(author: str, series: str, title: str, abs_folder: str, expected_dir: str):
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
