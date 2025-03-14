import tempfile
import logging
from pathlib import Path
import sys
from modules.config import Config
from collections.abc import Generator

import pytest
import yaml


@pytest.fixture
def config_dict(request: pytest.FixtureRequest) -> Generator[dict, None, None]:
    default_dict = {
        "abs-api-token": "",
        "books-json-path": "$HOME/OpenAudible/books.json",
        "destination-book-directory": "",
        "purchased-how-long-ago": 7,
        "download-program": "OpenAudible",
        "audio-file-extension": ".m4b",
        "libation-folder-cleanup": False,
        "library-id": "",
        "log-file-path": "/tmp/book_processing.txt",
        "server-url": "",
        "source-audio-book-directory": "",
    }
    param = getattr(request, "param", None)
    if isinstance(param, dict):
        default_dict.update(param)
    yield default_dict


@pytest.fixture
def cli_args(config_dict: dict) -> Generator[list[str], None, None]:
    args = []
    for key, value in config_dict.items():
        # Convert underscores to hyphens so that 'download_program' becomes '--download-program'
        arg_name = f"--{key.replace('_', '-')}"
        match value:
            case True:
                args.append(arg_name)
            case None:
                pass
            case False:
                pass
            case _:
                args.append(arg_name)
                args.append(str(value))
    yield args


@pytest.fixture
def yaml_config(tmp_path: Path, config_dict: dict) -> Generator[str, None, None]:
    yamlfile = tmp_path / "config.yaml"
    with open(yamlfile, "w") as file:
        yaml.dump(config_dict, file)
    yield str(yamlfile)


def test_yaml_load(config_dict: dict, yaml_config: str) -> None:
    config_obj = Config(yaml=yaml_config)

    config_obj._load_yaml()

    assert_config_correct(config_dict, config_obj)


