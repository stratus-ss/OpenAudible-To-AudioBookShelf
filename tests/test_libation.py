import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openaudible_to_ab import move_audio_book_files


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
        # Test case for titles with colons: Libation creates folders with truncated names
        # but files keep the full title (e.g., "Disney Agent Stitch: The M-Files")
        (
            [
                {
                    "AudibleProductId": "DISNEY123",
                    "AuthorNames": "Disney Author",
                    "Title": "Disney Agent Stitch: The M-Files",
                    "DateAdded": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "Disney_Author/Disney_Agent_Stitch_The_MFiles/Disney Agent Stitch: The M-Files [DISNEY123].m4b",
        ),
    ],
)
def test_libation_processing(setup_test_environment, test_books, expected_path_suffix):
    source_dir = Path(setup_test_environment["source_dir"])
    # Dynamically create subfolders and files based on test data
    for book in test_books:
        # Libation creates folder names using only the part before the first colon
        title_for_folder = book["Title"].split(":")[0].strip()
        book_folder_name = f"{title_for_folder} [{book['AudibleProductId']}]"
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
        "copy_instead_of_move": False,
        "destination_dir": setup_test_environment["dest_dir"],
        "download_program": "Libation",
        "libation_folder_cleanup": True,
        "log_file": open(Path(setup_test_environment["tmp_path"]) / "test.log", "a"),
        "purchased_how_long_ago": 7,
        "source_dir": str(source_dir),
        "libation_file_locations_path": "",
    }

    with open(args["books_json_path"], "w") as f:  # Use 'w' to overwrite for each test case
        json.dump(test_books, f)

    result = move_audio_book_files(**args)
    print(result)
    args["log_file"].close()

    expected_path = Path(args["destination_dir"]) / expected_path_suffix
    assert expected_path.exists()
    assert test_books[0]["Title"] in result[0]["title"]


@pytest.mark.parametrize(
    "test_books, expected_path_suffix",
    [
        (
            [
                {
                    "AudibleProductId": "B0C4Z42JNS",
                    "AuthorNames": "James Osiris Baldwin",
                    "Title": "The Archemi Online Chronicles Boxset",
                    "Subtitle": "Books 1, 2 & 3: A LitRPG Epic Fantasy Series (The Archemi Online Chronicles)",
                    "DateAdded": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "James_Osiris_Baldwin/"
            "The_Archemi_Online_Chronicles_Boxset__Books_1_2__3_A_LitRPG_Epic_Fantasy_Series_"
            "The_Archemi_Online_Chronicles/"
            "The Archemi Online Chronicles Boxset: Books 1, 2 & 3: A LitRPG Epic Fantasy Series "
            "(The Archemi Online Chronicles) [B0C4Z42JNS].m4b",
        ),
        (
            [
                {
                    "AudibleProductId": "B0F4L31PTG",
                    "AuthorNames": "Disney Author",
                    "Title": "Disney Agent Stitch: The M-Files",
                    "Subtitle": "Rise of the Mansquito",
                    "DateAdded": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "Disney_Author/Disney_Agent_Stitch_The_MFiles__Rise_of_the_Mansquito/"
            "Disney Agent Stitch: The M-Files: Rise of the Mansquito [B0F4L31PTG].m4b",
        ),
        (
            [
                {
                    "AudibleProductId": "B0DQHNJ9WK",
                    "AuthorNames": "Minecraft Author",
                    "Title": "My Middle Name Is Minecraft",
                    "DateAdded": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "Minecraft_Author/My_Middle_Name_Is_Minecraft/My Middle Name Is Minecraft [B0DQHNJ9WK].m4b",
        ),
    ],
)
def test_libation_with_file_locations_json(setup_test_environment, test_books, expected_path_suffix):
    """Test Libation processing with FileLocationsV2.json"""
    source_dir = Path(setup_test_environment["source_dir"])

    # Create the file structure and FileLocationsV2.json for each book
    file_locations = {"Dictionary": {}}

    for book in test_books:
        # Create the folder structure
        title_for_folder = book["Title"].split(":")[0].strip()
        book_folder = source_dir / f"{title_for_folder} [{book['AudibleProductId']}]"
        book_folder.mkdir(parents=True)

        # Create the file name
        subtitle = book.get("Subtitle", "")
        if subtitle:
            file_name = f"{book['Title']}: {subtitle} [{book['AudibleProductId']}].m4b"
        else:
            file_name = f"{book['Title']} [{book['AudibleProductId']}].m4b"

        source_file = book_folder / file_name
        source_file.touch()

        # Add to FileLocationsV2.json structure
        file_locations["Dictionary"][book["AudibleProductId"]] = [
            {
                "Id": book["AudibleProductId"],
                "FileType": 2,
                "Path": {"Path": f"/tmp/DownloadsInProgress/{book['AudibleProductId']}.aaxc"},
            },
            {"Id": book["AudibleProductId"], "FileType": 1, "Path": {"Path": str(source_file)}},
        ]

    # Write FileLocationsV2.json
    file_locations_path = Path(setup_test_environment["tmp_path"]) / "FileLocationsV2.json"
    with open(file_locations_path, "w") as f:
        json.dump(file_locations, f)

    args = {
        "audio_file_extension": ".m4b",
        "books_json_path": Path(setup_test_environment["tmp_path"]) / "books.json",
        "copy_instead_of_move": False,
        "destination_dir": setup_test_environment["dest_dir"],
        "download_program": "Libation",
        "libation_folder_cleanup": True,
        "log_file": open(Path(setup_test_environment["tmp_path"]) / "test.log", "a"),
        "purchased_how_long_ago": 7,
        "source_dir": str(source_dir),
        "libation_file_locations_path": str(file_locations_path),
    }

    with open(args["books_json_path"], "w") as f:
        json.dump(test_books, f)

    result = move_audio_book_files(**args)
    args["log_file"].close()

    # Verify the file was moved to the correct destination
    expected_path = Path(args["destination_dir"]) / expected_path_suffix
    assert expected_path.exists(), f"Expected file at {expected_path} but it doesn't exist"
    assert test_books[0]["Title"] in result[0]["title"]
