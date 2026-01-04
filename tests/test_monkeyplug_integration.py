"""
Test MonkeyPlug integration with AudioCleaner.

This module provides tests for the AudioCleaner class and its integration
with the MonkeyPlug profanity cleaning system.
"""
import os
import sys
import tempfile
from unittest.mock import Mock, patch, MagicMock

import pytest

# Mock monkeyplug before importing audio_cleaner
sys.modules['monkeyplug'] = MagicMock()
sys.modules['monkeyplug.monkeyplug'] = MagicMock()
sys.modules['monkeyplug.audio_chunker'] = MagicMock()

from modules.audio_cleaner import AudioCleaner, AudioCleaningError
from modules.config import Config


class TestAudioCleanerInit:
    """Test AudioCleaner initialization."""

    def test_audio_cleaner_creates_working_directory(self):
        """Test that AudioCleaner creates working directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Mock()
            config.working_directory = os.path.join(tmpdir, "test_work")
            config.enable_profanity_cleaning = True

            log_file = Mock()
            cleaner = AudioCleaner(config, log_file)

            assert os.path.exists(config.working_directory)
            assert cleaner.total_processed == 0
            assert cleaner.total_failed == 0

    def test_audio_cleaner_initializes_statistics(self):
        """Test that statistics are initialized to zero."""
        config = Mock()
        config.working_directory = "/tmp/test"
        log_file = Mock()

        cleaner = AudioCleaner(config, log_file)

        assert cleaner.total_processed == 0
        assert cleaner.total_failed == 0
        assert cleaner.total_profanities == 0


class TestProcessAudioFile:
    """Test process_audio_file method."""

    @patch('modules.audio_cleaner.AudioChunker')
    def test_process_audio_file_handles_exception(self, mock_chunker_class):
        """Test that exceptions during processing return original file."""
        with tempfile.NamedTemporaryFile(suffix=".m4b", delete=False) as tmp:
            try:
                config = Mock()
                config.enable_profanity_cleaning = True
                config.working_directory = "/tmp/test"
                config.save_transcripts = False
                config.swears_file = ""
                config.remote_whisper_url = "http://test:8000"
                config.timeout = 600
                config.beep_mode = False
                config.debug = False
                config.copy_instead_of_move = False
                log_file = Mock()

                # Mock AudioChunker to raise exception
                mock_chunker_class.side_effect = Exception("Test error")

                cleaner = AudioCleaner(config, log_file)
                book_data = {"title": "Test Book", "asin": "TEST123"}

                result = cleaner.process_audio_file(tmp.name, book_data)

                assert result == tmp.name
                assert cleaner.total_failed == 1
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)


class TestLogStatistics:
    """Test log_statistics method."""

    def test_log_statistics_writes_to_log_file(self):
        """Test that statistics are written to log file."""
        config = Mock()
        config.working_directory = "/tmp/test"
        log_file = Mock()

        cleaner = AudioCleaner(config, log_file)
        cleaner.total_processed = 5
        cleaner.total_failed = 1
        cleaner.total_profanities = 42

        cleaner.log_statistics()

        assert log_file.write.called
        # Check that all statistics were logged
        calls = [str(call) for call in log_file.write.call_args_list]
        log_content = "".join(calls)
        assert "5" in log_content  # total_processed
        assert "1" in log_content  # total_failed
        assert "42" in log_content  # total_profanities


class TestIntegrationWithConfig:
    """Test integration with Config class."""

    def test_config_has_profanity_cleaning_attributes(self):
        """Test that Config class has profanity cleaning attributes."""
        # Create minimal valid config
        config_args = [
            "--abs-api-token", "test_token",
            "--library-id", "test_lib",
            "--server-url", "http://test",
            "--source-audio-book-directory", "/test/source",
            "--destination-book-directory", "/test/dest",
            "--enable-profanity-cleaning",
            "--remote-whisper-url", "http://whisper:8000",
        ]

        config = Config.from_args(False, *config_args)

        assert hasattr(config, "enable_profanity_cleaning")
        assert hasattr(config, "remote_whisper_url")
        assert hasattr(config, "working_directory")
        assert hasattr(config, "save_transcripts")
        assert hasattr(config, "timeout")
        assert hasattr(config, "beep_mode")
        assert hasattr(config, "confidence_threshold")
        assert hasattr(config, "debug")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
