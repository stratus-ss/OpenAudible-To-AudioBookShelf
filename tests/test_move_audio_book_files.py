import importlib.util
import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openaudible_to_audiobookshelf.config import Config
from openaudible_to_audiobookshelf.pipeline import (
    make_directory_structure,
    move_audio_book_files,
    process_open_audible_book_json,
    step_organize,
)
from openaudible_to_audiobookshelf.utils import sanitize_name

_REAL_OS_PATH_EXISTS = os.path.exists


# ---------------------------------------------------------------------------
# Disk-scan helper import (lives in the MCP server, not the main package).
# ---------------------------------------------------------------------------
def _load_mcp_server_module():
    """Load abs-mcp/mcp_server.py as a module without triggering FastMCP startup."""
    repo_root = Path(__file__).resolve().parent.parent
    mcp_dir = repo_root / "abs-mcp"
    mcp_path = mcp_dir / "mcp_server.py"
    if not mcp_path.is_file():
        return None
    # The MCP module does top-level `from library_parser import ...` so its
    # directory must be on sys.path.
    mcp_dir_str = str(mcp_dir)
    added = mcp_dir_str not in sys.path
    if added:
        sys.path.insert(0, mcp_dir_str)
    try:
        spec = importlib.util.spec_from_file_location("mcp_server_under_test", mcp_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        # FastMCP or other optional deps missing in this environment.
        return None
    finally:
        if added and mcp_dir_str in sys.path:
            sys.path.remove(mcp_dir_str)
    return module


_MCP_SERVER = _load_mcp_server_module()


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
                "purchase_date": (datetime.now(timezone.utc) - timedelta(days=10))
                .date()
                .isoformat(),
            }
        ),
        (
            {
                "author": "New Author",
                "title": "New Book",
                "asin": "NEW456",
                "filename": "new_book",
                "purchase_date": (datetime.now(timezone.utc) - timedelta(days=5))
                .date()
                .isoformat(),
            }
        ),
    ],
)
def test_date_filtering(setup_test_environment, test_data):

    args = {
        "audio_file_extension": ".m4b",
        "books_json_path": os.path.join(
            setup_test_environment["tmp_path"], "books.json"
        ),
        "copy_instead_of_move": False,
        "destination_dir": setup_test_environment["dest_dir"],
        "download_program": "OpenAudible",
        "libation_folder_cleanup": False,
        "log_file": open(
            os.path.join(setup_test_environment["tmp_path"], "test.log"), "a"
        ),
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
        "books_json_path": os.path.join(
            setup_test_environment["tmp_path"], "books.json"
        ),
        "copy_instead_of_move": False,
        "destination_dir": setup_test_environment["dest_dir"],
        "download_program": "OpenAudible",
        "libation_folder_cleanup": False,
        "log_file": open(
            os.path.join(setup_test_environment["tmp_path"], "test.log"), "a"
        ),
        "purchased_how_long_ago": 7,
        "source_dir": setup_test_environment["source_dir"],
    }

    with open(args["books_json_path"], "w") as f:
        json.dump([test_data], f)

    # Create source file
    source_path = os.path.join(
        setup_test_environment["source_dir"], f"{test_data['filename']}.m4b"
    )
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
    with pytest.raises(RuntimeError):
        move_audio_book_files(
            audio_file_extension=".m4b",
            books_json_path=invalid_input[0],
            copy_instead_of_move=False,
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
    ],
)
def test_make_directory_structure(
    author: str, series: str, title: str, abs_folder: str, expected_dir: str
):
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


# ---------------------------------------------------------------------------
# Task 4a — Unit tests for move_audio_book_files ASIN filtering
# ---------------------------------------------------------------------------


def _build_books_json(tmp_path, books):
    """Helper: write a list of book dicts to books.json in tmp_path."""
    path = os.path.join(str(tmp_path), "books.json")
    with open(path, "w") as f:
        json.dump(books, f)
    return path


def _make_source_file(source_dir, book):
    """Helper: create a 0-byte audio file at the OpenAudible source path."""
    audio_path = os.path.join(source_dir, f"{book['filename']}.m4b")
    with open(audio_path, "wb") as f:
        f.write(b"")
    return audio_path


