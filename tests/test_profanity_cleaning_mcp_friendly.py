"""Tests for the profanity_cleaning_mcp_friendly plan changes.

Covers:
  - DR-1: process_audio_file raises AudioCleaningError on failure
  - DR-2: per-stage progress events (staging → uploading → transcribing → done/failed)
  - DR-5: cleaning_failures[] surfaced in step_organize response when AudioCleaningError raised
  - DR-6: organize_books uses async job pattern — returns job_id, get_job_result retrieves
"""

import asyncio
import io
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from openaudible_to_audiobookshelf.config import Config


# ---------------------------------------------------------------------------
# Module loading helpers (mcp_server lives in abs-mcp/, not src/)
# ---------------------------------------------------------------------------

def _load_mcp_server_module():
    """Load abs-mcp/mcp_server.py without triggering FastMCP startup."""
    repo_root = Path(__file__).resolve().parent.parent
    mcp_dir = repo_root / "abs-mcp"
    mcp_path = mcp_dir / "mcp_server.py"
    if not mcp_path.is_file():
        return None
    mcp_dir_str = str(mcp_dir)
    if mcp_dir_str not in sys.path:
        sys.path.insert(0, mcp_dir_str)
    # Prevent .env loading so test env is deterministic
    os.environ["MCP_ENV_FILE"] = ""
    spec = importlib.util.spec_from_file_location("mcp_server_under_test", mcp_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MCP_SERVER = _load_mcp_server_module()


# ---------------------------------------------------------------------------
# DR-2: per-stage progress events
# ---------------------------------------------------------------------------


def test_process_audio_file_fires_per_stage_events(tmp_path) -> None:
    """DR-2: process_audio_file fires _fire_progress with stage field for each phase."""
    from openaudible_to_audiobookshelf.audio_cleaner import AudioCleaner

    captured_events = []

    def _capture(**kwargs):
        captured_events.append(kwargs)

    cfg = Config()
    cfg.working_directory = str(tmp_path / "working")
    cfg.save_transcripts = False
    cfg.enable_profanity_cleaning = True
    cfg.remote_whisper_url = "http://whisper:8000"
    cfg.swears_file = str(tmp_path / "swears.txt")
    Path(cfg.swears_file).write_text("anal\n")
    cfg.timeout = 60
    cfg.beep_mode = False
    cfg.confidence_threshold = 0.7
    cfg.copy_instead_of_move = False

    log_file = io.StringIO()
    cleaner = AudioCleaner(cfg, log_file)
    cleaner._progress_callback = _capture
    cleaner.set_book_count(1)

    src = str(tmp_path / "src.m4b")
    Path(src).write_bytes(b"fake audio")

    # Mock the WhisperPlugger to simulate success without hitting a real backend
    fake_cleaned = str(tmp_path / "cleaned.m4b")
    Path(fake_cleaned).write_bytes(b"cleaned audio")

    mock_plugger = MagicMock()
    mock_plugger.EncodeCleanAudio.return_value = fake_cleaned

    with patch("openaudible_to_audiobookshelf.audio_cleaner.socket.create_connection"), \
         patch("openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger", return_value=mock_plugger):
        with patch("os.path.getsize", return_value=50 * 1024 * 1024):  # 50MB
            with patch("os.path.exists", return_value=True):
                result = cleaner.process_audio_file(src, {"title": "T", "asin": "TEST_DR2"})

    assert result == fake_cleaned
    # Inspect captured events for stage progression
    stages = [e.get("stage") for e in captured_events]
    assert "staging" in stages
    assert "uploading" in stages
    assert "transcribing" in stages
    assert "done" in stages
    # Each event has books_done + books_total
    for e in captured_events:
        if "stage" in e:
            assert "books_done" in e
            assert "books_total" in e
            assert e["books_total"] == 1


# ---------------------------------------------------------------------------
# DR-5: cleaning_failures[] in step_organize response
# ---------------------------------------------------------------------------


def test_step_organize_records_cleaning_failure_in_response(tmp_path, monkeypatch) -> None:
    """DR-5: When process_audio_file raises AudioCleaningError, the failure is
    recorded in step_organize response's cleaning_failures[] field.
    """
    from openaudible_to_audiobookshelf.audio_cleaner import AudioCleaningError
    from openaudible_to_audiobookshelf import pipeline as pipeline_mod

    # Set up a fake source dir with one book that will fail cleaning
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()
    (src / "libation.json").write_text(json.dumps([
        {
            "AudibleProductId": "B0FAIL_DR5",
            "Title": "Failing Book",
            "Subtitle": "",
            "AuthorNames": "Test Author",
            "SeriesNames": "",
            "DateAdded": "2026-07-24T00:00:00Z",
            "Description": "",
            "SeriesOrder": "",
        }
    ]))
    # Create the actual audio file at the path constructed by move_audio_book_files
    # (source_dir/<libation_book_folder>/<filename>.ext)
    book_dir = src / "Failing Book [B0FAIL_DR5]"
    book_dir.mkdir()
    (book_dir / "Failing Book [B0FAIL_DR5].m4b").write_bytes(b"fake audio")

    # Stub audio_cleaner.process_audio_file to raise AudioCleaningError
    def _failing_clean(*args, **kwargs):
        raise AudioCleaningError("Simulated failure")

    monkeypatch.setattr(
        "openaudible_to_audiobookshelf.audio_cleaner.AudioCleaner.process_audio_file",
        _failing_clean,
    )

    cfg = Config()
    cfg.source_audio_book_directory = str(src)
    cfg.destination_book_directory = str(dst)
    cfg.audio_file_extension = ".m4b"
    cfg.copy_instead_of_move = False
    cfg.libation_folder_cleanup = False
    cfg.download_program = "Libation"
    cfg.purchased_how_long_ago = 0
    cfg.libation_file_locations_path = ""
    cfg.enable_profanity_cleaning = True
    cfg.remote_whisper_url = "http://whisper:8000"
    cfg.swears_file = str(tmp_path / "swears.txt")
    Path(cfg.swears_file).write_text("anal\n")
    cfg.working_directory = str(tmp_path / "working")
    cfg.save_transcripts = False
    cfg.timeout = 60
    cfg.beep_mode = False
    cfg.confidence_threshold = 0.7

    result = pipeline_mod.step_organize(cfg, asins=["B0FAIL_DR5"])

    assert result["step"] == "organize"
    assert "cleaning_failures" in result, (
        f"Expected cleaning_failures key in step_organize response; got {list(result.keys())}"
    )
    failures = result["cleaning_failures"]
    assert len(failures) == 1
    assert failures[0]["asin"] == "B0FAIL_DR5"
    assert failures[0]["title"] == "Failing Book"
    assert "Simulated failure" in failures[0]["error"]
    # The failed book should NOT have been moved to the destination dir
    # (the move is skipped via `continue` after the AudioCleaningError except).
    dest_book_dir = dst / "Test Author" / "Failing Book"
    assert not dest_book_dir.exists(), (
        f"Expected destination dir NOT to exist for failed book; found {dest_book_dir}"
    )


def test_step_organize_caps_cleaning_failures_at_50(tmp_path, monkeypatch) -> None:
    """DR-5: cleaning_failures[] capped at 50 entries with '+N more failures' note."""
    from openaudible_to_audiobookshelf.audio_cleaner import AudioCleaningError
    from openaudible_to_audiobookshelf import pipeline as pipeline_mod

    # Create 75 books that all fail cleaning
    books = []
    for i in range(75):
        asin = f"B0CAP{i:04d}"
        books.append({
            "AudibleProductId": asin,
            "Title": f"Book {i}",
            "Subtitle": "",
            "AuthorNames": "Cap Author",
            "SeriesNames": "",
            "DateAdded": "2026-07-24T00:00:00Z",
            "Description": "",
            "SeriesOrder": "",
        })
        book_dir = tmp_path / "src" / f"Book {i} [{asin}]"
        book_dir.mkdir(parents=True, exist_ok=True)
        (book_dir / f"Book {i} [{asin}].m4b").write_bytes(b"x")
    (tmp_path / "src" / "libation.json").write_text(json.dumps(books))

    def _fail(*args, **kwargs):
        raise AudioCleaningError("always fails")

    monkeypatch.setattr(
        "openaudible_to_audiobookshelf.audio_cleaner.AudioCleaner.process_audio_file",
        _fail,
    )

    cfg = Config()
    cfg.source_audio_book_directory = str(tmp_path / "src")
    cfg.destination_book_directory = str(tmp_path / "dst")
    cfg.audio_file_extension = ".m4b"
    cfg.copy_instead_of_move = False
    cfg.libation_folder_cleanup = False
    cfg.download_program = "Libation"
    cfg.purchased_how_long_ago = 0
    cfg.libation_file_locations_path = ""
    cfg.enable_profanity_cleaning = True
    cfg.remote_whisper_url = "http://whisper:8000"
    cfg.swears_file = str(tmp_path / "swears.txt")
    Path(cfg.swears_file).write_text("x\n")
    cfg.working_directory = str(tmp_path / "working")
    cfg.save_transcripts = False
    cfg.timeout = 60
    cfg.beep_mode = False
    cfg.confidence_threshold = 0.7

    result = pipeline_mod.step_organize(cfg)

    failures = result["cleaning_failures"]
    # Should be 50 entries + 1 "+N more" note
    assert len(failures) == 51
    assert "+25 more failures" in failures[-1]["title"]


# ---------------------------------------------------------------------------
# DR-6: async job pattern for organize_books
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _MCP_SERVER is None, reason="abs-mcp/mcp_server.py could not be imported"
)
def test_organize_books_returns_job_id_immediately(tmp_path, monkeypatch) -> None:
    """DR-6: organize_books returns {"job_id": str, "status": "started"} immediately."""
    mcp = _MCP_SERVER

    empty_src = tmp_path / "src"
    empty_dst = tmp_path / "dst"
    empty_src.mkdir()
    empty_dst.mkdir()
    (empty_src / "libation.json").write_text("[]")
    mcp._last_downloaded_asins = []

    def _stub_config(**_kwargs):
        cfg = Config()
        cfg.source_audio_book_directory = str(empty_src)
        cfg.destination_book_directory = str(empty_dst)
        cfg.audio_file_extension = ".m4b"
        cfg.copy_instead_of_move = False
        cfg.libation_folder_cleanup = False
        cfg.download_program = "Libation"
        cfg.purchased_how_long_ago = 0
        cfg.libation_file_locations_path = ""
        cfg.enable_profanity_cleaning = False
        return cfg

    monkeypatch.setattr(mcp, "_build_config", _stub_config)
    monkeypatch.setattr(mcp, "_detect_audio_extension", lambda _p: "")

    # Make step_organize take a moment so we can verify the async return is fast
    def _slow_step_organize(cfg, **_kw):
        time.sleep(0.5)
        return {"step": "organize", "success": True, "processed_count": 0,
                "moved": [], "skipped": [], "skipped_reasons": {},
                "total_in_source": 0, "applied_asins": [],
                "destination_dir": cfg.destination_book_directory,
                "cleaning": {}, "log": "", "_book_list": []}

    monkeypatch.setattr(mcp, "step_organize", _slow_step_organize)

    async def _drive():
        t0 = time.monotonic()
        result = await mcp.organize_books(audio_file_extension=".m4b")
        elapsed_ms = (time.monotonic() - t0) * 1000
        return result, elapsed_ms

    result, elapsed_ms = asyncio.run(_drive())
    assert "job_id" in result
    assert result["status"] == "started"
    assert isinstance(result["job_id"], str) and len(result["job_id"]) == 12
    # The async wrapper returns BEFORE step_organize finishes (step_organize sleeps 0.5s)
    assert elapsed_ms < 200, f"organize_books should return immediately (<200ms); took {elapsed_ms:.0f}ms"


