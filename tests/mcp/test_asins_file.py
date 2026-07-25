"""ASIN file handoff tests (no ABS required — pure filesystem behavior)."""


def test_write_read_roundtrip(tmp_path) -> None:
    """Write then read ASINs via the file handoff helpers."""
    from abs_mcp.tools.ingestion import _read_downloaded_asins, _write_downloaded_asins

    src = tmp_path / "libation_books"
    src.mkdir()
    _write_downloaded_asins(str(src), ["B0001AAA", "B0002BBB"])
    result = _read_downloaded_asins(str(src))
    assert result == ["B0001AAA", "B0002BBB"]


def test_missing_file_returns_empty(tmp_path) -> None:
    """_read_downloaded_asins on a missing file returns an empty list, not an error."""
    from abs_mcp.tools.ingestion import _read_downloaded_asins

    nonexistent = tmp_path / "does_not_exist"
    result = _read_downloaded_asins(str(nonexistent))
    assert result == []


def test_json_corruption_returns_empty(tmp_path) -> None:
    """_read_downloaded_asins on corrupted JSON returns an empty list, not an error."""
    from abs_mcp.tools.ingestion import _read_downloaded_asins, _write_downloaded_asins

    src = tmp_path / "corrupt_src"
    src.mkdir()
    (src / "last_download.json").write_text("{not valid json", encoding="utf-8")
    result = _read_downloaded_asins(str(src))
    assert result == []