def test_asin_filter_matches_only_specified(setup_test_environment):
    """With asins=['KEEP123'], only the matching book is moved; the other
    is recorded in the _tracking['skipped'] dict with a reason."""
    env = setup_test_environment
    today = datetime.now(timezone.utc).date().isoformat()
    keep = {
        "author": "Author Keep",
        "title": "Keep Book",
        "asin": "KEEP123",
        "filename": "keep_book",
        "purchase_date": today,
    }
    skip = {
        "author": "Author Skip",
        "title": "Skip Book",
        "asin": "SKIP456",
        "filename": "skip_book",
        "purchase_date": today,
    }
    books_json = _build_books_json(env["tmp_path"], [keep, skip])
    _make_source_file(env["source_dir"], keep)
    _make_source_file(env["source_dir"], skip)

    tracking: dict = {}
    result = move_audio_book_files(
        audio_file_extension=".m4b",
        books_json_path=books_json,
        copy_instead_of_move=False,
        destination_dir=env["dest_dir"],
        download_program="OpenAudible",
        libation_folder_cleanup=False,
        log_file=open(os.path.join(env["tmp_path"], "test.log"), "a"),
        purchased_how_long_ago=0,
        source_dir=env["source_dir"],
        asins=["KEEP123"],
        _tracking=tracking,
    )

    assert len(result) == 1
    assert result[0]["asin"] == "KEEP123"
    assert "Skip Book" in tracking["skipped"]
    assert "KEEP123" not in tracking["skipped"]["Skip Book"]
    assert tracking["total"] == 2
    assert tracking["applied_asins"] == ["KEEP123"]
    # Skipped file should still be in source; matched file should be in dest.
    assert os.path.exists(os.path.join(env["source_dir"], "skip_book.m4b"))
    assert not os.path.exists(os.path.join(env["source_dir"], "keep_book.m4b"))


def test_asin_filter_none_falls_back(setup_test_environment):
    """Backward compat: no asins passed -> all date-filtered books processed."""
    env = setup_test_environment
    today = datetime.now(timezone.utc).date().isoformat()
    b1 = {
        "author": "A1",
        "title": "T1",
        "asin": "ASIN1",
        "filename": "file1",
        "purchase_date": today,
    }
    b2 = {
        "author": "A2",
        "title": "T2",
        "asin": "ASIN2",
        "filename": "file2",
        "purchase_date": today,
    }
    books_json = _build_books_json(env["tmp_path"], [b1, b2])
    _make_source_file(env["source_dir"], b1)
    _make_source_file(env["source_dir"], b2)

    tracking: dict = {}
    result = move_audio_book_files(
        audio_file_extension=".m4b",
        books_json_path=books_json,
        copy_instead_of_move=False,
        destination_dir=env["dest_dir"],
        download_program="OpenAudible",
        libation_folder_cleanup=False,
        log_file=open(os.path.join(env["tmp_path"], "test.log"), "a"),
        purchased_how_long_ago=0,
        source_dir=env["source_dir"],
        asins=None,
        _tracking=tracking,
    )

    assert len(result) == 2
    # When asins is None, no skipping happens and tracking reflects that.
    assert tracking["skipped"] == {}
    assert tracking["total"] == 2
    assert tracking["applied_asins"] is None


# ---------------------------------------------------------------------------
# Task 4b — Functional test for _extract_asins_from_dir (MCP disk-scan fallback)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _MCP_SERVER is None, reason="abs-mcp/mcp_server.py could not be imported"
)
def test_extract_asins_from_dir(tmp_path):
    """Disk scanner finds ASINs in .m4b/.mp3 filenames, ignores other files."""
    fn = _MCP_SERVER._extract_asins_from_dir

    # Populated dir
    (tmp_path / "Book A [B0AAA11111].m4b").write_bytes(b"")
    (tmp_path / "Book B [B0BBB22222].m4b").write_bytes(b"")
    (tmp_path / "NoAsinHere.m4b").write_bytes(b"")
    (tmp_path / "not-audio.txt").write_text("ignore me")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "Nested [B0CCC33333].mp3").write_bytes(b"")

    assert fn(str(tmp_path)) == ["B0AAA11111", "B0BBB22222", "B0CCC33333"]

    # Empty dir
    empty = tmp_path / "empty"
    empty.mkdir()
    assert fn(str(empty)) == []

    # Missing dir
    assert fn(str(tmp_path / "does_not_exist")) == []