@pytest.mark.skipif(
    _MCP_SERVER is None, reason="abs-mcp/mcp_server.py could not be imported"
)
def test_get_job_result_returns_completed_result(tmp_path, monkeypatch) -> None:
    """DR-6: After async job completes, get_job_result returns the result."""
    mcp = _MCP_SERVER

    empty_src = tmp_path / "src"
    empty_dst = tmp_path / "dst"
    empty_src.mkdir()
    empty_dst.mkdir()
    (empty_src / "libation.json").write_text("[]")
    mcp._last_downloaded_asins = []

    def _stub_config(**_kwargs):
        cfg = Config()
        cfg.source_audio_book_directory = str(empty_src)
        cfg.destination_book_directory = str(empty_dst)
        cfg.audio_file_extension = ".m4b"
        cfg.copy_instead_of_move = False
        cfg.libation_folder_cleanup = False
        cfg.download_program = "Libation"
        cfg.purchased_how_long_ago = 0
        cfg.libation_file_locations_path = ""
        cfg.enable_profanity_cleaning = False
        return cfg

    monkeypatch.setattr(mcp, "_build_config", _stub_config)
    monkeypatch.setattr(mcp, "_detect_audio_extension", lambda _p: "")

    def _quick_step_organize(cfg, **_kw):
        return {"step": "organize", "success": True, "processed_count": 0,
                "moved": [], "skipped": [], "skipped_reasons": {},
                "total_in_source": 0, "applied_asins": [],
                "destination_dir": cfg.destination_book_directory,
                "cleaning": {}, "log": "", "_book_list": []}

    def _passthrough_result(_name, _t, result, **_k):
        # _record_tool_result returns a JSON-stringified dict
        if isinstance(result, dict):
            return json.dumps(result)
        return result

    monkeypatch.setattr(mcp, "step_organize", _quick_step_organize)
    monkeypatch.setattr(mcp, "_record_tool_result", _passthrough_result)

    async def _drive():
        result = await mcp.organize_books(audio_file_extension=".m4b")
        # Wait for the background task to finish
        for _ in range(200):
            if mcp._active_job is not None and mcp._active_job["task"].done():
                break
            await asyncio.sleep(0.05)
        jr = mcp.get_job_result(result["job_id"])
        return jr

    jr = asyncio.run(_drive())
    assert jr["status"] == "completed", f"Expected completed, got {jr}"
    # Result is a JSON string from _record_tool_result
    parsed = json.loads(jr["result"])
    assert parsed.get("success") is True
    assert parsed.get("step") == "organize"


