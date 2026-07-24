# Pipeline Specification

## Purpose
The pipeline capability is the core library (`src/openaudible_to_audiobookshelf/`) that moves Audible audiobooks from a Libation source directory to an AudioBookShelf (ABS) destination, organized as `Author/Series/Title/*.m4b`. It exposes the CLI step dispatch (`scan → download → export → organize → scan-abs → match`) and the underlying file-organization, config-construction, date-parsing, filename-sanitization, and logging primitives that the MCP layer wraps.

## Requirements

### Requirement: `sanitize_filename` produces filesystem-safe names
The system SHALL replace ampersands with "and", strip commas, replace spaces with underscores, and keep only alphanumeric characters, underscores, and periods in any sanitized filename.

#### Scenario: Aggressive sanitization on a book title
- GIVEN a book title `"Test Author & Co., Volume 1"`
- WHEN `sanitize_filename(title)` is called
- THEN the result is `"Test_Author_and_Co_Volume_1"`
- AND the result contains no commas, no spaces, and no ampersands

#### Scenario: Periods and underscores are preserved
- GIVEN a title `"Book.Title_With_Underscores"`
- WHEN `sanitize_filename(title)` is called
- THEN the result is `"Book.Title_With_Underscores"` (unchanged)

### Requirement: `Config.from_dict` constructs a validated Config from a dict
The system SHALL build a `Config` from a dict, normalize dash-separated keys to underscores, set only valid Config attributes, and call `_validate()` unconditionally before returning.

#### Scenario: Valid dict produces a fully-set Config
- GIVEN a dict containing all required keys (`abs_api_token`, `destination_book_directory`, `library_id`, `server_url`, `source_audio_book_directory`)
- WHEN `Config.from_dict(data)` is called
- THEN a `Config` instance is returned with all five fields populated
- AND `_validate()` has been called (no further validation needed by the caller)

#### Scenario: Missing required fields raise `ConfigError`
- GIVEN an empty dict `{}`
- WHEN `Config.from_dict({})` is called
- THEN a `ConfigError` (subclass of `ValueError`) is raised
- AND the error message references at least one missing required field

#### Scenario: Dash-separated keys are normalized
- GIVEN a dict with keys like `"abs-api-token"`, `"destination-book-directory"`
- WHEN `Config.from_dict(data)` is called
- THEN those keys are treated as `"abs_api_token"`, `"destination_book_directory"`
- AND the resulting Config has the corresponding attributes set

### Requirement: `_parse_date` returns YYYY-MM-DD from ISO 8601 input
The system SHALL parse any ISO 8601 date string (UTC `Z` suffix, explicit `+/-` timezone offsets, fractional seconds, or date-only) and return a `YYYY-MM-DD` formatted string.

#### Scenario: UTC `Z` suffix
- GIVEN the input `"2026-07-22T00:00:00Z"`
- WHEN `_parse_date(input)` is called
- THEN the result is `"2026-07-22"`

#### Scenario: Timezone offset with fractional seconds
- GIVEN the input `"2026-07-22T12:30:45.123456+05:30"`
- WHEN `_parse_date(input)` is called
- THEN the result is `"2026-07-22"`

#### Scenario: Date-only input
- GIVEN the input `"2026-07-22"`
- WHEN `_parse_date(input)` is called
- THEN the result is `"2026-07-22"`

### Requirement: Pipeline step dispatch returns structured results
The system SHALL dispatch pipeline steps by name and return a result dict with at least `step` and `success` keys.

#### Scenario: Known step returns a structured result
- GIVEN a valid step name (e.g. `"scan-abs"`) and a constructed `Config`
- WHEN `run_step(step_name, config)` is called
- THEN the returned dict contains keys `"step"` (the step name) and `"success"` (boolean)
- AND no exception is raised for the dispatch itself

### Requirement: ABSClient retry behavior
The system SHALL retry transient HTTP errors with exponential backoff.

#### Scenario: Retry on connection error
- GIVEN an ABSClient configured with max_retries=3 and backoff_factor=1.0
- WHEN a request encounters a ConnectionError
- THEN the client retries up to 3 times
- AND delays between attempts are 0s, 1s, and 2s (exponential backoff)
- AND raises ABSConnectionError after all retries exhausted

#### Scenario: No retry on client errors
- GIVEN an ABSClient
- WHEN a request receives HTTP 404 Not Found
- THEN the client does NOT retry
- AND raises ABSApiError immediately

#### Scenario: Retry on server errors
- GIVEN an ABSClient configured with max_retries=3
- WHEN a request receives HTTP 503 Service Unavailable
- THEN the client retries up to 3 times
- AND raises ABSApiError after all retries exhausted

#### Scenario: Config fields feed client
- GIVEN a Config with abs_http_timeout=60, abs_http_max_retries=5, abs_http_backoff_factor=2.0
- WHEN an ABSClient is constructed from that Config
- THEN the client uses timeout=60, max_retries=5, backoff_factor=2.0

### Requirement: Pipeline step error boundaries
The system SHALL return structured error dicts when pipeline steps encounter failures.

#### Scenario: step_scan_abs handles connection failure
- GIVEN a Config pointing to an unreachable ABS server
- WHEN step_scan_abs is called
- THEN it returns {"step": "scan-abs", "success": false, "error": "..."}
- AND does NOT raise an exception

#### Scenario: step_match handles HTTP error
- GIVEN a Config with an invalid ABS API token
- WHEN step_match is called
- THEN it returns {"step": "match", "success": false, "error": "..."}
- AND does NOT raise an exception

#### Scenario: step_organize handles unexpected error
- GIVEN a Config with valid fields but an I/O error during file transfer
- WHEN step_organize is called
- THEN it returns {"step": "organize", "success": false, "error": "..."}
- AND does NOT raise an exception
# pipeline delta — profanity-cleaning-mcp-friendly

## MODIFIED Requirements

### Requirement: Pipeline step error boundaries

#### Scenario: step_organize handles cleaning failure with structured error
- GIVEN profanity cleaning fails for one book in a batch
- WHEN `step_organize` is called
- THEN it returns `{"step": "organize", "success": true}`
- AND the result includes a `cleaning_failures` list containing the failed book's
  `{asin, title, error}` details
- AND the failed book is absent from `moved`
- AND the batch continues to process remaining books
- AND the result includes a `cleaning` aggregate with `total_cleaned`,
  `total_failed`, `total_profanities` integer counters