# ---------------------------------------------------------------------------
# Task 4c — Integration test: step_organize with ASINs
# ---------------------------------------------------------------------------


def test_step_organize_with_asins(setup_test_environment):
    """End-to-end: step_organize with asins moves only the matching file
    and returns a populated visibility dict."""
    env = setup_test_environment
    today = datetime.now(timezone.utc).date().isoformat()
    match_book = {
        "author": "Author1",
        "title": "Match Book",
        "asin": "MATCH123",
        "filename": "Match Book [MATCH123]",
        "purchase_date": today,
    }
    skip_book = {
        "author": "Author2",
        "title": "Skip Book",
        "asin": "SKIP999",
        "filename": "Skip Book [SKIP999]",
        "purchase_date": today,
    }
    books_json = _build_books_json(env["tmp_path"], [match_book, skip_book])
    _make_source_file(env["source_dir"], match_book)
    _make_source_file(env["source_dir"], skip_book)

    cfg = Config(
        source_audio_book_directory=env["source_dir"],
        destination_book_directory=env["dest_dir"],
        books_json_path=books_json,
        audio_file_extension=".m4b",
        copy_instead_of_move=False,
        libation_folder_cleanup=False,
        download_program="OpenAudible",
        purchased_how_long_ago=0,
    )

    result = step_organize(cfg, asins=["MATCH123"])

    assert result["step"] == "organize"
    assert result["processed_count"] == 1
    assert result["applied_asins"] == ["MATCH123"]
    assert "Match Book" in result["moved"]
    assert "Skip Book" in result["skipped"]
    assert "Skip Book" in result["skipped_reasons"]
    assert result["total_in_source"] == 2
    # The matching file should be at the destination tree.
    expected = os.path.join(
        env["dest_dir"], "Author1", "Match_Book", "Match Book [MATCH123].m4b"
    )
    assert os.path.exists(expected)
    # The skipped book should NOT be in the destination tree.
    assert not os.path.exists(os.path.join(env["dest_dir"], "Author2"))
    # And the skipped source file should still be there.
    assert os.path.exists(os.path.join(env["source_dir"], "Skip Book [SKIP999].m4b"))


