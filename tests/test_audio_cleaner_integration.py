"""
Test AudioCleaner integration layer.

This module tests the AudioCleaner class orchestration logic and integration
with MonkeyPlug. It does NOT test MonkeyPlug's internal chunking logic - that's
MonkeyPlug's responsibility. These tests focus on:
- Path setup and configuration
- MonkeyPlug initialization
- Error handling and fallbacks
- Statistics tracking
- Logging integration
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

# Mock monkeyplug before importing audio_cleaner
sys.modules['monkeyplug'] = MagicMock()
sys.modules['monkeyplug.monkeyplug'] = MagicMock()
sys.modules['monkeyplug.audio_chunker'] = MagicMock()

from openaudible_to_audiobookshelf.audio_cleaner import AudioCleaner, AudioCleaningError


@pytest.fixture
def base_config():
    """Create a basic config mock with common defaults."""
    config = Mock()
    config.working_directory = "/tmp/test"
    config.save_transcripts = False
    config.swears_file = ""
    config.remote_whisper_url = "http://whisper:8000"
    config.timeout = 600
    config.beep_mode = False
    config.debug = False
    config.confidence_threshold = 0.75
    config.parallel_encoding = True
    config.max_workers = None
    config.poll_interval = 30
    config.copy_instead_of_move = False
    return config


@pytest.fixture
def mock_log_file():
    """Create a mock log file."""
    return Mock()


class TestAudioCleanerInitialization:
    """Test AudioCleaner initialization."""

    def test_creates_working_directory(self, base_config, mock_log_file):
        """Test that working directory is created on init."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = os.path.join(tmpdir, "work")
            base_config.working_directory = work_dir

            cleaner = AudioCleaner(base_config, mock_log_file)

            assert os.path.exists(work_dir)
            assert cleaner.working_dir == Path(work_dir)