@pytest.mark.skipif(
    _MCP_SERVER is None, reason="abs-mcp/mcp_server.py could not be imported"
)
def test_get_job_result_unknown_job() -> None:
    """DR-6: get_job_result with no active job returns Unknown job error."""
    mcp = _MCP_SERVER
    # Use a job_id that doesn't match whatever is currently active
    current_id = mcp._active_job["id"] if mcp._active_job is not None else None
    bogus_id = "ZZZZZZZZZZZZ"
    assert current_id != bogus_id
    result = mcp.get_job_result(bogus_id)
    assert result == {"error": "Unknown job", "job_id": bogus_id}


@pytest.mark.skipif(
    _MCP_SERVER is None, reason="abs-mcp/mcp_server.py could not be imported"
)
def test_organize_books_rejects_concurrent_job(tmp_path, monkeypatch) -> None:
    """DR-6: While a job is running, second organize_books call returns error."""
    mcp = _MCP_SERVER

    empty_src = tmp_path / "src"
    empty_dst = tmp_path / "dst"
    empty_src.mkdir()
    empty_dst.mkdir()
    (empty_src / "libation.json").write_text("[]")
    mcp._last_downloaded_asins = []

    def _stub_config(**_kwargs):
        cfg = Config()
        cfg.source_audio_book_directory = str(empty_src)
        cfg.destination_book_directory = str(empty_dst)
        cfg.audio_file_extension = ".m4b"
        cfg.copy_instead_of_move = False
        cfg.libation_folder_cleanup = False
        cfg.download_program = "Libation"
        cfg.purchased_how_long_ago = 0
        cfg.libation_file_locations_path = ""
        cfg.enable_profanity_cleaning = False
        return cfg

    monkeypatch.setattr(mcp, "_build_config", _stub_config)
    monkeypatch.setattr(mcp, "_detect_audio_extension", lambda _p: "")

    def _slow_step_organize(cfg, **_kw):
        time.sleep(1.0)
        return {"step": "organize", "success": True, "processed_count": 0,
                "moved": [], "skipped": [], "skipped_reasons": {},
                "total_in_source": 0, "applied_asins": [],
                "destination_dir": cfg.destination_book_directory,
                "cleaning": {}, "log": "", "_book_list": []}

    monkeypatch.setattr(mcp, "step_organize", _slow_step_organize)

    async def _drive():
        first = await mcp.organize_books(audio_file_extension=".m4b")
        await asyncio.sleep(0.1)  # ensure first task is dispatched
        second = await mcp.organize_books(audio_file_extension=".m4b")
        return first, second

    first, second = asyncio.run(_drive())
    assert first["status"] == "started"
    assert "error" in second
    assert second.get("active_job_id") == first["job_id"]

    # Cleanup: wait for the background task to finish
    async def _wait():
        for _ in range(200):
            if mcp._active_job is not None and mcp._active_job["task"].done():
                return
            await asyncio.sleep(0.05)
    asyncio.run(_wait())


