"""Error-path integration tests against unreachable/invalid ABS endpoints."""

import pytest


pytestmark = pytest.mark.usefixtures("abs_healthy")


def test_unreachable_abs_returns_error_dict() -> None:
    """scan_audiobookshelf against an unreachable host returns success=False."""
    from abs_mcp.tools.ingestion import scan_audiobookshelf

    result = scan_audiobookshelf(
        abs_server_url="http://127.0.0.1:1",
        abs_api_token="bogus",
        abs_library_id="bogus",
    )
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert "error" in result


def test_bad_auth_token_returns_error(
    abs_server_url: str, abs_library_id: str
) -> None:
    """match_audiobookshelf with a bad token returns success=False."""
    from abs_mcp.tools.ingestion import match_audiobookshelf

    result = match_audiobookshelf(
        abs_server_url=abs_server_url,
        abs_api_token="invalid_token_xyz",
        abs_library_id=abs_library_id,
    )
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert "error" in result


def test_list_abs_library_cache_failure_returns_error() -> None:
    """list_abs_library with refresh=True against unreachable ABS returns error dict."""
    from abs_mcp.tools.discovery import list_abs_library

    result = list_abs_library(
        abs_server_url="http://127.0.0.1:1",
        abs_api_token="bogus",
        abs_library_id="bogus",
        refresh=True,
    )
    assert isinstance(result, dict)
    assert "error" in result
    assert result["error"] == "cache_refresh_failed"


def test_missing_library_returns_error() -> None:
    """get_status with an unreachable ABS reports unreachable status."""
    from abs_mcp.tools.discovery import get_status

    result = get_status(abs_server_url="http://127.0.0.1:1")
    assert isinstance(result, dict)
    assert "abs_server" in result
    assert result["abs_server"] == "http://127.0.0.1:1"
    status = result.get("abs_status", "")
    assert "unreachable" in status or "Connection refused" in status or "error" in status.lower()