# ---------------------------------------------------------------------------
# Task 4d — MCP handler integration test: download → handoff → organize
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _MCP_SERVER is None, reason="abs-mcp/mcp_server.py could not be imported"
)
def test_mcp_handoff_download_to_organize(monkeypatch, tmp_path):
    """End-to-end MCP handler chain: download_books stashes ASINs in
    _last_downloaded_asins, organize_books reads it, forwards to
    step_organize, then clears it. On a second organize with no prior
    download, the disk-scan fallback kicks in.
    """
    mcp = _MCP_SERVER

    # Seed source dir with two Libation-style files (one matching, one not).
    src = tmp_path / "Libation" / "Books"
    src.mkdir(parents=True)
    (src / "Match Book [B0MATCH123].m4b").write_bytes(b"")
    (src / "Skip Book [B0SKIP9999].m4b").write_bytes(b"")
    dst = tmp_path / "abs_audiobooks"
    dst.mkdir()

    # Reset handoff state so prior tests/sessions can't leak in.
    mcp._last_downloaded_asins = []

    # Stub _build_config so the handlers don't touch env / libraries.yaml.
    def _stub_config(**_kwargs):
        cfg = Config()
        cfg.source_audio_book_directory = str(src)
        cfg.destination_book_directory = str(dst)
        cfg.audio_file_extension = ".m4b"
        cfg.copy_instead_of_move = False
        cfg.libation_folder_cleanup = False
        cfg.download_program = "OpenAudible"
        cfg.purchased_how_long_ago = 0
        cfg.libation_file_locations_path = ""
        cfg.enable_profanity_cleaning = False
        return cfg

    monkeypatch.setattr(mcp, "_build_config", _stub_config)

    # Skip the audio-extension auto-detect walk to keep the test fast.
    monkeypatch.setattr(mcp, "_detect_audio_extension", lambda _p: "")

    # Stub _record_tool_result so we don't append to abs-mcp/data/tool-metrics.jsonl
    # and so the handler's return value is the bare result dict for inspection.
    # The real handler passes a json.dumps(...) string into _record_tool_result,
    # so decode it back to a dict for assertion convenience.
    def _passthrough_result(_name, _t, result, **_k):
        return json.loads(result) if isinstance(result, str) else result

    monkeypatch.setattr(mcp, "_record_tool_result", _passthrough_result)

    # Stub step_download to return canned output (no real libationcli).
    monkeypatch.setattr(
        mcp,
        "step_download",
        lambda cfg: {
            "step": "download",
            "success": True,
            "asins": ["B0MATCH123"],
            "source_dir": cfg.source_audio_book_directory,
            "detail": {},
        },
    )

    # Capture what step_organize is called with.
    captured: dict = {}

    def _fake_step_organize(cfg, **kwargs):
        captured["asins"] = kwargs.get("asins")
        captured["called"] = True
        return {
            "step": "organize",
            "success": True,
            "processed_count": 1,
            "moved": ["Match Book"],
            "skipped": ["Skip Book"],
            "skipped_reasons": {"Skip Book": "ASIN B0SKIP9999 not in requested set"},
            "total_in_source": 2,
            "applied_asins": kwargs.get("asins") or [],
            "destination_dir": cfg.destination_book_directory,
            "log": "",
            "_book_list": [],
        }

    monkeypatch.setattr(mcp, "step_organize", _fake_step_organize)

    # --- Phase 1: download_books stashes ASINs ---
    mcp.download_books(asins=["B0MATCH123"])
    assert mcp._last_downloaded_asins == ["B0MATCH123"], (
        "download_books should stash requested ASINs in module global; "
        f"got {mcp._last_downloaded_asins!r}"
    )

    # --- Phase 2: organize_books reads handoff, forwards, then clears ---
    result = mcp.organize_books(audio_file_extension=".m4b")
    assert captured.get("called") is True, (
        "organize_books should have called step_organize"
    )
    assert captured.get("asins") == ["B0MATCH123"], (
        "organize_books should forward stashed ASINs to step_organize; "
        f"got {captured.get('asins')!r}"
    )
    assert mcp._last_downloaded_asins == [], (
        "handoff is one-shot; global should be cleared after organize_books; "
        f"got {mcp._last_downloaded_asins!r}"
    )
    assert result["step"] == "organize"
    assert result["processed_count"] == 1

    # --- Phase 3: no prior download → disk-scan fallback kicks in ---
    captured.clear()
    mcp._last_downloaded_asins = []  # simulate process restart / no prior download
    result2 = mcp.organize_books(audio_file_extension=".m4b")
    assert captured.get("called") is True
    # The disk-scan returns every [B0…] ASIN it finds in the source dir;
    # downstream step_organize does the include/exclude decision. So we
    # assert the fallback path fired (non-empty list, contains our ASIN)
    # rather than asserting an exact list.
    fallback_asins = captured.get("asins")
    assert isinstance(fallback_asins, list) and "B0MATCH123" in fallback_asins, (
        "disk-scan fallback should derive ASINs from source-dir filenames "
        f"and include B0MATCH123; got {fallback_asins!r}"
    )
    # Global should still be cleared after fallback path.
    assert mcp._last_downloaded_asins == []
    assert result2["step"] == "organize"


