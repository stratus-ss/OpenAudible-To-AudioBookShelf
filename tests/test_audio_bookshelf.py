from datetime import datetime, timedelta, timezone

import pytest
from requests.exceptions import HTTPError

from openaudible_to_audiobookshelf.audio_bookshelf import get_all_books, get_audio_bookshelf_recent_books, update_book_series


BOOK_DATA = [
    {
        "id": "1caa6769-dffd-4594-8be6-b39620f1452e",
        "libraryId": "1cc175ca-88b9-4910-abe5-bf10b8aaa702",
        "addedAt": 1741404484782,
        "media": {
            "metadata": {
                "title": "Daemon",
                "subtitle": "null",
                "authorName": "Daniel Suarez",
                "seriesName": "Daemon #1",
            }
        },
    },
    {
        "id": "fb258245-9ecb-43da-9b20-75ceb3f0511d",
        "libraryId": "1cc175ca-88b9-4910-abe5-bf10b8aaa702",
        "addedAt": 1741442608503,
        "media": {
            "metadata": {
                "title": "The Name of the Wind",
                "subtitle": "Kingkiller Chronicle, Book 1",
                "authorName": "Patrick Rothfuss",
                "seriesName": "Kingkiller Chronicle #1",
            }
        },
    },
    {
        "id": "ab2afda0-479b-4d0d-b145-1c0fa01d8c08",
        "libraryId": "1cc175ca-88b9-4910-abe5-bf10b8aaa702",
        "addedAt": 1741404483947,
        "media": {
            "metadata": {
                "title": "Never Split the Difference",
                "subtitle": "Negotiating as if Your Life Depended on It",
                "authorName": "Chris Voss",
                "seriesName": "",
            }
        },
    },
    {
        "id": "aa9b4d5c-fe04-482d-9296-6b2bfd280f9d",
        "libraryId": "1cc175ca-88b9-4910-abe5-bf10b8aaa702",
        "addedAt": 1741404483037,
        "media": {
            "metadata": {
                "title": "Permanent Record",
                "subtitle": "null",
                "authorName": "Edward Snowden",
                "seriesName": "",
            }
        },
    },
]


@pytest.mark.parametrize(
    "server_url, library_id, abs_api_token, query_params, expected_status, results",
    [
        (
            "http://abs.example.com",
            "123456789",
            "asdflkjanelw123",
            {"sort": "addedAt"},
            200,
            BOOK_DATA,
        ),
        (
            "http://abs.example.com",
            "invalid_library_id",
            "asdflkjanelw123",
            {"sort": "addedAt"},
            404,
            None,
        ),
    ],
)
def test_get_all_books(
    server_url,
    library_id,
    abs_api_token,
    query_params,
    expected_status,
    results,
    mocker,
):
    mock_get = mocker.patch("requests.get")

    mock_response = mocker.MagicMock()
    mock_response.status_code = expected_status
    mock_get.return_value = mock_response
    expected_url = f"{server_url}/api/libraries/{library_id}/items"
    expected_headers = {"Authorization": f"Bearer {abs_api_token}"}

    if expected_status == 200:
        mock_response.json.return_value = {
            "results": results,
            "total": len(results),
            "limit": 0,
            "page": 0,
            "sortBy": "addedAt",
            "sortDesc": False,
            "mediaType": "book",
            "minified": False,
            "collapseseries": False,
        }
    else:
        # For error responses, raise an HTTPError when json() is called
        mock_response.json.side_effect = HTTPError("Not found")

    all_book_response = get_all_books(server_url, library_id, abs_api_token)
    assert all_book_response.status_code == expected_status

    mock_get.assert_called_once_with(
        expected_url,
        headers=expected_headers,
        params={"sort": "addedAt", "limit": 0},
        timeout=30,
    )

    if expected_status == 200:
        response_data = all_book_response.json()
        assert "results" in response_data
        assert response_data["results"] == results


