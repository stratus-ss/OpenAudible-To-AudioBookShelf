import argparse
import logging
import sys
import typing as t
from pathlib import Path

import yaml

LOGGER = logging.getLogger(__name__)
_IS_TEST: bool = False


def _get_parser() -> argparse.ArgumentParser:
    """Create ArguementParser object and add arguements to it.
    This is separated into its own function to increase readability.

    Returns:
        argparse.ArgumentParser: ArgumentParser object configured for all cli options.
    """
    parser = argparse.ArgumentParser(description="Process and organize audio books.")
    parser.add_argument(
        "--abs-api-token",
        dest="abs_api_token",
        type=str,
        default="",
        help="The API token for authentication",
    )

    parser.add_argument(
        "--books-json-path",
        dest="books_json_path",
        type=str,
        default="$HOME/OpenAudible/books.json",
        help="Path to the JSON file containing book information",
    )

    parser.add_argument(
        "--purchased-how-long-ago",
        dest="purchased_how_long_ago",
        type=int,
        default=7,
        help="Process books purchased within this many days (default: 7)",
    )

    parser.add_argument(
        "--destination-book-directory",
        dest="destination_book_directory",
        type=str,
        default="",
        help="Destination directory for organized audio books",
    )

    parser.add_argument(
        "--download-program",
        dest="download_program",
        type=str,
        default="OpenAudible",
        help="Specify the download project (OpenAudible or Libation)",
    )

    parser.add_argument(
        "--audio-file-extension",
        dest="audio_file_extension",
        type=str,
        default=".m4b",
        help="Audio file extension (default: .m4b)",
    )

    parser.add_argument(
        "--generate-yaml",
        dest="generate_yaml",
        # type=bool,
        default=False,
        action="store_true",
        help="Generates a yaml file instead of running the command",
    )

    parser.add_argument(
        "--copy-instead-of-move",
        dest="copy_instead_of_move",
        default=False,
        action="store_true",
        help="Copy files instead of moving them (useful for debugging/testing)",
    )

    parser.add_argument(
        "--libation-folder-cleanup",
        dest="libation_folder_cleanup",
        type=bool,
        default=False,
        help="Delete the source folder in Libation Directory",
    )

    parser.add_argument(
        "--libation-file-locations-path",
        dest="libation_file_locations_path",
        type=str,
        default="",
        help="Path to Libation's FileLocationsV2.json file (optional, uses constructed paths if not provided)",
    )

    parser.add_argument(
        "--library-id",
        dest="library_id",
        type=str,
        default="",
        help="The library ID in AudioBookShelf",
    )

    parser.add_argument(
        "--log-file-path",
        dest="log_file_path",
        type=str,
        default="/tmp/book_processing.txt",
        help="Path to the log file",
    )

    parser.add_argument(
        "--server-url",
        dest="server_url",
        type=str,
        default="",
        help="The base URL of the AudioBookShelf server",
    )

    parser.add_argument(
        "--source-audio-book-directory",
        dest="source_audio_book_directory",
        type=str,
        default="",
        help="Source directory containing audio book files",
    )

    parser.add_argument("--yaml", type=str, help="Path to YAML configuration file")

    return parser


def _parse_fail(msg: str) -> None:
    LOGGER.error(msg)
    _get_parser().print_help(sys.stderr)
    exit(1)


