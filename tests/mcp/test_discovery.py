"""Discovery tool integration tests against a real ABS test instance."""

import pytest


pytestmark = pytest.mark.usefixtures("abs_healthy")


def test_list_libraries_returns_dict() -> None:
    """list_libraries must return a dict with default_library and libraries keys."""
    from abs_mcp.tools.discovery import list_libraries

    result = list_libraries()
    assert isinstance(result, dict)
    assert "libraries" in result
    assert "default_library" in result
    assert isinstance(result["libraries"], dict)


def test_list_abs_library_returns_items(
    abs_server_url: str, abs_api_token: str, abs_library_id: str
) -> None:
    """list_abs_library must return a dict with total_in_library and items keys."""
    from abs_mcp.tools.discovery import list_abs_library

    result = list_abs_library(
        abs_server_url=abs_server_url,
        abs_api_token=abs_api_token,
        abs_library_id=abs_library_id,
    )
    assert isinstance(result, dict)
    assert "total_in_library" in result
    assert "items" in result
    assert isinstance(result["items"], list)


def test_search_abs_library_with_bogus_query(
    abs_server_url: str, abs_api_token: str, abs_library_id: str
) -> None:
    """search_abs_library with a nonexistent query returns empty items."""
    from abs_mcp.tools.discovery import search_abs_library

    result = search_abs_library(
        query="ZZZZNONEXISTENT_QUERY_PLAN3_TEST",
        abs_server_url=abs_server_url,
        abs_api_token=abs_api_token,
        abs_library_id=abs_library_id,
    )
    assert isinstance(result, dict)
    assert "items" in result
    assert isinstance(result["items"], list)
    assert len(result["items"]) == 0


def test_get_status_returns_config(
    abs_server_url: str, abs_api_token: str, abs_library_id: str
) -> None:
    """get_status must return a dict with source_dir, destination_dir, abs_server keys."""
    from abs_mcp.tools.discovery import get_status

    result = get_status(
        abs_server_url=abs_server_url,
        abs_api_token=abs_api_token,
        abs_library_id=abs_library_id,
    )
    assert isinstance(result, dict)
    assert "abs_server" in result
    assert result["abs_server"] == abs_server_url


def test_get_source_status_returns_counts(tmp_path) -> None:
    """get_source_status must return a dict with source and destination keys."""
    from abs_mcp.tools.discovery import get_source_status

    result = get_source_status(
        source_dir=str(tmp_path),
        destination_dir=str(tmp_path / "dst"),
    )
    assert isinstance(result, dict)
    assert "source" in result
    assert "destination" in result