TEST_DATA = [
    (
        {
            "results": [
                {
                    "addedAt": int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000),
                    "media": {"metadata": {"title": "Book 1"}},
                },
                {
                    "addedAt": int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp() * 1000),
                    "media": {"metadata": {"title": "Book 2"}},
                },
            ]
        },
        None,
        1,
        [],
        [
            {
                "addedAt": int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000),
                "media": {"metadata": {"title": "Book 1"}},
            }
        ],
    ),
    (
        {
            "results": [
                {
                    "addedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
                    "media": {"metadata": {"title": "Book 1"}},
                },
                {
                    "addedAt": int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp() * 1000),
                    "media": {"metadata": {"title": "Book 2"}},
                },
            ]
        },
        None,
        0,
        [{"title": "Book 2", "short_title": "Book 2", "asin": "asin2", "series": "", "volumeNumber": ""}],
        [
            {
                "addedAt": int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp() * 1000),
                "media": {"metadata": {"title": "Book 2", "asin": "asin2"}},
                "_original_series": "",
                "_original_volume": "",
            }
        ],
    ),
    ({"results": []}, None, 1, [], []),
    ({"results": []}, None, 0, [{"title": "Book 1", "short_title": "Book 1", "asin": "asin1"}], []),
]


@pytest.mark.parametrize("json_data, log_file, days_ago, book_list, expected_recent_items", TEST_DATA)
def test_get_audio_bookshelf_recent_books(
    json_data, log_file, days_ago, book_list, expected_recent_items, tmp_path, mocker
):
    """Test cases for get_audio_bookshelf_recent_books function."""
    response = mocker.MagicMock()
    response.json.return_value = json_data
    log_file = tmp_path / "test.log"
    log_file.touch()
    recent_items = get_audio_bookshelf_recent_books(response, days_ago=days_ago, book_list=book_list)

    assert recent_items == expected_recent_items


def test_short_title_matching(mocker):
    """short_title should be used for matching instead of the full title."""
    abs_data = {
        "results": [
            {
                "id": "li_abc123",
                "addedAt": 1700000000000,
                "media": {"metadata": {"title": "Master_of_None__All_Trades_Book_1"}},
            }
        ]
    }
    book_list = [
        {
            "title": "Master of None - All Trades, Book 1",
            "short_title": "Master of None",
            "asin": "B0C24R5GP1",
            "series": "All Trades",
            "volumeNumber": "1",
        }
    ]
    response = mocker.MagicMock()
    response.json.return_value = abs_data

    results = get_audio_bookshelf_recent_books(response, book_list=book_list)

    assert len(results) == 0, "Sanitized folder title should NOT contain the short_title substring"


def test_short_title_matching_with_real_abs_title(mocker):
    """ABS often parses a readable title from audio file metadata."""
    abs_data = {
        "results": [
            {
                "id": "li_abc123",
                "addedAt": 1700000000000,
                "media": {"metadata": {"title": "Master of None"}},
            }
        ]
    }
    book_list = [
        {
            "title": "Master of None - All Trades, Book 1",
            "short_title": "Master of None",
            "asin": "B0C24R5GP1",
            "series": "All Trades",
            "volumeNumber": "1",
        }
    ]
    response = mocker.MagicMock()
    response.json.return_value = abs_data

    results = get_audio_bookshelf_recent_books(response, book_list=book_list)

    assert len(results) == 1
    assert results[0]["media"]["metadata"]["asin"] == "B0C24R5GP1"
    assert results[0]["_original_series"] == "All Trades"
    assert results[0]["_original_volume"] == "1"


def test_update_book_series_sends_patch(mocker):
    """update_book_series should PATCH the correct endpoint with series payload."""
    mock_patch = mocker.patch("openaudible_to_audiobookshelf.audio_bookshelf.requests.patch")
    mock_response = mocker.MagicMock()
    mock_response.ok = True
    mock_patch.return_value = mock_response

    result = update_book_series(
        item_id="li_abc123",
        series_name="All Trades",
        volume_number="1",
        server_url="http://abs.example.com",
        abs_api_token="test_token",
    )

    mock_patch.assert_called_once_with(
        "http://abs.example.com/api/items/li_abc123/media",
        json={"metadata": {"series": [{"name": "All Trades", "sequence": "1"}]}},
        headers={"Authorization": "Bearer test_token"},
        timeout=30,
    )
    assert result == mock_response


def test_update_book_series_empty_name_returns_none():
    """update_book_series should return None when series_name is empty."""
    result = update_book_series(
        item_id="li_abc123",
        series_name="",
        volume_number="1",
        server_url="http://abs.example.com",
        abs_api_token="test_token",
    )
    assert result is None
