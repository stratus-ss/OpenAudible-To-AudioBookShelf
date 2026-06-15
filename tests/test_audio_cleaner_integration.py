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
from unittest.mock import Mock, patch, MagicMock, call

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

    def test_initializes_statistics(self, base_config, mock_log_file):
        """Test that statistics counters are initialized."""
        cleaner = AudioCleaner(base_config, mock_log_file)

        assert cleaner.total_processed == 0
        assert cleaner.total_failed == 0
        assert cleaner.total_profanities == 0

    def test_reads_copy_mode_from_config(self, base_config, mock_log_file):
        """Test that copy mode is read from config."""
        base_config.copy_instead_of_move = True

        cleaner = AudioCleaner(base_config, mock_log_file)

        assert cleaner.copy_mode is True


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

    def test_handles_m4b_format_conversion(self, base_config, mock_log_file):
        """Test that .m4b extension is converted to m4a format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir

            cleaner = AudioCleaner(base_config, mock_log_file)
            book_data = {"asin": "TEST123", "title": "Test"}
            source_file = "/path/to/test.m4b"

            paths = cleaner._setup_output_paths(source_file, book_data)

            assert paths["audio_format"] == "m4b"
            assert paths["ext"] == "m4b"

    def test_includes_transcript_path_when_enabled(self, base_config, mock_log_file):
        """Test that transcript path is included when save_transcripts is True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir
            base_config.save_transcripts = True

            cleaner = AudioCleaner(base_config, mock_log_file)
            book_data = {"asin": "TEST123", "title": "Test"}
            source_file = "/path/to/test.m4b"

            paths = cleaner._setup_output_paths(source_file, book_data)

            assert paths["transcript_file"] is not None
            assert "_transcript.json" in paths["transcript_file"]

    def test_excludes_transcript_path_when_disabled(self, base_config, mock_log_file):
        """Test that transcript path is None when save_transcripts is False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_config.working_directory = tmpdir

            cleaner = AudioCleaner(base_config, mock_log_file)
            book_data = {"asin": "TEST123", "title": "Test"}
            source_file = "/path/to/test.m4b"

            paths = cleaner._setup_output_paths(source_file, book_data)

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
        
        assert call_kwargs["iFileSpec"] == source_file
        assert call_kwargs["oFileSpec"] == paths["output_file"]
        assert call_kwargs["oAudioFileFormat"] == "m4a"
        assert call_kwargs["iSwearsFileSpec"] == "/path/to/swears.txt"
        assert call_kwargs["remoteUrl"] == "http://whisper:8000"
        assert call_kwargs["apiTimeout"] == 600
        assert call_kwargs["beep"] is False
        assert call_kwargs["saveTranscript"] is True

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

    @patch('openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger')
    def test_uses_chunker_for_large_files(self, mock_plugger_class, base_config, mock_log_file):
        """Test that AudioChunker is used for large files."""
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

    @patch('openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger')
    def test_uses_direct_encoding_for_small_files(self, mock_plugger_class, base_config, mock_log_file):
        """Test that direct encoding is used for small files."""
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

    @patch('openaudible_to_audiobookshelf.audio_cleaner.WhisperPlugger')
    def test_returns_original_file_on_error(self, mock_plugger_class, base_config, mock_log_file):
        """Test that original file is returned when processing fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = os.path.join(tmpdir, "test.m4b")
            with open(source_file, 'wb') as f:
                f.write(b'test data')

            base_config.working_directory = tmpdir

            # Mock WhisperPlugger to raise exception
            mock_plugger_class.side_effect = Exception("Processing failed")

            cleaner = AudioCleaner(base_config, mock_log_file)
            book_data = {"asin": "TEST123", "title": "Test Book"}

            result = cleaner.process_audio_file(source_file, book_data)

            assert result == source_file
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


class TestLogStatistics:
    """Test log_statistics method."""

    def test_writes_statistics_to_log(self, base_config, mock_log_file):
        """Test that statistics are written to log file."""
        cleaner = AudioCleaner(base_config, mock_log_file)
        cleaner.total_processed = 10
        cleaner.total_failed = 2
        cleaner.total_profanities = 150

        cleaner.log_statistics()

        # Verify log_file.write was called
        assert mock_log_file.write.called
        
        # Collect all log content
        calls = [str(call) for call in mock_log_file.write.call_args_list]
        log_content = "".join(calls)
        
        # Verify all statistics appear in log
        assert "10" in log_content
        assert "2" in log_content
        assert "150" in log_content
        assert "Statistics" in log_content


class TestLoggingMethods:
    """Test logging helper methods."""

    def test_log_writes_with_timestamp(self):
        """Test that _log includes timestamp."""
        config = Mock()
        config.working_directory = "/tmp/test"
        config.copy_instead_of_move = False
        log_file = Mock()

        cleaner = AudioCleaner(config, log_file)
        cleaner._log("Test message", "INFO")

        log_file.write.assert_called_once()
        call_content = str(log_file.write.call_args[0][0])
        
        assert "INFO" in call_content
        assert "Test message" in call_content

    def test_log_start_header_formats_correctly(self):
        """Test that start header is properly formatted."""
        config = Mock()
        config.working_directory = "/tmp/test"
        config.copy_instead_of_move = False
        log_file = Mock()

        cleaner = AudioCleaner(config, log_file)
        cleaner._log_start_header("Test Book Title")

        calls = [str(call) for call in log_file.write.call_args_list]
        log_content = "".join(calls)
        
        assert "Test Book Title" in log_content
        assert "profanity cleaning" in log_content
        assert "=" in log_content  # Separator


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
