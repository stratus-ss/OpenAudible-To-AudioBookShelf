"""
Audio profanity cleaning integration with MonkeyPlug.

This module provides the AudioCleaner class which integrates MonkeyPlug
profanity cleaning into the OpenAudible-To-AudioBookShelf pipeline.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from monkeyplug.monkeyplug import WhisperPlugger
from monkeyplug.audio_chunker import AudioChunker
from modules.utils import sanitize_name


class AudioCleaningError(Exception):
    """Base exception for audio cleaning errors."""
    pass


class AudioCleaner:
    """Manages profanity cleaning of audio files using MonkeyPlug."""

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

        # Track statistics
        self.total_processed = 0
        self.total_failed = 0
        self.total_profanities = 0

    # ============================================================================
    # UTILITY HELPERS
    # ============================================================================

    def _log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now()
        self.log_file.write(f"{timestamp} - {level} - {message}\n")
        self.log_file.flush()

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
            str: Path to cleaned file, or original file if processing fails
        """
        try:
            book_title = book_data.get("title", "Unknown Title")
            self._log_start_header(book_title)
            
            paths = self._setup_output_paths(source_file, book_data)
            self._log_configuration(source_file, paths)
            
            # Initialize plugger
            plugger = self._initialize_monkeyplug(source_file, paths)
            
            # Check if chunking is needed
            chunker = AudioChunker(
                working_dir=str(self.working_dir),
                plugger=plugger,
                parallel_encoding=False
            )
            
            if chunker.needs_chunking(source_file):
                self._log("File is large (>150MB), will process with chunking")
                cleaned_file = chunker.process_with_chunking(source_file, paths["output_file"])
            else:
                self._log_transcription_start(source_file, 
                    transcript_exists=paths["transcript_file"] and os.path.exists(paths["transcript_file"]))
                cleaned_file = plugger.EncodeCleanAudio()
            
            if not os.path.exists(cleaned_file):
                raise AudioCleaningError("Cleaned file was not created")
            
            self._log_completion(plugger, cleaned_file, paths.get("transcript_file"))
            
            # Validate output
            if not os.path.exists(cleaned_file):
                raise AudioCleaningError(f"Output file not found: {cleaned_file}")
            
            output_size = os.path.getsize(cleaned_file)
            if output_size == 0:
                raise AudioCleaningError(f"Output file is empty: {cleaned_file}")

            self.total_processed += 1
            return cleaned_file
        except Exception as e:
            self.total_failed += 1
            title = book_data.get("title", "Unknown")
            self._log(f"Processing failed for {title}: {e}", "ERROR")
            self._log("Using original file")
            return source_file

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
        audio_format = "m4a" if ext.lower() == "m4b" else ext.lower()

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

            return WhisperPlugger(
                iFileSpec=source_file,
                oFileSpec=paths["output_file"],
                oAudioFileFormat=paths["audio_format"],
                iSwearsFileSpec=self.config.swears_file,
                mDir=None,
                mName=None,
                torchThreads=0,
                outputJson=paths["transcript_file"],
                inputTranscript=input_transcript,
                saveTranscript=self.config.save_transcripts,
                remoteUrl=self.config.remote_whisper_url,
                apiTimeout=self.config.timeout,
                pollInterval=5,
                beep=self.config.beep_mode,
                force=False,
                dbug=getattr(self.config, "debug", False),
            )
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
