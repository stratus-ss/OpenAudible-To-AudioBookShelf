"""Configuration, library registry, auth, and metrics wiring for the MCP server.

Extracted from mcp_server.py as part of Plan 3 (DR-1 module decomposition).
Unlike mcp_server.py (which is always invoked as a script), this module is
only ever imported as part of the `abs_mcp` package, so it uses proper
package-relative imports for its sibling modules.
"""

import io
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from .library_parser import LibraryDataParser
from .tool_metrics import ToolMetricsRecorder

from openaudible_to_audiobookshelf.config import Config
from openaudible_to_audiobookshelf.utils import resolve_books_json_path


ABS_MCP_DIR = Path(__file__).resolve().parent


_cleaning_progress: dict = {}
_CLEANING_PROGRESS_LOCK = threading.Lock()


def _update_cleaning_progress(**kwargs) -> None:
    """Module-level progress callback used by AudioCleaner via cfg._progress_callback."""
    with _CLEANING_PROGRESS_LOCK:
        _cleaning_progress.update(kwargs)
        _cleaning_progress["_updated_at"] = time.time()


def _reset_cleaning_progress() -> None:
    """Clear any prior run's progress before starting a new organize/ingest operation."""
    with _CLEANING_PROGRESS_LOCK:
        _cleaning_progress.clear()


def _load_dotenv(env_file: Path) -> None:
    """Load a .env file into os.environ (does not override existing vars)."""
    if not env_file.is_file():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


_env_path = os.environ.get("MCP_ENV_FILE", "")
if _env_path:
    _load_dotenv(Path(_env_path))
else:
    _load_dotenv(ABS_MCP_DIR / ".env")


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
LOGGER = logging.getLogger("audiobook-ingestion-mcp")
PARSER = LibraryDataParser()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


METRICS = ToolMetricsRecorder(
    history_path=_env(
        "TOOL_METRICS_PATH", str(ABS_MCP_DIR / "data" / "tool-metrics.jsonl")
    ),
)


def _r(override: str | None, env_key: str, default: str = "") -> str:
    """Resolve a value: per-call override > env var > default."""
    if override is not None:
        return override
    return os.environ.get(env_key, default)


def _log_buffer() -> io.StringIO:
    return io.StringIO()


def _elapsed_ms(start_time: float) -> int:
    """Milliseconds elapsed since start time."""
    return int((time.monotonic() - start_time) * 1000)


def _record_tool_result(
    tool_name: str, start_time: float, result: dict, success: bool = True
) -> dict:
    """Record response efficiency metrics and return the result as a dict.

    Returns dict — FastMCP auto-serializes to structuredContent.
    """
    # Record metrics with the JSON-serialized size of the result
    result_str = json.dumps(result)
    METRICS.record(
        tool_name=tool_name,
        result=result_str,
        duration_ms=_elapsed_ms(start_time),
        success=success,
    )
    return result


def _summarize_metric_records(records: list[dict]) -> dict:
    """Build per-tool aggregate summary for a metrics record list."""
    return ToolMetricsRecorder._aggregate(records)


# ---------------------------------------------------------------------------
# Library registry
# ---------------------------------------------------------------------------

_LIBRARIES_CACHE: dict | None = None


def _load_libraries() -> dict:
    """Load the YAML library registry, caching after first read."""
    global _LIBRARIES_CACHE
    if _LIBRARIES_CACHE is not None:
        return _LIBRARIES_CACHE

    config_path = _env("LIBRARIES_CONFIG", "")
    if not config_path:
        config_path = str(ABS_MCP_DIR / "libraries.yaml")

    path = Path(config_path)
    if not path.is_file():
        LOGGER.warning("Libraries config not found at %s", config_path)
        _LIBRARIES_CACHE = {"default_library": "", "libraries": {}}
        return _LIBRARIES_CACHE

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    _LIBRARIES_CACHE = {
        "default_library": data.get("default_library", ""),
        "libraries": data.get("libraries", {}),
    }
    LOGGER.info(
        "Loaded %d libraries from %s (default: %s)",
        len(_LIBRARIES_CACHE["libraries"]),
        config_path,
        _LIBRARIES_CACHE["default_library"],
    )
    return _LIBRARIES_CACHE


def _resolve_library(library: str | None) -> dict:
    """Resolve a friendly library name to its full config dict.

    Returns a dict with keys: library_id, destination_dir, media_type,
    folder_id, abs_server_url, abs_api_token.  Missing per-library
    values fall back to env-var defaults.
    """
    cfg = _load_libraries()
    name = library or cfg.get("default_library", "")
    lib_entry = cfg["libraries"].get(name, {}) if name else {}

    return {
        "name": name,
        "library_id": lib_entry.get("library_id", _env("ABS_LIBRARY_ID")),
        "destination_dir": lib_entry.get(
            "destination_dir", _env("DESTINATION_BOOK_DIRECTORY")
        ),
        "media_type": lib_entry.get("media_type", "book"),
        "folder_id": lib_entry.get("folder_id", ""),
        "abs_server_url": lib_entry.get("abs_server_url", _env("ABS_SERVER_URL")),
        "abs_api_token": lib_entry.get("abs_api_token", _env("ABS_API_TOKEN")),
    }