class TestResumeStateSchema:
    """Test the extended resume state schema with backward compatibility."""

    def test_old_string_format_still_recognized(self, base_config, mock_log_file):
        """'done' and 'failed' string entries work as before."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            cleaner = AudioCleaner(base_config, mock_log_file)
            cleaner._resume_state = {"B001": "done", "B002": "failed"}
            assert cleaner._is_already_processed("B001") is True
            assert cleaner._is_already_processed("B002") is False
            assert cleaner._is_already_processed("B003") is False

    def test_new_object_format_done_status(self, base_config, mock_log_file):
        """Object entry with status 'done' is recognized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            cleaner = AudioCleaner(base_config, mock_log_file)
            cleaner._resume_state = {"B001": {"status": "done", "working_dir": "/tmp/B001"}}
            assert cleaner._is_already_processed("B001") is True

    def test_in_progress_not_considered_done(self, base_config, mock_log_file):
        """in_progress entry is NOT 'already processed'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            cleaner = AudioCleaner(base_config, mock_log_file)
            cleaner._resume_state = {"B001": {"status": "in_progress", "working_dir": "/tmp/B001"}}
            assert cleaner._is_already_processed("B001") is False

    def test_mark_resume_status_string_format(self, base_config, mock_log_file):
        """_mark_resume_status without working_dir writes old string format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            cleaner = AudioCleaner(base_config, mock_log_file)
            cleaner._mark_resume_status("B001", "done")
            assert cleaner._resume_state["B001"] == "done"

    def test_mark_resume_status_object_format(self, base_config, mock_log_file):
        """_mark_resume_status with working_dir writes object format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            cleaner = AudioCleaner(base_config, mock_log_file)
            cleaner._mark_resume_status("B001", "in_progress", working_dir="/tmp/B001")
            assert cleaner._resume_state["B001"] == {"status": "in_progress", "working_dir": "/tmp/B001"}

    def test_mark_resume_status_in_progress_requires_working_dir(self, base_config, mock_log_file):
        """_mark_resume_status raises ValueError for in_progress without working_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            cleaner = AudioCleaner(base_config, mock_log_file)
            with pytest.raises(ValueError, match="working_dir is required"):
                cleaner._mark_resume_status("B001", "in_progress")

    def test_is_resumable_valid_working_dir(self, base_config, mock_log_file):
        """Valid working dir with chunks passes validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            cleaner = AudioCleaner(base_config, mock_log_file)
            book_dir = Path(tmpdir) / "B001"
            book_dir.mkdir()
            cleaner._resume_state = {"B001": {"status": "in_progress", "working_dir": str(book_dir)}}
            with patch.object(cleaner, '_validate_working_dir', return_value=True):
                is_resumable, wd = cleaner._is_resumable("B001")
                assert is_resumable is True
                assert wd == str(book_dir)

    def test_is_resumable_missing_dir(self, base_config, mock_log_file):
        """Missing working dir returns not resumable and resets entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            cleaner = AudioCleaner(base_config, mock_log_file)
            cleaner._resume_state = {"B001": {"status": "in_progress", "working_dir": "/nonexistent/B001"}}
            is_resumable, wd = cleaner._is_resumable("B001")
            assert is_resumable is False
            assert wd == ""
            assert cleaner._resume_state["B001"] == "failed"

    def test_cleanup_book_working_dir(self, base_config, mock_log_file):
        """_cleanup_book_working_dir removes the ASIN's subdirectory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            cleaner = AudioCleaner(base_config, mock_log_file)
            book_dir = Path(tmpdir) / "B001"
            book_dir.mkdir()
            (book_dir / "some_file.txt").write_text("data")
            assert book_dir.exists()
            cleaner._cleanup_book_working_dir("B001")
            assert not book_dir.exists()


class TestSetupOutputPaths:
    """Test _setup_output_paths method."""

    def test_creates_book_specific_directory(self, base_config, mock_log_file):
        """Test that book-specific working directory is created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir

            cleaner = AudioCleaner(base_config, mock_log_file)
            book_data = {"asin": "TEST123", "title": "Test Book"}
            source_file = "/path/to/test_book.m4b"

            paths = cleaner._setup_output_paths(source_file, book_data)

            assert "TEST123" in str(paths["working_dir"])
            assert os.path.exists(paths["working_dir"])

    def test_sanitizes_filename(self, base_config, mock_log_file):
        """Test that filenames are sanitized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir

            cleaner = AudioCleaner(base_config, mock_log_file)
            book_data = {"asin": "TEST123", "title": "Test Book"}
            source_file = "/path/to/book with spaces & commas,.m4b"

            paths = cleaner._setup_output_paths(source_file, book_data)

            output_filename = os.path.basename(paths["output_file"])
            assert " " not in output_filename
            assert "&" not in output_filename
            assert "," not in output_filename

    @pytest.mark.parametrize(
        "save_transcripts, expect_transcript_path",
        [(True, True), (False, False)],
        ids=["transcripts_enabled", "transcripts_disabled"],
    )
    def test_transcript_path_respects_config(
        self,
        base_config,
        mock_log_file,
        save_transcripts,
        expect_transcript_path,
    ):
        """Test that transcript path creation follows the save_transcripts setting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            base_config.save_transcripts = save_transcripts

            cleaner = AudioCleaner(base_config, mock_log_file)
            book_data = {"asin": "TEST123", "title": "Test"}
            source_file = "/path/to/test.m4b"

            paths = cleaner._setup_output_paths(source_file, book_data)

            if expect_transcript_path:
                assert paths["transcript_file"] is not None
                assert "_transcript.json" in paths["transcript_file"]
            else:
                assert paths["transcript_file"] is None