# ---------------------------------------------------------------------------
# Profanity-cleaning validation (gating `_build_config` against
# `REMOTE_WHISPER_URL` correctness when `enable_profanity_cleaning=True`).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _MCP_SERVER is None, reason="abs-mcp/mcp_server.py could not be imported"
)
class TestProfanityCleaningValidation:
    """Pre-flight validation that rejects cleaning=true + REMOTE_WHISPER_URL="",
    so AudioCleaner never silently falls back to the un-cleaned audio file.
    """

    def _stub_config(self, *, enable_profanity_cleaning, remote_whisper_url):
        """Mimic the real `_build_config` shape minus env touches."""

        def _factory(**_kwargs):
            cfg = Config()
            cfg.source_audio_book_directory = "/tmp/fake/source"
            cfg.destination_book_directory = "/tmp/fake/dest"
            cfg.audio_file_extension = ".m4b"
            cfg.copy_instead_of_move = False
            cfg.libation_folder_cleanup = False
            cfg.download_program = "Libation"
            cfg.purchased_how_long_ago = 0
            cfg.libation_file_locations_path = ""
            cfg.enable_profanity_cleaning = enable_profanity_cleaning
            cfg.remote_whisper_url = remote_whisper_url
            return cfg

        return _factory

    def test_validate_profanity_config_raises_without_whisper_url(self, monkeypatch):
        """cleaning=True, URL="" → ValueError mentioning REMOTE_WHISPER_URL."""
        mcp = _MCP_SERVER
        monkeypatch.delenv("REMOTE_WHISPER_URL", raising=False)
        cfg = Config()
        cfg.enable_profanity_cleaning = True
        cfg.remote_whisper_url = ""
        with pytest.raises(ValueError, match="REMOTE_WHISPER_URL"):
            mcp._validate_profanity_config(cfg)

    def test_validate_profanity_config_passes_when_whisper_url_set(self, monkeypatch):
        """cleaning=True, URL+s wears_file set → no exception."""
        mcp = _MCP_SERVER
        monkeypatch.delenv("REMOTE_WHISPER_URL", raising=False)
        cfg = Config()
        cfg.enable_profanity_cleaning = True
        cfg.remote_whisper_url = "http://whisper.example:8000"
        cfg.swears_file = "/fake/path/swears.txt"
        mcp._validate_profanity_config(cfg)

    def test_validate_profanity_config_raises_without_swears_file(self, monkeypatch):
        """cleaning=True, URL set, swears_file="", monkeyplug exists but swears.txt missing → ValueError."""
        mcp = _MCP_SERVER
        monkeypatch.delenv("REMOTE_WHISPER_URL", raising=False)
        monkeypatch.setattr("os.path.exists", lambda p: False if str(p).endswith("swears.txt") else _REAL_OS_PATH_EXISTS(str(p)))
        cfg = Config()
        cfg.enable_profanity_cleaning = True
        cfg.remote_whisper_url = "http://whisper.example:8000"
        cfg.swears_file = ""
        with pytest.raises(ValueError, match="SWEARS_FILE"):
            mcp._validate_profanity_config(cfg)

    def test_validate_profanity_config_passes_when_cleaning_disabled(self, monkeypatch):
        """cleaning=False regardless of URL → validation short-circuits, no exception."""
        mcp = _MCP_SERVER
        monkeypatch.delenv("REMOTE_WHISPER_URL", raising=False)
        cfg = Config()
        cfg.enable_profanity_cleaning = False
        cfg.remote_whisper_url = ""
        mcp._validate_profanity_config(cfg)

    def test_build_config_raises_when_cleaning_enabled_without_url(self, monkeypatch, tmp_path):
        """End-to-end: `_build_config` itself raises ValueError before returning.

        This proves the in-line call (`_validate_profanity_config(cfg)`) is reached,
        not just the helper when invoked directly.
        """
        mcp = _MCP_SERVER
        monkeypatch.delenv("REMOTE_WHISPER_URL", raising=False)

        def _stub_resolve_library(_library):
            return {
                "destination_dir": str(tmp_path),
                "abs_server_url": "http://abs.example",
                "library_id": "fake-lib-id",
                "abs_api_token": "fake-token",
            }

        monkeypatch.setattr(mcp, "_resolve_library", _stub_resolve_library)

        with pytest.raises(ValueError, match="REMOTE_WHISPER_URL"):
            mcp._build_config(
                library="kids",
                enable_profanity_cleaning=True,
            )

    def test_organize_books_returns_error_when_misconfigured(self, monkeypatch, tmp_path):
        """`organize_books` returns a clean error result; `step_organize` not called.

        The `_build_config` stub mirrors real production behavior: it builds the
        Config AND runs `_validate_profanity_config` so the handler's wrap
        exercises the same code path the live server does on misconfiguration.
        """
        mcp = _MCP_SERVER
        monkeypatch.delenv("REMOTE_WHISPER_URL", raising=False)

        mcp._last_downloaded_asins = []

        def _stub_config(**_kwargs):
            cfg = Config()
            cfg.source_audio_book_directory = str(tmp_path)
            cfg.destination_book_directory = str(tmp_path)
            cfg.audio_file_extension = ".m4b"
            cfg.copy_instead_of_move = False
            cfg.libation_folder_cleanup = False
            cfg.download_program = "Libation"
            cfg.purchased_how_long_ago = 0
            cfg.libation_file_locations_path = ""
            cfg.enable_profanity_cleaning = True
            cfg.remote_whisper_url = ""
            # Replicate production semantics: validate before returning.
            mcp._validate_profanity_config(cfg)
            return cfg

        monkeypatch.setattr(mcp, "_build_config", _stub_config)

        # Decode the JSON-stringified result so the assertions read naturally.
        def _passthrough_result(_name, _t, result, **_k):
            return json.loads(result) if isinstance(result, str) else result

        monkeypatch.setattr(mcp, "_record_tool_result", _passthrough_result)

        # Spy: if validation+wrap works, this will never run. If it does run,
        # the spy captures that fact for the assertion below.
        def _spy_step_organize(*_args, **_kwargs):
            raise AssertionError(
                "step_organize must not be called when validation fails"
            )

        monkeypatch.setattr(mcp, "step_organize", _spy_step_organize)
        monkeypatch.setattr(mcp, "_detect_audio_extension", lambda _p: "")

        result = mcp.organize_books(audio_file_extension=".m4b", enable_profanity_cleaning=True)

        assert isinstance(result, dict)
        assert result.get("success") is False, (
            f"organize_books should report failure when misconfigured; got {result!r}"
        )
        assert result.get("step") == "organize"
        assert "REMOTE_WHISPER_URL" in result.get("error", ""), (
            f"error message should name REMOTE_WHISPER_URL; got {result.get('error')!r}"
        )