def test_yaml_load_file_not_found(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    yamlfile = str(tmp_path / "config.yaml")
    config_obj = Config(yaml=yamlfile)

    with pytest.raises(SystemExit):
        config_obj._load_yaml()

    assert caplog.record_tuples == [
        ("modules.config", logging.CRITICAL, "YAML file not found: %s" % yamlfile)
    ]


def test_yaml_load_invalid_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    yamlfile = str(tmp_path / "config.yaml")
    with open(yamlfile, "w") as file:
        file.write("value: !invalid\n")
    config_obj = Config(yaml=yamlfile)

    with pytest.raises(SystemExit):
        config_obj._load_yaml()

    assert caplog.record_tuples == [
        ("modules.config", logging.CRITICAL, "Error parsing YAML file")
    ]


def test_cli_yaml_and_args(
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with caplog.at_level(logging.ERROR):
        with monkeypatch.context() as m:
            m.setattr(
                sys,
                "argv",
                ["pytest", "--yaml", "./filename", "--library-id", "ad9234lkjx"],
            )
            with pytest.raises(SystemExit):
                Config.from_args()

        # Verify the correct error message was logged
        assert caplog.record_tuples == [
            (
                "modules.config",
                logging.ERROR,
                "When using --yaml, no other arguments should be provided.",
            )
        ]

        # Verify that help text was printed to stderr
        output = capsys.readouterr()
        assert "usage:" in output.err


def assert_config_correct(config_dict: dict, config_obj: Config) -> None:
    for key, item in config_dict.items():
        assert getattr(config_obj, key.replace("-", "_")) == item


@pytest.mark.parametrize(
    "config_kwargs, expect_exit, expected_logs",
    [
        # Valid configuration: All required fields are present → no exit and no log entries.
        (
            {
                "abs_api_token": "test_token",
                "books_json_path": "/path/to/books.json",
                "destination_book_directory": "/path/to/dest",
                "library_id": "library123",
                "server_url": "http://example.com",
                "source_audio_book_directory": "/path/to/source",
            },
            False,
            [],
        ),
        # Invalid configuration: Missing all required fields → SystemExit and multiple critical log messages.
        (
            {},
            True,
            [
                (
                    "modules.config",
                    logging.CRITICAL,
                    "API token not specified in YAML or command line",
                ),
                (
                    "modules.config",
                    logging.CRITICAL,
                    "Books JSON file not specified in YAML or command line",
                ),
                (
                    "modules.config",
                    logging.CRITICAL,
                    "Destination directory not specified in YAML or command line",
                ),
                (
                    "modules.config",
                    logging.CRITICAL,
                    "Library ID not specified in YAML or command line",
                ),
                (
                    "modules.config",
                    logging.CRITICAL,
                    "Server URL not specified in YAML or command line",
                ),
                (
                    "modules.config",
                    logging.CRITICAL,
                    "Source directory not specified in YAML or command line",
                ),
            ],
        ),
    ],
)
def test_validate_param(config_kwargs, expect_exit, expected_logs, caplog):
    with caplog.at_level(logging.CRITICAL):
        if expect_exit:
            with pytest.raises(SystemExit):
                Config(**config_kwargs)._validate()
        else:
            Config(**config_kwargs)._validate()

    if expect_exit:
        # For invalid configurations, verify all expected log records are present.
        assert caplog.record_tuples == expected_logs
    else:
        # For valid configurations, ensure no critical logs were generated.
        assert caplog.record_tuples == []


@pytest.mark.parametrize(
    "cli_args,expected_attr",
    [
        (["--abs-api-token", "123"], {"abs_api_token": "123"}),
        (["--purchased-how-long-ago", "3"], {"purchased_how_long_ago": 3}),
        (["--audio-file-extension", ".mp3"], {"audio_file_extension": ".mp3"}),
        (["--libation-folder-cleanup", "True"], {"libation_folder_cleanup": True}),
    ],
)
def test_cli_argument_parsing(cli_args, expected_attr):
    """Test individual CLI arguments set attributes correctly"""
    # Add exit_on_error=False as first argument to isolate CLI args
    config = Config.from_args(False, *cli_args)
    for key, value in expected_attr.items():
        assert getattr(config, key) == value


@pytest.mark.parametrize(
    "args",
    [
        ["--yaml", "config.yaml", "--abs-api-token", "123"],
        ["--yaml", "config.yaml", "--days=5"],
    ],
)
def test_yaml_exclusivity(args):
    """Test YAML mode prevents other arguments"""
    with pytest.raises(SystemExit):
        Config.from_args(False, *args)


@pytest.mark.parametrize(
    "yaml_content,expected_attrs",
    [
        (
            {
                "abs_api_token": "yaml_token",
                "books_json_path": "/yaml/books.json",
                "purchased_how_long_ago": 10,
                "audio_file_extension": ".ogg",
                "dest_dir": "/yaml/dest",
                "library_id": "yaml_123",
                "server_url": "http://yaml",
                "source_dir": "/yaml/src",
            },
            {"purchased_how_long_ago": 10, "audio_file_extension": ".ogg"},
        ),
    ],
)
def test_from_args(yaml_content, expected_attrs):
    """Test YAML configuration properly overrides defaults"""
    with tempfile.NamedTemporaryFile(mode="w") as yaml_file:
        yaml.dump(yaml_content, yaml_file)
        yaml_file.seek(0)
        config = Config.from_args(False, "--yaml", yaml_file.name)

    for attr, value in expected_attrs.items():
        assert getattr(config, attr) == value


@pytest.mark.parametrize(
    "arguments, expected_yaml",
    [
        (
            [
                "--abs-api-token",
                "zzzz",
                "--books-json-path",
                "books.json",
                "--purchased-how-long-ago",
                "0",
                "--destination-book-directory",
                "/tmp/ABS/books",
                "--download-program",
                "OpenAudible",
                "--audio-file-extension",
                ".m4b",
                "--libation-folder-cleanup",
                False,
                "--library-id",
                "123456",
                "--log-file-path",
                "/tmp/book_processing.txt",
                "--server-url",
                "http://example.com",
                "--source-audio-book-directory",
                "/tmp/OpenAudible/books",
            ],
            {
                "abs_api_token": "zzzz",
                "books_json_path": "books.json",
                "purchased_how_long_ago": 0,
                "destination_book_directory": "/tmp/ABS/books",
                "download_program": "OpenAudible",
                "audio_file_extension": ".m4b",
                "libation_folder_cleanup": False,
                "library_id": "123456",
                "log_file_path": "/tmp/book_processing.txt",
                "server_url": "http://example.com",
                "source_audio_book_directory": "/tmp/OpenAudible/books",
            },
        ),
    ],
)
def test_generate_yaml_from_parser(arguments, expected_yaml, tmp_path: Path) -> None:
    config_obj = Config.from_args(False, *arguments)
    tmp_file = tmp_path / "config.yaml"
    config_obj.generate_yaml_from_parser(tmp_file)

    with open(tmp_file, "r") as generated_file:
        generated_yaml = yaml.safe_load(generated_file)

    assert generated_yaml == expected_yaml
