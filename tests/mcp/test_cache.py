"""ABS library cache behavior tests."""

import pytest


pytestmark = pytest.mark.usefixtures("abs_healthy")


def test_first_list_abs_library_fetches_from_abs(
    abs_server_url: str, abs_api_token: str, abs_library_id: str
) -> None:
    """list_abs_library with refresh=True populates the cache and returns items."""
    from abs_mcp.tools.discovery import list_abs_library

    result = list_abs_library(
        abs_server_url=abs_server_url,
        abs_api_token=abs_api_token,
        abs_library_id=abs_library_id,
        refresh=True,
    )
    assert isinstance(result, dict)
    assert "total_in_library" in result
    assert "items" in result
    assert isinstance(result["items"], list)


def test_second_list_abs_library_uses_cache(
    abs_server_url: str, abs_api_token: str, abs_library_id: str
) -> None:
    """A second list_abs_library call (no refresh) returns cached data."""
    from abs_mcp.tools.discovery import list_abs_library

    first = list_abs_library(
        abs_server_url=abs_server_url,
        abs_api_token=abs_api_token,
        abs_library_id=abs_library_id,
        refresh=True,
    )
    second = list_abs_library(
        abs_server_url=abs_server_url,
        abs_api_token=abs_api_token,
        abs_library_id=abs_library_id,
        refresh=False,
    )
    assert first["total_in_library"] == second["total_in_library"]
    assert len(first["items"]) == len(second["items"])


def test_forced_refresh_bypasses_cache(
    abs_server_url: str, abs_api_token: str, abs_library_id: str
) -> None:
    """refresh=True fetches fresh data, ignoring cache."""
    from abs_mcp.tools.discovery import list_abs_library

    list_abs_library(
        abs_server_url=abs_server_url,
        abs_api_token=abs_api_token,
        abs_library_id=abs_library_id,
        refresh=True,
    )
    refreshed = list_abs_library(
        abs_server_url=abs_server_url,
        abs_api_token=abs_api_token,
        abs_library_id=abs_library_id,
        refresh=True,
    )
    assert "items" in refreshed
    assert "total_in_library" in refreshed
