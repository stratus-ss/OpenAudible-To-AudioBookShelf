"""
Audio profanity cleaning integration with MonkeyPlug.

This module provides the AudioCleaner class which integrates MonkeyPlug
profanity cleaning into the OpenAudible-To-AudioBookShelf pipeline.
"""

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from monkeyplug.monkeyplug import WhisperPlugger
from openaudible_to_audiobookshelf.utils import sanitize_name, log_message

_LOG_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class AudioCleaningError(Exception):
    """Base exception for audio cleaning errors."""
    pass


class AudioCleaner:
    """Manages profanity cleaning of audio files using MonkeyPlug."""
    
    CHUNKING_THRESHOLD_MB = 150  # File size threshold for chunked processing
    _RESUME_FILE = "profanity_cleaning_resume.json"

    def __init__(self, config, log_file):
        """
        Initialize the AudioCleaner.

        Args:
            config: Config object with profanity cleaning settings
            log_file: Log file handle for writing progress
        """
        self.config = config
        self.log_file = log_file
        self.working_dir = Path(config.working_directory)
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self.copy_mode = getattr(config, "copy_instead_of_move", False)

        self._logger = logging.getLogger(__name__)

        self._progress_callback = getattr(self.config, "_progress_callback", None)

        self.resume_path = Path(self.working_dir.parent) / self._RESUME_FILE
        self._resume_state: dict = {}
        if self.resume_path.exists():
            try:
                self._resume_state = json.loads(self.resume_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                self._log(f"Could not load resume state from {self.resume_path}: {e}", "WARNING")
                self._resume_state = {}

        # Track statistics
        self.total_processed = 0
        self.total_failed = 0
        self.total_profanities = 0
        self._last_profanity_count = 0
        self._book_count = 0

    # ============================================================================
    # UTILITY HELPERS
    # ============================================================================

    def _get_resume_entry(self, asin: str):
        """Return the raw resume entry for ASIN (string, dict, or None)."""
        if not asin:
            return None
        return self._resume_state.get(asin)

    def _is_already_processed(self, asin: str) -> bool:
        """Return True if ASIN is in the resume state and marked as done.

        Accepts both the legacy string format ("done") and the new object format
        ({"status": "done"}).
        """
        entry = self._get_resume_entry(asin)
        if entry is None:
            return False
        if isinstance(entry, str):
            return entry == "done"
        if isinstance(entry, dict):
            return entry.get("status") == "done"
        return False

    def _is_resumable(self, asin: str) -> tuple:
        """Return (True, working_dir) if ASIN has an intact in-progress state.

        Validates that:
        1. The entry exists and is a dict with status="in_progress" and a working_dir.
        2. The working_dir directory exists on disk.
        3. The directory contents pass _validate_working_dir() (chunks non-empty,
           transcripts parse, ffprobe succeeds on first chunk).

        If validation fails (e.g. directory missing or corrupt), the working dir
        is deleted, the resume entry is reset to "failed", and (False, "") is
        returned — caller should start fresh.

        Returns (False, "") if the ASIN is absent, has a legacy string entry,
        has any other status, or validation fails.
        """
        entry = self._get_resume_entry(asin)
        if not isinstance(entry, dict):
            return (False, "")
        if entry.get("status") != "in_progress":
            return (False, "")
        working_dir = entry.get("working_dir", "")
        if not working_dir:
            return (False, "")
        wd_path = Path(working_dir)
        if not wd_path.exists():
            self._log(
                f"Stale in_progress state for ASIN {asin}: working_dir missing, resetting", "WARNING"
            )
            self._mark_resume_status(asin, "failed")
            return (False, "")
        if not self._validate_working_dir(asin):
            self._log(
                f"Stale in_progress state for ASIN {asin}: working_dir validation failed, resetting", "WARNING"
            )
            if wd_path.exists():
                try:
                    shutil.rmtree(str(wd_path))
                except OSError as e:
                    self._log(f"Failed to remove corrupt working dir for {asin}: {e}", "WARNING")
            self._mark_resume_status(asin, "failed")
            return (False, "")
        return (True, working_dir)

    def _mark_resume_status(self, asin: str, status: str, working_dir: str = None) -> None:
        """Persist ASIN -> status into the resume JSON file.

        For "done"/"failed" statuses without a working_dir, writes the legacy
        string format for backward compatibility with existing consumers.

        For "in_progress" status, writes an object {"status", "working_dir"}.
        Raises ValueError if status="in_progress" is passed without a working_dir.
        """
        if not asin:
            return
        if status == "in_progress" and not working_dir:
            raise ValueError("working_dir is required when status='in_progress'")
        if working_dir:
            self._resume_state[asin] = {"status": status, "working_dir": working_dir}
        else:
            self._resume_state[asin] = status
        try:
            self.resume_path.parent.mkdir(parents=True, exist_ok=True)
            self.resume_path.write_text(json.dumps(self._resume_state, indent=2))
        except OSError as e:
            self._log(f"Failed to write resume state to {self.resume_path}: {e}", "WARNING")

    def _cleanup_book_working_dir(self, asin: str) -> None:
        """Remove the working directory for a single book (after successful cleaning).

        The cleaned audio file has already been moved to the destination by the caller,
        so this only removes the per-book subdirectory containing chunks and transcripts.
        No-op if the directory does not exist (already cleaned up, or never created).
        """
        if not asin:
            return
        book_dir = self.working_dir / asin
        if book_dir.exists():
            try:
                shutil.rmtree(str(book_dir))
                self._log(f"Cleaned up working directory for ASIN {asin}")
            except OSError as e:
                self._log(f"Failed to clean up working directory for {asin}: {e}", "WARNING")

    def _validate_working_dir(self, asin: str) -> bool:
        """Validate that an existing book's working directory is intact for resume.

        Checks:
        1. The book's subdirectory exists.
        2. All chunk audio files (.m4b/.mp3) are non-empty.
        3. All paired transcript JSONs are valid JSON with non-empty word lists.
        4. At least one chunk audio file is parseable by ffprobe (catches truncated writes).

        Returns True only if every check passes. Used by _is_resumable to decide
        whether to trust existing artifacts or discard them.
        """
        if not asin:
            return False
        book_dir = self.working_dir / asin
        if not book_dir.exists():
            return False

        chunk_files = [
            p for p in book_dir.rglob("*_chunk_*.*")
            if p.is_file()
            and p.suffix.lower() not in (".json",)
            and not p.name.endswith("_transcript.json")
        ]
        if not chunk_files:
            return False

        for chunk in chunk_files:
            if chunk.stat().st_size == 0:
                return False

        for chunk in chunk_files:
            transcript = chunk.with_name(chunk.name + "_transcript.json")
            if transcript.exists():
                try:
                    data = json.loads(transcript.read_text())
                except (json.JSONDecodeError, OSError):
                    return False
                if not isinstance(data, list) or not data:
                    return False
                if not isinstance(data[0], dict) or "word" not in data[0]:
                    return False

        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(chunk_files[0])],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return False
            try:
                duration = float(result.stdout.strip())
            except ValueError:
                return False
            if duration <= 0:
                return False
        except (subprocess.TimeoutExpired, OSError):
            return False

        return True

    def _fire_progress(self, **data) -> None:
        """Forward progress events to the configured callback (if any)."""
        if self._progress_callback:
            try:
                self._progress_callback(**data)
            except Exception as e:  # never let progress reporting break processing
                self._log(f"Progress callback failed: {e}", "WARNING")

    def _log(self, message: str, level: str = "INFO"):
        """Write a log message using centralized logging utility."""
        log_message(self.log_file, message, level)
        self._logger.log(_LOG_LEVEL_MAP.get(level, logging.INFO), message)

    def set_book_count(self, count: int) -> None:
        """Set the total number of books to be processed for progress reporting."""
        self._book_count = count

    # ============================================================================
    # MAIN PUBLIC METHODS
    # ============================================================================

    def process_audio_file(self, source_file: str, book_data: dict) -> str:
        """
        Process audio file to remove profanity.

        For files >150MB, automatically splits into chunks, processes separately,
        and reassembles to avoid upload timeout issues.

        Args:
            source_file: Path to source audio file
            book_data: Book metadata dictionary

        Returns:
            str: Path to cleaned file.

        Raises:
            AudioCleaningError: When processing fails. Caller is responsible for
                handling the failure (e.g. skip the book and record the error);
                this method never returns the original un-cleaned source_file.
        """
        try:
            book_title = book_data.get("title", "Unknown Title")
            asin = book_data.get("asin", "")
            if asin and self._is_already_processed(asin):
                self._log_start_header(book_title)
                self._log(f"Skipping — ASIN {asin} already processed (resume state)")
                self._fire_progress(
                    asin=asin, book_title=book_title, status="skipped",
                    stage="skipped", books_done=self.total_processed, books_total=self._book_count,
                )
                return source_file

            self._fire_progress(
                asin=asin, book_title=book_title, status="processing",
                stage="staging", books_done=self.total_processed, books_total=self._book_count,
            )
            self._log_start_header(book_title)

            if asin:
                is_resumable, resume_wd = self._is_resumable(asin)
                if is_resumable:
                    self._log(f"Resuming ASIN {asin} from working dir: {resume_wd}")
                    self._fire_progress(
                        asin=asin, book_title=book_title, status="processing",
                        stage="resuming", books_done=self.total_processed, books_total=self._book_count,
                    )

            if asin:
                book_wd = str(self.working_dir / asin)
                self._mark_resume_status(asin, "in_progress", working_dir=book_wd)

            if self.config.remote_whisper_url:
                parsed = urllib.parse.urlparse(self.config.remote_whisper_url)
                host = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                if not host:
                    raise AudioCleaningError(
                        f"Whisper backend URL has no host: {self.config.remote_whisper_url}"
                    )
                last_error = None
                for attempt in range(12):
                    try:
                        with socket.create_connection((host, port), timeout=5):
                            break
                    except (OSError, socket.error) as e:
                        last_error = e
                        if attempt < 11:
                            time.sleep(5)
                else:
                    raise AudioCleaningError(
                        f"Whisper backend at {self.config.remote_whisper_url} is unreachable after 12 attempts: {last_error}"
                    )

            paths = self._setup_output_paths(source_file, book_data)
            self._log_configuration(source_file, paths)

            # Initialize plugger (with chunking params if needed)
            plugger = self._initialize_monkeyplug(source_file, paths)

            self._fire_progress(
                asin=asin, book_title=book_title, status="processing",
                stage="uploading", books_done=self.total_processed, books_total=self._book_count,
            )

            # Let WhisperPlugger handle everything internally (including chunking)
            file_size_mb = os.path.getsize(source_file) / (1024 * 1024)
            if file_size_mb > self.CHUNKING_THRESHOLD_MB:
                self._log("File is large (>150MB), will process with chunking")
            else:
                self._log_transcription_start(source_file,
                    transcript_exists=paths["transcript_file"] and os.path.exists(paths["transcript_file"]))

            self._fire_progress(
                asin=asin, book_title=book_title, status="processing",
                stage="transcribing", books_done=self.total_processed, books_total=self._book_count,
            )

            # Per-chunk progress monitoring. MonkeyPlug processes chunks
            # sequentially; a background thread counts transcript JSONs as
            # they land, giving the agent live "3 of 9 chunks done" updates
            # via get_cleaning_progress() without polling overhead.
            chunk_monitor = None
            if file_size_mb > self.CHUNKING_THRESHOLD_MB:
                # MonkeyPlug creates <working_dir>/<input_filename>/chunks/
                # so we search for the chunks/ subdir dynamically.
                _wd = Path(paths["working_dir"])
                _stop = threading.Event()
                _done = 0
                _total = 0
                _chunk_dir_resolved = None

                def _find_chunk_dir():
                    for d in _wd.rglob("chunks"):
                        if d.is_dir():
                            return d
                    return None

                def _watch():
                    nonlocal _done, _total, _chunk_dir_resolved
                    while not _stop.is_set():
                        if _chunk_dir_resolved is None:
                            _chunk_dir_resolved = _find_chunk_dir()
                        if _chunk_dir_resolved and _chunk_dir_resolved.exists():
                            all_files = list(_chunk_dir_resolved.glob("*_chunk_*.*"))
                            audio = [f for f in all_files
                                     if not f.name.endswith("_transcript.json")
                                     and f.suffix.lower() != ".json"]
                            if _total == 0 and audio:
                                _total = len(audio)
                            transcripts = [f for f in all_files
                                          if f.name.endswith("_transcript.json")]
                            current = len(transcripts)
                            if current > _done and _total > 0:
                                _done = current
                                self._fire_progress(
                                    asin=asin, book_title=book_title,
                                    status="processing", stage="transcribing",
                                    books_done=self.total_processed,
                                    books_total=self._book_count,
                                    chunk_done=_done, chunk_total=_total,
                                )
                        time.sleep(2)

                chunk_monitor = threading.Thread(target=_watch, daemon=True)
                chunk_monitor.start()

            cleaned_file = plugger.EncodeCleanAudio()

            if chunk_monitor is not None:
                _stop.set()
                chunk_monitor.join(timeout=5)
            
            # Validate that a file path was returned
            if cleaned_file is None:
                # MonkeyPlug may return None but still create the file
                if os.path.exists(paths["output_file"]):
                    cleaned_file = paths["output_file"]
                    self._log("Processing complete (using output file path)")
                else:
                    raise AudioCleaningError("Processing returned None - no output file was created")
            
            if not os.path.exists(cleaned_file):
                raise AudioCleaningError(f"Cleaned file was not created: {cleaned_file}")
            
            self._log_completion(plugger, cleaned_file, paths.get("transcript_file"))
            
            output_size = os.path.getsize(cleaned_file)
            if output_size == 0:
                raise AudioCleaningError(f"Output file is empty: {cleaned_file}")

            self.total_processed += 1
            self._mark_resume_status(asin, "done")
            self._cleanup_book_working_dir(asin)
            self._fire_progress(
                asin=asin, book_title=book_title, status="done",
                stage="done",
                books_done=self.total_processed, books_total=self._book_count,
                profanities=self.total_profanities - getattr(self, "_last_profanity_count", 0),
            )
            self._last_profanity_count = self.total_profanities
            return cleaned_file
        except Exception as e:
            self.total_failed += 1
            title = book_data.get("title", "Unknown")
            asin_fail = book_data.get("asin", "")
            self._log(f"Processing failed for {title}: {e}", "ERROR")
            book_wd_fail = str(self.working_dir / asin_fail) if asin_fail else None
            self._mark_resume_status(asin_fail, "in_progress", working_dir=book_wd_fail)
            self._fire_progress(
                asin=asin_fail, book_title=title, status="failed",
                stage="failed", error=str(e),
                books_done=self.total_processed, books_total=self._book_count,
            )
            if isinstance(e, AudioCleaningError):
                raise
            raise AudioCleaningError(str(e)) from e

    def cleanup_working_directory(self, keep_transcripts: bool = None):
        """
        Clean up temporary working directory.

        Args:
            keep_transcripts: Override config setting for keeping transcripts
        """
        if keep_transcripts is None:
            keep_transcripts = self.config.save_transcripts

        # If copy_instead_of_move is enabled, preserve all intermediate files
        if self.copy_mode:
            self._log(
                f"Copy mode enabled - preserving all intermediate files "
                f"(chunks, transcripts, etc.) in: {self.working_dir}"
            )
            return

        if not keep_transcripts:
            try:
                if self.working_dir.exists():
                    shutil.rmtree(self.working_dir)
                    self._log("Cleaned up working directory")
            except Exception as e:
                self._log(f"Failed to cleanup working directory: {e}", "WARNING")
        else:
            self._log(f"Keeping transcripts in: {self.working_dir}")

    def log_statistics(self):
        separator = "=" * 42
        self._log(separator)
        self._log("Profanity Cleaning Statistics")
        self._log(separator)
        self._log(f"Files processed: {self.total_processed}")
        self._log(f"Files failed: {self.total_failed}")
        self._log(f"Total profanities removed: {self.total_profanities}")
        self._log(separator)

    # ============================================================================
    # SETUP AND INITIALIZATION
    # ============================================================================

    def _setup_output_paths(self, source_file: str, book_data: dict) -> dict:
        """
        Setup working directory and output file paths.

        Args:
            source_file: Path to source audio file
            book_data: Book metadata dictionary

        Returns:
            dict: Paths dictionary with keys: working_dir, output_file,
                  transcript_file, ext, audio_format
        """
        book_id = book_data.get("asin", "unknown")
        book_working_dir = self.working_dir / book_id
        book_working_dir.mkdir(parents=True, exist_ok=True)

        original_filename = os.path.basename(source_file)
        filename_base, ext = os.path.splitext(original_filename)

        sanitized_base = sanitize_name(filename_base)
        sanitized_filename = f"{sanitized_base}{ext}"

        ext = ext.lstrip(".")
        audio_format = ext.lower()

        output_file = str(book_working_dir / sanitized_filename)
        transcript_file = None
        if self.config.save_transcripts:
            transcript_name = f"{sanitized_base}_transcript.json"
            transcript_file = str(book_working_dir / transcript_name)

        return {
            "working_dir": book_working_dir,
            "output_file": output_file,
            "transcript_file": transcript_file,
            "ext": ext,
            "audio_format": audio_format,
        }

    def _initialize_monkeyplug(self, source_file: str, paths: dict) -> WhisperPlugger:
        """
        Create and configure MonkeyPlug WhisperPlugger instance.

        Args:
            source_file: Path to source audio file
            paths: Paths dictionary from _setup_output_paths

        Returns:
            WhisperPlugger: Configured instance

        Raises:
            AudioCleaningError: If initialization fails
        """
        try:
            input_transcript = None
            transcript_file = paths["transcript_file"]
            if transcript_file and os.path.exists(transcript_file):
                input_transcript = transcript_file
                self._log("Found existing transcript, will reuse it")

            # Determine if we should use chunking for large files
            file_size = os.path.getsize(source_file)
            use_chunking = file_size > self.CHUNKING_THRESHOLD_MB * 1024 * 1024
            
            params = {
                "iFileSpec": source_file,
                "oFileSpec": paths["output_file"],
                "oAudioFileFormat": paths["audio_format"],
                "iSwearsFileSpec": self.config.swears_file,
                "mDir": None,
                "mName": None,
                "torchThreads": 0,
                "outputJson": paths["transcript_file"],
                "reportFormat": "json" if getattr(self.config, "debug", False) else "txt",
                "inputTranscript": input_transcript,
                "saveTranscript": self.config.save_transcripts,
                "remoteUrl": self.config.remote_whisper_url,
                "apiTimeout": self.config.timeout,
                "pollInterval": getattr(self.config, "poll_interval", 30),
                "confidenceThreshold": self.config.confidence_threshold,
                "beep": self.config.beep_mode,
                "force": False,
                "useChunking": use_chunking,
                "chunkingWorkDir": str(paths["working_dir"]) if use_chunking else None,
                "parallelEncoding": getattr(self.config, "parallel_encoding", True),
                "maxWorkers": getattr(self.config, "max_workers", None),
                "verbose": getattr(self.config, "debug", False),
            }
            
            # Log and print MonkeyPlug parameters if debug enabled
            separator = "=" * 70
            header = "MONKEYPLUG PARAMETERS"
            
            self._log(separator)
            self._log(header)
            self._log(separator)
            
            if getattr(self.config, "debug", False):
                print(separator)
                print(header)
                print(separator)
            
            for key, value in params.items():
                if value is not None:
                    line = f"  {key}: {value}"
                    self._log(line)
                    if getattr(self.config, "debug", False):
                        print(line)
            
            self._log(separator)
            if getattr(self.config, "debug", False):
                print(separator)

            return WhisperPlugger(**params)
        except Exception as e:
            raise AudioCleaningError(f"Failed to initialize MonkeyPlug: {e}")

    # ============================================================================
    # LOGGING HELPERS
    # ============================================================================

    def _log_start_header(self, book_title: str):
        separator = "=" * 38
        self._log(separator)
        self._log("Starting profanity cleaning")
        self._log(f"Book: {book_title}")
        self._log(separator)

    def _log_configuration(self, source_file: str, paths: dict):
        self._log("Configuration:")
        self._log(f"  Source file: {source_file}")
        self._log(f"  Detected extension: '{paths['ext']}'")
        self._log(f"  Audio format for MonkeyPlug: '{paths['audio_format']}'")
        self._log(f"  Output file: {paths['output_file']}")
        self._log(f"  Remote Whisper URL: {self.config.remote_whisper_url}")
        self._log(f"  Swears file: {self.config.swears_file}")
        self._log(f"  Timeout: {self.config.timeout}s")
        self._log(f"  Beep mode: {self.config.beep_mode}")
        threshold = self.config.confidence_threshold
        self._log(f"  Confidence threshold: {threshold}")
        parallel = getattr(self.config, "parallel_encoding", True)
        self._log(f"  Parallel encoding: {parallel} (multi-core processing)")
        if getattr(self.config, "debug", False):
            self._log(f"  Debug mode: ENABLED (censorship report will be generated)")

    def _log_transcription_start(
        self, source_file: str, transcript_exists: bool = False
    ):
        file_size_mb = os.path.getsize(source_file) / (1024 * 1024)

        if transcript_exists:
            self._log("Using existing transcript (skipping transcription)")
        else:
            self._log("Transcription started (this may take a while)...")

        self._log(f"Source: {os.path.basename(source_file)}")
        self._log(f"File size: {file_size_mb:.1f} MB")

    def _log_completion(
        self, plugger: WhisperPlugger, cleaned_file: str, transcript_file: Optional[str]
    ):
        profanity_count = 0
        if hasattr(plugger, "naughtyWordList"):
            profanity_count = len(plugger.naughtyWordList)
        self.total_profanities += profanity_count

        cleaned_size_mb = os.path.getsize(cleaned_file) / (1024 * 1024)

        separator = "=" * 38
        self._log(separator)
        self._log("Cleaning complete!")
        self._log(f"Profanities removed: {profanity_count}")
        self._log(f"Output file: {cleaned_file}")
        self._log(f"Output size: {cleaned_size_mb:.1f} MB")

        if transcript_file and os.path.exists(transcript_file):
            transcript_size_kb = os.path.getsize(transcript_file) / 1024
            self._log(f"Transcript saved: {transcript_file}")
            self._log(f"Transcript size: {transcript_size_kb:.1f} KB")

        self._log(separator)