class TestInitializeMonkeyplug:
    """Test _initialize_monkeyplug method."""

    @patch('os.path.getsize', return_value=100 * 1024 * 1024)  # 100MB file
    @patch('openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger')
    def test_creates_whisper_plugger_with_correct_params(self, mock_plugger_class, mock_getsize, base_config, mock_log_file):
        """Test that WhisperPlugger is initialized with correct parameters."""
        base_config.swears_file = "/path/to/swears.txt"
        base_config.save_transcripts = True

        cleaner = AudioCleaner(base_config, mock_log_file)
        
        source_file = "/path/to/test.m4b"
        paths = {
            "working_dir": Path("/tmp/test"),
            "output_file": "/output/test.m4b",
            "audio_format": "m4a",
            "transcript_file": "/output/transcript.json"
        }

        cleaner._initialize_monkeyplug(source_file, paths)

        mock_plugger_class.assert_called_once()
        call_kwargs = mock_plugger_class.call_args[1]
        
        assert call_kwargs["iSwearsFileSpec"] == "/path/to/swears.txt"
        assert call_kwargs["remoteUrl"] == "http://whisper:8000"
        assert call_kwargs["apiTimeout"] == 600
        assert call_kwargs["useChunking"] is False

    @patch('os.path.getsize', return_value=100 * 1024 * 1024)  # 100MB file
    @patch('openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger')
    def test_reuses_existing_transcript(self, mock_plugger_class, mock_getsize, base_config, mock_log_file):
        """Test that existing transcript is passed to WhisperPlugger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = os.path.join(tmpdir, "transcript.json")
            with open(transcript_file, 'w') as f:
                f.write('{"test": "data"}')

            base_config.working_directory = tmpdir
            base_config.save_transcripts = True

            cleaner = AudioCleaner(base_config, mock_log_file)
            
            paths = {
                "working_dir": Path(tmpdir),
                "output_file": "/output/test.m4b",
                "audio_format": "m4a",
                "transcript_file": transcript_file
            }

            cleaner._initialize_monkeyplug("/path/to/test.m4b", paths)

            call_kwargs = mock_plugger_class.call_args[1]
            assert call_kwargs["inputTranscript"] == transcript_file

    @patch('os.path.getsize', return_value=100 * 1024 * 1024)  # 100MB file
    @patch('openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger', side_effect=Exception("Init failed"))
    def test_raises_audio_cleaning_error_on_failure(self, mock_plugger_class, mock_getsize, base_config, mock_log_file):
        """Test that AudioCleaningError is raised when initialization fails."""
        cleaner = AudioCleaner(base_config, mock_log_file)
        
        paths = {
            "working_dir": Path("/tmp/test"),
            "output_file": "/output/test.m4b",
            "audio_format": "m4a",
            "transcript_file": None
        }

        with pytest.raises(AudioCleaningError, match="Failed to initialize MonkeyPlug"):
            cleaner._initialize_monkeyplug("/path/to/test.m4b", paths)


class TestProcessAudioFile:
    """Test process_audio_file method - integration with MonkeyPlug."""

    def setup_method(self):
        # Clean any stale resume state shared across tests using /tmp working dirs
        resume = "/tmp/profanity_cleaning_resume.json"
        if os.path.exists(resume):
            os.remove(resume)

    @patch('openaudible_to_audiobookshelf.audio_cleaner.socket.create_connection')
    @patch('openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger')
    def test_uses_chunker_for_large_files(self, mock_plugger_class, mock_create_connection, base_config, mock_log_file):
        """Test that AudioChunker is used for large files."""
        mock_create_connection.return_value.__enter__ = lambda self: self
        mock_create_connection.return_value.__exit__ = lambda self, *args: None
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a large test file (>150MB)
            source_file = os.path.join(tmpdir, "large.m4b")
            with open(source_file, 'wb') as f:
                f.write(b'x' * (200 * 1024 * 1024))
            
            output_file = os.path.join(tmpdir, "output.m4b")
            with open(output_file, 'wb') as f:
                f.write(b'x' * (180 * 1024 * 1024))

            base_config.working_directory = tmpdir

            # Mock plugger to return cleaned file
            mock_plugger_instance = MagicMock()
            mock_plugger_instance.EncodeCleanAudio.return_value = output_file
            mock_plugger_instance.naughtyWordList = []
            mock_plugger_class.return_value = mock_plugger_instance

            cleaner = AudioCleaner(base_config, mock_log_file)
            book_data = {"asin": "TEST123", "title": "Large Book"}

            result = cleaner.process_audio_file(source_file, book_data)

            # Verify WhisperPlugger was called with useChunking=True for large files
            call_kwargs = mock_plugger_class.call_args[1]
            assert call_kwargs["useChunking"] is True
            assert call_kwargs["chunkingWorkDir"] is not None
            
            assert result == output_file
            assert cleaner.total_processed == 1

    @patch('openaudible_to_audiobookshelf.audio_cleaner.socket.create_connection')
    @patch('openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger')
    def test_uses_direct_encoding_for_small_files(self, mock_plugger_class, mock_create_connection, base_config, mock_log_file):
        """Test that direct encoding is used for small files."""
        mock_create_connection.return_value.__enter__ = lambda self: self
        mock_create_connection.return_value.__exit__ = lambda self, *args: None
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a small test file (<150MB)
            source_file = os.path.join(tmpdir, "small.m4b")
            with open(source_file, 'wb') as f:
                f.write(b'x' * (50 * 1024 * 1024))
            
            output_file = os.path.join(tmpdir, "output.m4b")
            with open(output_file, 'wb') as f:
                f.write(b'x' * (45 * 1024 * 1024))

            base_config.working_directory = tmpdir

            # Mock plugger to return cleaned file
            mock_plugger_instance = MagicMock()
            mock_plugger_instance.EncodeCleanAudio.return_value = output_file
            mock_plugger_instance.naughtyWordList = ["bad", "words"]
            mock_plugger_class.return_value = mock_plugger_instance

            cleaner = AudioCleaner(base_config, mock_log_file)
            book_data = {"asin": "TEST123", "title": "Small Book"}

            result = cleaner.process_audio_file(source_file, book_data)

            # Verify WhisperPlugger was called with useChunking=False for small files
            call_kwargs = mock_plugger_class.call_args[1]
            assert call_kwargs["useChunking"] is False
            assert call_kwargs["chunkingWorkDir"] is None
            
            # Verify direct encoding was used
            mock_plugger_instance.EncodeCleanAudio.assert_called_once()
            
            assert result == output_file
            assert cleaner.total_processed == 1
            assert cleaner.total_profanities == 2  # len(naughtyWordList)

    @patch('openaudible_to_audiobookshelf.audio_cleaner.socket.create_connection')
    @patch('openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger')
    def test_returns_original_file_on_error(self, mock_plugger_class, mock_create_connection, base_config, mock_log_file):
        """DR-1: process_audio_file raises AudioCleaningError on failure (no silent fallback)."""
        from openaudible_to_audiobookshelf.audio_cleaner import AudioCleaningError
        mock_create_connection.return_value.__enter__ = lambda self: self
        mock_create_connection.return_value.__exit__ = lambda self, *args: None
        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = os.path.join(tmpdir, "test.m4b")
            with open(source_file, 'wb') as f:
                f.write(b'test data')

            base_config.working_directory = tmpdir

            # Mock WhisperPlugger to raise exception
            mock_plugger_class.side_effect = Exception("Processing failed")

            cleaner = AudioCleaner(base_config, mock_log_file)
            book_data = {"asin": "TEST123", "title": "Test Book"}

            with pytest.raises(AudioCleaningError, match="Processing failed"):
                cleaner.process_audio_file(source_file, book_data)

            assert cleaner.total_failed == 1
            assert cleaner.total_processed == 0


class TestCleanupWorkingDirectory:
    """Test cleanup_working_directory method."""

    def test_removes_directory_when_not_keeping_transcripts(self):
        """Test that working directory is removed when not saving transcripts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = os.path.join(tmpdir, "work")
            os.makedirs(work_dir)
            
            # Create some files
            test_file = os.path.join(work_dir, "test.txt")
            with open(test_file, 'w') as f:
                f.write("test")

            config = Mock()
            config.working_directory = work_dir
            config.save_transcripts = False
            config.copy_instead_of_move = False
            log_file = Mock()

            cleaner = AudioCleaner(config, log_file)
            cleaner.cleanup_working_directory()

            assert not os.path.exists(work_dir)

    def test_keeps_directory_when_saving_transcripts(self, base_config, mock_log_file):
        """Test that working directory is kept when saving transcripts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = os.path.join(tmpdir, "work")
            os.makedirs(work_dir)
            
            test_file = os.path.join(work_dir, "test.txt")
            with open(test_file, 'w') as f:
                f.write("test")

            base_config.working_directory = work_dir
            base_config.save_transcripts = True

            cleaner = AudioCleaner(base_config, mock_log_file)
            cleaner.cleanup_working_directory()

            assert os.path.exists(work_dir)
            assert os.path.exists(test_file)

    def test_preserves_files_in_copy_mode(self, base_config, mock_log_file):
        """Test that all files are preserved when copy mode is enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = os.path.join(tmpdir, "work")
            os.makedirs(work_dir)
            
            test_file = os.path.join(work_dir, "test.txt")
            with open(test_file, 'w') as f:
                f.write("test")

            base_config.working_directory = work_dir
            base_config.copy_instead_of_move = True  # Copy mode overrides

            cleaner = AudioCleaner(base_config, mock_log_file)
            cleaner.cleanup_working_directory()

            assert os.path.exists(work_dir)
            assert os.path.exists(test_file)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