def _abs_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _validate_profanity_config(cfg: Config) -> None:
    """Validate profanity cleaning config and auto-discover swears file.

    The MCP path constructs Config directly via _build_config and never reaches
    Config._validate(), so the MonkeyPlug checks that live there are bypassed.
    Without this guard, AudioCleaner proceeds with empty remoteUrl or empty
    swears_file and WhisperPlugger fails at init, after which process_audio_file
    silently falls back to the original file and the move step still runs.
    Fail loud, fail early.
    """
    if not getattr(cfg, "enable_profanity_cleaning", False):
        return
    url = (getattr(cfg, "remote_whisper_url", "") or "").strip()
    if not url:
        raise ValueError(
            "Profanity cleaning is enabled but REMOTE_WHISPER_URL is not set. "
            "Configure a Whisper-WebUI endpoint in .env "
            "(e.g. REMOTE_WHISPER_URL=http://whisper-host:8000) or disable "
            "profanity cleaning via ENABLE_PROFANITY_CLEANING=false."
        )
    swears = (getattr(cfg, "swears_file", "") or "").strip()
    if swears:
        return
    try:
        import monkeyplug
        monkeyplug_dir = os.path.dirname(monkeyplug.__file__)
        default_swears = os.path.join(monkeyplug_dir, "swears.txt")
        if os.path.exists(default_swears):
            cfg.swears_file = default_swears
            return
    except ImportError:
        pass
    raise ValueError(
        "Profanity cleaning is enabled but SWEARS_FILE is not set and "
        "MonkeyPlug's bundled swears.txt was not found. Install the "
        "monkeyplug package or set SWEARS_FILE in .env."
    )


def _build_config(
    library: str | None = None,
    source_dir: str | None = None,
    destination_dir: str | None = None,
    audio_file_extension: str | None = None,
    copy_instead_of_move: bool | None = None,
    libation_folder_cleanup: bool | None = None,
    libation_file_locations_path: str | None = None,
    enable_profanity_cleaning: bool | None = None,
    purchased_how_long_ago: int = 0,
    abs_server_url: str | None = None,
    abs_library_id: str | None = None,
    abs_api_token: str | None = None,
    libation_cli: str | None = None,
    asins: list[str] | None = None,
) -> Config:
    """Build a Config object from MCP tool parameters + library registry + env."""
    lib = _resolve_library(library)

    data: dict[str, Any] = {
        "source_audio_book_directory": _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY"),
        "destination_book_directory": destination_dir or lib["destination_dir"],
        "audio_file_extension": _r(audio_file_extension, "AUDIO_FILE_EXTENSION", ".m4b"),
        "copy_instead_of_move": (
            copy_instead_of_move
            if copy_instead_of_move is not None
            else _env("COPY_INSTEAD_OF_MOVE", "false").lower() == "true"
        ),
        "libation_folder_cleanup": (
            libation_folder_cleanup
            if libation_folder_cleanup is not None
            else _env("LIBATION_FOLDER_CLEANUP", "false").lower() == "true"
        ),
        "libation_file_locations_path": _r(
            libation_file_locations_path, "LIBATION_FILE_LOCATIONS_PATH"
        ),
        "enable_profanity_cleaning": (
            enable_profanity_cleaning
            if enable_profanity_cleaning is not None
            else _env("ENABLE_PROFANITY_CLEANING", "false").lower() == "true"
        ),
        "purchased_how_long_ago": purchased_how_long_ago,
        "server_url": abs_server_url or lib["abs_server_url"],
        "library_id": abs_library_id or lib["library_id"],
        "abs_api_token": abs_api_token or lib["abs_api_token"],
        "libation_cli": libation_cli or _env("LIBATION_CLI", "libationcli"),
        "download_program": "Libation",
        "books_json_path": resolve_books_json_path(
            _r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY"),
            str(Path(_r(source_dir, "SOURCE_AUDIO_BOOK_DIRECTORY")) / "libation.json"),
        ),
        "asins": asins or [],
        "working_directory": _env(
            "WORKING_DIRECTORY", str(Path.home() / ".cache" / "monkeyplug-cleaning")
        ),
        "save_transcripts": _env("SAVE_TRANSCRIPTS", "true").lower() == "true",
        "swears_file": _env("SWEARS_FILE", ""),
        "remote_whisper_url": _env("REMOTE_WHISPER_URL", ""),
        "whisper_model": _env("WHISPER_MODEL", "small"),
        "timeout": int(_env("TIMEOUT", "600")),
        "confidence_threshold": float(_env("CONFIDENCE_THRESHOLD", "0.70")),
        "beep_mode": _env("BEEP_MODE", "false").lower() == "true",
        "abs_http_timeout": int(_env("ABS_HTTP_TIMEOUT", "30")),
        "abs_http_max_retries": int(_env("ABS_HTTP_MAX_RETRIES", "3")),
        "abs_http_backoff_factor": float(_env("ABS_HTTP_BACKOFF_FACTOR", "1.0")),
    }

    cfg = Config(**data)
    cfg._progress_callback = _update_cleaning_progress
    _validate_profanity_config(cfg)
    return cfg