@pytest.mark.skipif(
    _MCP_SERVER is None, reason="abs-mcp/mcp_server.py could not be imported"
)
def test_get_cleaning_progress_callable_during_job(tmp_path, monkeypatch) -> None:
    """DR-6 primary requirement: get_cleaning_progress is callable concurrently
    while organize_books is running (SSE response stream freed).
    """
    mcp = _MCP_SERVER

    empty_src = tmp_path / "src"
    empty_dst = tmp_path / "dst"
    empty_src.mkdir()
    empty_dst.mkdir()
    (empty_src / "libation.json").write_text("[]")
    mcp._last_downloaded_asins = []

    def _stub_config(**_kwargs):
        cfg = Config()
        cfg.source_audio_book_directory = str(empty_src)
        cfg.destination_book_directory = str(empty_dst)
        cfg.audio_file_extension = ".m4b"
        cfg.copy_instead_of_move = False
        cfg.libation_folder_cleanup = False
        cfg.download_program = "Libation"
        cfg.purchased_how_long_ago = 0
        cfg.libation_file_locations_path = ""
        cfg.enable_profanity_cleaning = False
        return cfg

    monkeypatch.setattr(mcp, "_build_config", _stub_config)
    monkeypatch.setattr(mcp, "_detect_audio_extension", lambda _p: "")

    def _slow_step_organize(cfg, **_kw):
        time.sleep(1.0)
        return {"step": "organize", "success": True, "processed_count": 0,
                "moved": [], "skipped": [], "skipped_reasons": {},
                "total_in_source": 0, "applied_asins": [],
                "destination_dir": cfg.destination_book_directory,
                "cleaning": {}, "log": "", "_book_list": []}

    monkeypatch.setattr(mcp, "step_organize", _slow_step_organize)

    async def _drive():
        result = await mcp.organize_books(audio_file_extension=".m4b")
        assert result["status"] == "started"
        # Rapid-fire polls while the background task is running
        polls = []
        for _ in range(3):
            t0 = time.monotonic()
            progress = mcp.get_cleaning_progress()
            elapsed_ms = (time.monotonic() - t0) * 1000
            polls.append((elapsed_ms, progress))
            await asyncio.sleep(0.05)
        # Wait for background task
        for _ in range(200):
            if mcp._active_job is not None and mcp._active_job["task"].done():
                break
            await asyncio.sleep(0.05)
        return polls

    polls = asyncio.run(_drive())
    # All polls must return a JSON string (parses to dict) and complete quickly
    for elapsed_ms, progress in polls:
        assert isinstance(progress, str)
        json.loads(progress)  # parses without error
        assert elapsed_ms < 200, f"get_cleaning_progress took {elapsed_ms:.0f}ms — should be <200ms"