class TestProfanityCleaningUx:
    """Tests for the 5 new behaviors added by the profanity_cleaning_ux plan:

    - fail-fast backend check (audio_cleaner.py 1c)
    - book-level resume state (audio_cleaner.py 1b)
    - progress callback plumbing (audio_cleaner.py + mcp_server.py 2b)
    - cleaning stats in tool response (pipeline.py 2c)
    - get_cleaning_progress MCP tool (mcp_server.py 2d)
    - duration hint in tool docstring (mcp_server.py 2a)
    """

    def setup_method(self):
        # Defensive cleanup of the shared /tmp resume file (some test classes
        # use /tmp/<random> working dirs which all share one resume path).
        shared_resume = "/tmp/profanity_cleaning_resume.json"
        if os.path.exists(shared_resume):
            os.remove(shared_resume)

    def _make_config(self, tmp_path, *, remote_whisper_url, swears_file):
        """Build a Config suitable for instantiating AudioCleaner in tests."""
        cfg = Config()
        cfg.working_directory = str(tmp_path / "working")
        cfg.save_transcripts = False
        cfg.enable_profanity_cleaning = True
        cfg.remote_whisper_url = remote_whisper_url
        cfg.swears_file = swears_file
        cfg.timeout = 60
        cfg.beep_mode = False
        cfg.confidence_threshold = 0.7
        cfg.copy_instead_of_move = False
        return cfg

    def _write_swears(self):
        path = "/tmp/test_swears_ux.txt"
        Path(path).write_text("anal\n")
        return path

    def test_fail_fast_backend_unreachable(self, tmp_path):
        """AudioCleaner with unreachable Whisper URL raises AudioCleaningError in <10s.

        Verifies Task 1c: fail-fast backend check raises AudioCleaningError,
        the existing except handler returns the source file, total_failed is incremented.
        """
        from openaudible_to_audiobookshelf.audio_cleaner import AudioCleaner

        swears = self._write_swears()
        try:
            cfg = self._make_config(
                tmp_path, remote_whisper_url="http://localhost:1", swears_file=swears
            )
            cleaner = AudioCleaner(cfg, io.StringIO())
            with tempfile.NamedTemporaryFile(suffix=".m4b", delete=False) as f:
                f.write(b"fakedata")
                src = f.name
            try:
                start = time.monotonic()
                result = cleaner.process_audio_file(
                    src, {"title": "Fail Book", "asin": "B0FAIL"}
                )
                elapsed = time.monotonic() - start
            finally:
                os.remove(src)

            assert result == src, "should fall back to source file on backend down"
            assert elapsed < 10, f"should fail fast (<10s); took {elapsed:.2f}s"
            assert cleaner.total_failed == 1
            assert cleaner.total_processed == 0
        finally:
            if os.path.exists(swears):
                os.remove(swears)

    def test_resume_state_written_on_success(self, tmp_path):
        """After successful process_audio_file, resume JSON marks ASIN 'done'.

        Verifies Task 1b: _mark_resume_status writes 'done' on success.
        """
        from openaudible_to_audiobookshelf.audio_cleaner import AudioCleaner

        swears = self._write_swears()
        try:
            cfg = self._make_config(
                tmp_path, remote_whisper_url="http://whisper:8000", swears_file=swears
            )
            cleaner = AudioCleaner(cfg, io.StringIO())

            src = str(tmp_path / "src.m4b")
            Path(src).write_bytes(b"fakedata")
            # Pre-create the output file that the mocked WhisperPlugger will return.
            out_dir = tmp_path / "working" / "B0DONE"
            out_dir.mkdir(parents=True)
            out = out_dir / "src.m4b"
            out.write_bytes(b"cleaned output")

            with patch(
                "openaudible_to_audiobookshelf.audio_cleaner.requests.head"
            ) as rh:
                rh.return_value.raise_for_status.return_value = None
                with patch(
                    "openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger"
                ) as mpc:
                    mock_plugger = MagicMock()
                    mock_plugger.EncodeCleanAudio.return_value = str(out)
                    mock_plugger.naughtyWordList = []
                    mpc.return_value = mock_plugger

                    result = cleaner.process_audio_file(
                        src, {"title": "Done Book", "asin": "B0DONE"}
                    )

            assert result == str(out)
            assert cleaner.total_processed == 1

            resume_path = tmp_path / "profanity_cleaning_resume.json"
            assert resume_path.exists(), (
                f"resume JSON not written; expected at {resume_path}"
            )
            state = json.loads(resume_path.read_text())
            assert state.get("B0DONE") == "done", (
                f"expected B0DONE='done' in resume state; got {state}"
            )
        finally:
            if os.path.exists(swears):
                os.remove(swears)

    def test_resume_state_written_on_failure(self, tmp_path):
        """After failed process_audio_file, resume JSON marks ASIN 'failed'.

        Verifies Task 1b: _mark_resume_status writes 'failed' in the except handler.
        """
        from openaudible_to_audiobookshelf.audio_cleaner import AudioCleaner

        swears = self._write_swears()
        try:
            cfg = self._make_config(
                tmp_path, remote_whisper_url="http://localhost:1", swears_file=swears
            )
            cleaner = AudioCleaner(cfg, io.StringIO())
            with tempfile.NamedTemporaryFile(suffix=".m4b", delete=False) as f:
                f.write(b"fakedata")
                src = f.name
            try:
                result = cleaner.process_audio_file(
                    src, {"title": "Failing Book", "asin": "B0FAIL2"}
                )
            finally:
                os.remove(src)

            assert result == src
            assert cleaner.total_failed == 1

            resume_path = tmp_path / "profanity_cleaning_resume.json"
            assert resume_path.exists(), (
                f"resume JSON not written; expected at {resume_path}"
            )
            state = json.loads(resume_path.read_text())
            assert state.get("B0FAIL2") == "failed", (
                f"expected B0FAIL2='failed' in resume state; got {state}"
            )
        finally:
            if os.path.exists(swears):
                os.remove(swears)

    def test_resume_state_skips_processed(self, tmp_path):
        """Pre-populated 'done' ASIN short-circuits process_audio_file.

        Verifies Task 1b: _is_already_processed causes an immediate return
        before any HTTP / plugger call is made.
        """
        from openaudible_to_audiobookshelf.audio_cleaner import AudioCleaner

        swears = self._write_swears()
        try:
            # Pre-populate the resume JSON that lives at <working_dir>.parent/_RESUME_FILE.
            resume_path = tmp_path / "profanity_cleaning_resume.json"
            resume_path.write_text(json.dumps({"B0SKIP": "done"}))

            cfg = self._make_config(
                tmp_path, remote_whisper_url="http://localhost:1", swears_file=swears
            )
            cleaner = AudioCleaner(cfg, io.StringIO())
            assert cleaner._is_already_processed("B0SKIP") is True

            with tempfile.NamedTemporaryFile(suffix=".m4b", delete=False) as f:
                f.write(b"fakedata")
                src = f.name
            try:
                with patch(
                    "openaudible_to_audiobookshelf.audio_cleaner.requests.head"
                ) as rh:
                    result = cleaner.process_audio_file(
                        src, {"title": "Skip Book", "asin": "B0SKIP"}
                    )
                    rh.assert_not_called()
            finally:
                os.remove(src)

            assert result == src, "should return source immediately on resume skip"
            assert cleaner.total_processed == 0
            assert cleaner.total_failed == 0
            # State should still be 'done' (not overwritten)
            state = json.loads(resume_path.read_text())
            assert state.get("B0SKIP") == "done"
        finally:
            if os.path.exists(swears):
                os.remove(swears)

    def test_cleaning_stats_in_response(self, tmp_path):
        """step_organize returns a 'cleaning' key with the expected fields.

        Verifies Task 2c: pipeline.py adds the cleaning stats key to the return dict.
        """
        cfg = self._make_config(
            tmp_path, remote_whisper_url="http://whisper:8000", swears_file=self._write_swears()
        )
        cfg.books_json_path = str(tmp_path / "libation.json")
        cfg.source_audio_book_directory = str(tmp_path / "src")
        cfg.destination_book_directory = str(tmp_path / "dest")
        cfg.audio_file_extension = ".m4b"
        cfg.libation_file_locations_path = ""
        cfg.purchased_how_long_ago = 0
        cfg.download_program = "Libation"
        Path(cfg.books_json_path).write_text("[]")
        Path(cfg.source_audio_book_directory).mkdir()
        Path(cfg.destination_book_directory).mkdir()

        try:
            with patch(
                "openaudible_to_audiobookshelf.audio_cleaner.requests.head"
            ) as rh:
                rh.return_value.raise_for_status.return_value = None
                result = step_organize(cfg)
        finally:
            if os.path.exists(cfg.swears_file):
                os.remove(cfg.swears_file)

        assert "cleaning" in result, f"Expected 'cleaning' key in result: {result}"
        cleaning = result["cleaning"]
        assert "total_cleaned" in cleaning, f"missing total_cleaned: {cleaning}"
        assert "total_failed" in cleaning, f"missing total_failed: {cleaning}"
        assert "total_profanities" in cleaning, f"missing total_profanities: {cleaning}"

    def test_get_cleaning_progress_tool_returns_json(self):
        """get_cleaning_progress returns valid JSON dict.

        Verifies Task 2d: the new MCP tool is registered and returns JSON.
        """
        if _MCP_SERVER is None:
            pytest.skip("mcp_server module not loadable in this environment")
        result_json = _MCP_SERVER.get_cleaning_progress()
        result = json.loads(result_json)
        assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"

    def test_duration_hint_in_docstring(self):
        """organize_books.__doc__ contains the expected duration hint.

        Verifies Task 2a: duration hint added to tool docstring.
        """
        if _MCP_SERVER is None:
            pytest.skip("mcp_server module not loadable in this environment")
        doc = _MCP_SERVER.organize_books.__doc__ or ""
        assert "~2-5 min per hour" in doc, (
            f"organize_books docstring missing duration hint:\n{doc}"
        )
        assert "get_cleaning_progress()" in doc, (
            "organize_books docstring missing get_cleaning_progress reference"
        )
        assert "150MB" in doc, (
            "organize_books docstring missing chunking size reference"
        )