class Config:
    def __init__(self: t.Self, **kwargs: t.Any) -> None:
        for name in kwargs:
            setattr(self, name, kwargs[name])

    def __contains__(self: t.Self, key: str) -> bool:
        return key in self.__dict__

    @classmethod
    def from_args(cls: type[t.Self], exit_on_error: bool = True, *args: str) -> t.Self:
        """Create Config object from passed arguements or sys.argv

        Args:
            *args (str): strings to be interpreted as command line arguements.

        Returns:
            Config: Config object with all parsed arguements as attributes
        """
        parser = _get_parser()
        config = cls()
        if not args:
            args = None

        parser.parse_args(args=args, namespace=config)

        # Check if --yaml is used and exit if other arguments are provided
        if config.yaml:
            # Get the list of arguments passed to the parser
            parser_args = sys.argv[1:] if args is None else args

            # If more than one argument is passed (yaml and the yaml file path), it's an error
            if len(parser_args) > 2:
                _parse_fail("When using --yaml, no other arguments should be provided.")
            config._load_yaml()

        config._validate(exit_on_error=exit_on_error)
        return config

    def _load_yaml(self: t.Self) -> None:
        """Read yaml file and add arguements as attributes to Config object.
        Does not override non-default attributes set elsewhere.
        """
        try:
            with open(self.yaml, "r") as yaml_file:
                config_dict = yaml.safe_load(yaml_file)
                # Convert dash-separated keys to underscore-separated keys
                for key, value in config_dict.items():
                    new_key = key.replace("-", "_")
                    new_value = Path(value) if new_key == "file" else value
                    setattr(self, new_key, new_value)
            delattr(self, "yaml")

        except FileNotFoundError:
            LOGGER.critical("YAML file not found: %s", self.yaml)
            exit(1)
        except yaml.YAMLError as e:
            LOGGER.critical("Error parsing YAML file", exc_info=e)
            exit(1)

    def load_from_env(self: t.Self) -> None:
        # Implement loading configuration from environment variables
        pass

    def _validate(self: t.Self, exit_on_error=True) -> None:
        """
        Ensure that necessary options are available for the parser to function.
        The required options are:
        - abs_api_token
        - books_json_path (will be auto-generated for Libation if not provided)
        - destination_book_directory
        - library_id
        - server_url
        - source_audio_book_directory

        If any required option is missing or empty, a critical log message is issued
        and the program exits.
        """

        required = {
            "abs_api_token": "API token not specified in YAML or command line",
            "destination_book_directory": "Destination directory not specified in YAML or command line",
            "library_id": "Library ID not specified in YAML or command line",
            "server_url": "Server URL not specified in YAML or command line",
            "source_audio_book_directory": "Source directory not specified in YAML or command line",
        }
        
        # books_json_path is only required for non-Libation or if file doesn't exist
        # For Libation, it will be auto-generated if missing
        missing_errors = []
        for attr, error_msg in required.items():
            # Use getattr with a default of None in case the attribute is missing
            value = getattr(self, attr, None)
            # Check that the attribute exists and is not an empty string (if applicable)
            if value is None or (isinstance(value, str) and value.strip() == ""):
                missing_errors.append(error_msg)
        
        # Special handling for books_json_path
        books_json = getattr(self, "books_json_path", None)
        download_program = getattr(self, "download_program", "OpenAudible")
        
        # Only require books_json_path for OpenAudible or when explicitly set for Libation
        if download_program != "Libation":
            if books_json is None or (isinstance(books_json, str) and books_json.strip() == ""):
                missing_errors.append("Books JSON file not specified in YAML or command line")
        
        if missing_errors:
            for error_msg in missing_errors:
                LOGGER.critical(error_msg)

            if exit_on_error:
                exit(1)

    def generate_yaml_from_parser(self: t.Self, file_path: str | None = None) -> None:
        """
        Generate a YAML file containing all arguments from the given ArgumentParser.

        This method creates a YAML file named "arguments.yaml" in the current directory,
        containing all the arguments defined in the parser, excluding the --generate-yaml option,
        in a format compatible with _load_yaml.
        """
        if file_path is None:
            file_path = "parser_arguments.yaml"
        config_data_attributes = {}
        for attr in _get_parser().parse_args(args=()).__dict__.keys():
            if attr == "file":
                config_data_attributes[attr] = str(getattr(self, attr))
            elif attr in ["yaml", "generate_yaml"]:
                continue
            else:
                config_data_attributes[attr] = getattr(self, attr)

        # Write to YAML file
        with open(file_path, "w") as f:
            yaml.dump(config_data_attributes, f, indent=2, sort_keys=False)
