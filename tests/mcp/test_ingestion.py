"""Ingestion tool integration tests against a real ABS test instance."""

import pytest


pytestmark = pytest.mark.usefixtures("abs_healthy")


def test_scan_audible_returns_dict() -> None:
    """scan_audible must return a dict (not a string) per Plan 3 Task 9."""
    from abs_mcp.tools.ingestion import scan_audible

    result = scan_audible()
    assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"
    assert "step" in result
    assert "success" in result


def test_scan_audiobookshelf_happy_path(
    abs_server_url: str, abs_api_token: str, abs_library_id: str
) -> None:
    """scan_audiobookshelf must return success=True against a healthy ABS."""
    from abs_mcp.tools.ingestion import scan_audiobookshelf

    result = scan_audiobookshelf(
        abs_server_url=abs_server_url,
        abs_api_token=abs_api_token,
        abs_library_id=abs_library_id,
    )
    assert isinstance(result, dict)
    assert result.get("success") is True, f"Expected success=True, got {result}"


def test_organize_books_with_empty_source_returns_zero(tmp_path) -> None:
    """organize_books on an empty source dir with empty libation.json returns success=True."""
    from abs_mcp.tools.ingestion import organize_books

    empty_src = tmp_path / "empty_src"
    empty_dst = tmp_path / "empty_dst"
    empty_src.mkdir()
    empty_dst.mkdir()
    (empty_src / "libation.json").write_text("[]")

    result = organize_books(
        purchased_how_long_ago=0,
        source_dir=str(empty_src),
        destination_dir=str(empty_dst),
    )
    assert isinstance(result, dict)
    assert result.get("success") is True, f"Expected success=True, got {result}"


def test_get_cleaning_progress_returns_dict() -> None:
    """get_cleaning_progress must return a dict (empty when no operation active)."""
    from abs_mcp.tools.ingestion import get_cleaning_progress

    result = get_cleaning_progress()
    assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"
