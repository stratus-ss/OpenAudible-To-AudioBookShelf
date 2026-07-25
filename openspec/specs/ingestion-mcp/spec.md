# Ingestion-MCP Specification

## Purpose
The ingestion-MCP capability is the FastMCP server (`abs_mcp/mcp_server.py`, `abs_mcp/podcast_tools.py`) that exposes the audiobook and podcast ingestion pipeline as Model Context Protocol tools. It resolves library registry entries, builds a `Config` from per-call parameters, calls the same pipeline steps as the CLI, and provides ABS REST client helpers for library introspection, item search, and podcast episode management.

## Requirements

### Requirement: `_abs_headers` builds a Bearer-token Authorization header
The system SHALL return a dict containing a single `Authorization` key with the value `"Bearer <token>"` for any token string.

#### Scenario: Token produces a Bearer header
- GIVEN a token string `"abc123"`
- WHEN `_abs_headers(token)` is called
- THEN the result is `{"Authorization": "Bearer abc123"}`

### Requirement: `_build_config` constructs a validated Config via `Config.from_dict`
The system SHALL assemble a dict from MCP tool parameters, library registry values, and environment fallbacks, then return a fully validated `Config` (via `Config.from_dict()`).

#### Scenario: Library registry values populate required fields
- GIVEN a library named `"adult"` registered in `libraries.yaml` with a `library_id`, `abs_server_url`, and `abs_api_token`
- WHEN `_build_config(library="adult")` is called
- THEN the returned Config has `library_id`, `server_url`, and `abs_api_token` populated from the registry
- AND `_validate()` has succeeded (no `ConfigError` raised)

#### Scenario: Per-call overrides replace registry values
- GIVEN a library named `"adult"` registered with `library_id="lib_default"`
- WHEN `_build_config(library="adult", abs_library_id="lib_override")` is called
- THEN the returned Config has `library_id == "lib_override"`

#### Scenario: Validation failures raise `ConfigError` (which is a `ValueError`)
- GIVEN a library whose registry entry is missing `abs_api_token`
- WHEN `_build_config(library="adult")` is called
- THEN `ConfigError` is raised
- AND existing `except ValueError:` handlers in MCP tools catch it

### Requirement: `list_libraries` enumerates configured libraries
The system SHALL return one entry per library in `libraries.yaml` with at least `name`, `library_id`, and `media_type` fields.

#### Scenario: Two-entry libraries.yaml yields two results
- GIVEN `libraries.yaml` containing two entries (e.g. `"adult"` and `"kids"`)
- WHEN `list_libraries()` is called
- THEN two entries are returned
- AND each entry has `name`, `library_id`, and `media_type` populated

### Requirement: Library resolution
The system SHALL map a friendly library name to its full registry entry.

#### Scenario: Resolve named library to full config
- GIVEN libraries.yaml contains a `"kids"` entry with library_id, destination_dir, abs_server_url
- WHEN `_resolve_library("kids")` is called
- THEN it returns a dict with keys: name, library_id, destination_dir, media_type, folder_id, abs_server_url, abs_api_token
- AND library_id matches the YAML entry

#### Scenario: Resolve default library when name is None
- GIVEN libraries.yaml defines default_library: `"adult"`
- WHEN `_resolve_library(None)` is called
- THEN it returns config for the `"adult"` library

#### Scenario: Env var fallback when registry has gaps
- GIVEN a library entry missing abs_api_token
- AND `ABS_API_TOKEN` env var is set
- WHEN `_resolve_library` resolves that library
- THEN abs_api_token falls back to the env var value

#### Scenario: Unknown library name raises error
- GIVEN libraries.yaml defines `"adult"` and `"kids"` only
- WHEN `_resolve_library("nonexistent")` is called
- THEN a ValueError is raised
- AND the error message includes the unknown library name

### Requirement: Tool return type
All MCP tool functions SHALL return native Python dicts (not JSON-encoded strings).

#### Scenario: scan_audible returns dict
- GIVEN a working Libation CLI
- WHEN `scan_audible()` is called
- THEN the return value is a dict
- AND the dict has keys: `step`, `success`

#### Scenario: organize_books returns dict with cleaning stats
- GIVEN a valid library and source directory with audio files
- WHEN `organize_books(purchased_how_long_ago=0)` is called
- THEN the return value is a dict with keys: `step`, `success`, `processed_count`
- AND if profanity cleaning is enabled, cleaning stats appear as nested dict keys

#### Scenario: download_books returns dict with ASIN list
- GIVEN valid Libation CLI and authentication
- WHEN `download_books(asins=["B0001"])` is called
- THEN the return value is a dict
- AND the dict has keys: `step`, `success`, `asins` (list of ASINs downloaded)

#### Scenario: get_cleaning_progress returns dict
- GIVEN no cleaning operation is active
- WHEN `get_cleaning_progress()` is called
- THEN it returns a dict
- AND the dict contains aggregate stats (total_cleaned, total_failed, total_profanities)
- AND the dict contains a per-ASIN status map

#### Scenario: get_tool_metrics returns dict
- GIVEN tool metrics history has at least one record
- WHEN `get_tool_metrics(limit=10)` is called
- THEN the return value is a dict with keys: `limit`, `records`, `summary`

### Requirement: ASIN file handoff
The system SHALL write downloaded ASINs to a deterministic file path so subsequent organize calls can pick them up across process restarts.

#### Scenario: download_books writes last_download.json
- GIVEN a valid source directory with write permissions
- WHEN `download_books(asins=["B0001", "B0002"])` completes
- THEN `<source_dir>/last_download.json` exists
- AND it contains a JSON object with `timestamp` (ISO8601) and `asins` keys

#### Scenario: organize_books reads last_download.json when no explicit ASINs
- GIVEN `<source_dir>/last_download.json` exists with `asins: ["B0001"]`
- AND the caller did not pass explicit ASINs to organize_books
- WHEN `organize_books()` is called
- THEN it processes only book B0001

#### Scenario: organize_books ignores last_download.json when explicit ASINs provided
- GIVEN `<source_dir>/last_download.json` exists with `asins: ["B0001"]`
- AND the caller passed `asins=["B0002"]` to organize_books
- WHEN `organize_books()` is called
- THEN it processes only book B0002

#### Scenario: organize_books falls back to disk scan when no file and no explicit ASINs
- GIVEN `<source_dir>/last_download.json` does not exist
- AND the caller did not pass explicit ASINs
- WHEN `organize_books()` is called
- THEN it scans the source directory for audiobook files
- AND processes all detected books

#### Scenario: Missing last_download.json returns empty list
- GIVEN `<source_dir>/last_download.json` does not exist
- WHEN `_read_downloaded_asins(source_dir)` is called
- THEN it returns `[]`
- AND no exception is raised

#### Scenario: Corrupted last_download.json returns empty list
- GIVEN `<source_dir>/last_download.json` contains invalid JSON
- WHEN `_read_downloaded_asins(source_dir)` is called
- THEN it returns `[]`
- AND a warning is logged

#### Scenario: File-based handoff survives process restart
- GIVEN `download_books` wrote `<source_dir>/last_download.json`
- WHEN the MCP server process restarts
- AND `organize_books()` is called
- THEN the ASINs are read from the file (not lost with the in-memory state)

### Requirement: Error propagation
The system SHALL surface core library errors as structured dicts with `success: false`.

#### Scenario: scan_audiobookshelf returns error dict on connection failure
- GIVEN an unreachable ABS server URL
- WHEN `scan_audiobookshelf(abs_server_url="http://127.0.0.1:1")` is called
- THEN the return value has `success: false`
- AND the return value has an `error` key describing the failure

#### Scenario: organize_books returns error dict on missing source directory
- GIVEN `source_dir` set to a non-existent path
- WHEN `organize_books()` is called
- THEN the return value has `success: false`
- AND the return value has an `error` key

#### Scenario: match_audiobookshelf returns error dict on bad auth token
- GIVEN an invalid `abs_api_token`
- WHEN `match_audiobookshelf(abs_api_token="invalid")` is called
- THEN the return value has `success: false`
- AND the return value has an `error` key

#### Scenario: list_abs_library returns error dict on cache refresh failure
- GIVEN an unreachable ABS server
- AND `refresh=True`
- WHEN `list_abs_library(abs_server_url="http://127.0.0.1:1", refresh=True)` is called
- THEN the return value has `error: "cache_refresh_failed"` key
- AND a `detail` key with the underlying error

### Requirement: Tool ordering invariants
The system SHALL tolerate missing prerequisites without raising exceptions, returning structured results instead.

#### Scenario: match succeeds even with no prior scan
- GIVEN `organize_books` ran successfully
- AND `scan_audiobookshelf` was skipped
- WHEN `match_audiobookshelf()` is called
- THEN it returns successfully (success: true)
- AND matches 0 items if no scan completed

#### Scenario: organize with no new books returns success with zero count
- GIVEN the source directory has no audiobook files
- WHEN `organize_books()` is called
- THEN the return value has `success: true`
- AND `processed_count: 0`

### Requirement: Cache behavior
The system SHALL cache ABS library data and refresh on demand or staleness.

#### Scenario: First list_abs_library call fetches from ABS
- GIVEN an empty or expired cache
- WHEN `list_abs_library()` is called
- THEN it fetches data from the ABS API
- AND populates the cache file

#### Scenario: Subsequent call within max_age returns cached data
- GIVEN a fresh cache (less than `cache_max_age_seconds` old)
- AND `refresh=False`
- WHEN `list_abs_library()` is called
- THEN it returns cached data
- AND no ABS API call is made

#### Scenario: Forced refresh bypasses cache
- GIVEN a fresh cache exists
- WHEN `list_abs_library(refresh=True)` is called
- THEN it fetches fresh data from the ABS API
- AND updates the cache file

#### Scenario: Stale cache auto-refreshes
- GIVEN a cache older than `cache_max_age_seconds`
- WHEN `list_abs_library()` is called
- THEN it fetches fresh data from the ABS API

#### Scenario: list_abs_library returns items with required fields
- GIVEN an ABS library with items
- WHEN `list_abs_library()` is called
- THEN each item has fields: id, title, author, media_type
- AND the response includes `total_in_library`, `total_matches`, `items`

#### Scenario: list_abs_library pagination via offset/limit
- GIVEN an ABS library with 50 items
- WHEN `list_abs_library(limit=10, offset=20)` is called
- THEN the response includes at most 10 items
- AND `next_offset` indicates the next page (30 if more items exist)

### Requirement: Progress tracking
The system SHALL expose per-book profanity cleaning progress through a queryable interface.

#### Scenario: get_cleaning_progress returns empty when no operation
- GIVEN no cleaning operation is running
- WHEN `get_cleaning_progress()` is called
- THEN it returns a dict with no per-ASIN entries
- AND aggregate stats show zero values

#### Scenario: get_cleaning_progress returns per-ASIN status mid-operation
- GIVEN a cleaning operation is in progress for 3 books
- WHEN `get_cleaning_progress()` is called
- THEN it returns a per-ASIN status map
- AND each status is one of: processing, transcribing, done, failed, skipped
- AND aggregate stats include `total_cleaned`, `total_failed`, `total_profanities`

### Requirement: Podcast tools
The system SHALL expose podcast search, subscription, episode retrieval, and download.

#### Scenario: search_podcasts returns results from iTunes
- GIVEN a search term like `"Under The Hood"`
- WHEN `search_podcasts(term="Under The Hood")` is called
- THEN it returns a list of podcast results
- AND each result has `title` and `feed_url` fields

#### Scenario: add_podcast subscribes to an RSS feed
- GIVEN a valid RSS feed URL
- AND a podcast library with folder_id
- WHEN `add_podcast(feed_url="...", library="adult_podcasts")` is called
- THEN it returns a dict with the new podcast's library item ID
- AND ABS subscribes the library to the feed

#### Scenario: list_podcasts returns all podcasts in a library
- GIVEN a podcast library with 2 subscriptions
- WHEN `list_podcasts(library="adult_podcasts")` is called
- THEN it returns a list with 2 entries
- AND each entry has `id`, `title`, and `mediaType: "podcast"` fields

#### Scenario: get_podcast_episodes returns episodes for a podcast
- GIVEN a podcast library item ID
- WHEN `get_podcast_episodes(podcast_id="...")` is called
- THEN it returns a list of episodes
- AND each episode has `title`, `publishedAt`, and `downloadUrl` fields

#### Scenario: download_podcast_episodes triggers ABS to fetch new episodes
- GIVEN a podcast library item ID
- WHEN `download_podcast_episodes(podcast_id="...", limit=3)` is called
- THEN ABS downloads up to 3 new episodes
- AND returns a list of the downloaded episodes

#### Scenario: fetch_podcast_feed parses RSS from Apple URL
- GIVEN an Apple Podcasts URL like `https://podcasts.apple.com/.../id410937196`
- WHEN `fetch_podcast_feed(apple_url="...")` is called
- THEN it returns `podcast_title`
- AND a list of episodes with `download_url` fields

#### Scenario: fetch_podcast_feed parses direct RSS feed URL
- GIVEN a direct RSS feed URL
- WHEN `fetch_podcast_feed(feed_url="...")` is called
- THEN it returns the parsed feed
- AND includes episode metadata

#### Scenario: download_podcast_files saves audio files to ABS directory
- GIVEN a list of audio file download URLs
- AND a podcast name and library
- WHEN `download_podcast_files(urls=[...], podcast_name="My Podcast", library="adult_podcasts")` is called
- THEN files are saved under the podcast name in the ABS podcast directory
- AND a library scan is triggered

### Requirement: Discovery tools
The system SHALL expose library browsing, searching, and status inspection.

#### Scenario: list_libraries returns dict with libraries map
- GIVEN libraries.yaml with 2 libraries
- WHEN `list_libraries()` is called
- THEN the return value is a dict with `default_library` and `libraries` keys
- AND `libraries` is a dict with at least 2 entries

#### Scenario: list_library filters by author
- GIVEN the Libation library has books by multiple authors
- WHEN `list_library(author="Brandon Sanderson")` is called
- THEN only books by Brandon Sanderson are returned
- AND the count matches the filter

#### Scenario: list_library filters by series
- GIVEN the Libation library has books in multiple series
- WHEN `list_library(series="Mistborn")` is called
- THEN only books in the Mistborn series are returned

#### Scenario: list_library returns dict with pagination
- GIVEN the Libation library has books matching the filter
- WHEN `list_library(limit=10, offset=20)` is called
- THEN at most 10 items are returned
- AND `next_offset` indicates the next page

#### Scenario: search_abs_library returns matching items
- GIVEN an ABS library with items
- WHEN `search_abs_library(query="...")` is called
- THEN matching items are returned
- AND each item has flattened title, author, and series fields

#### Scenario: get_status returns config summary
- GIVEN valid library configuration
- WHEN `get_status()` is called
- THEN the return value has `source_dir`, `destination_dir`, `abs_server` keys
- AND the ABS server health is reported in `abs_status`

#### Scenario: get_source_status returns file counts
- GIVEN a source directory with files
- AND a destination directory with files
- WHEN `get_source_status()` is called
- THEN the return value has `source` and `destination` keys
- AND each contains file counts and extensions

#### Scenario: delete_library_items removes items from ABS
- GIVEN an ABS library with items
- AND valid item IDs
- WHEN `delete_library_items(item_ids=["..."])` is called
- THEN those items are removed from the ABS library
- AND the return value reports the count of deleted items

### Requirement: Metrics tools
The system SHALL expose tool metrics recording and querying.

#### Scenario: query_tool_metrics_history filters by tool name
- GIVEN tool metrics history has records for multiple tools
- WHEN `query_tool_metrics_history(tool_name="list_libraries")` is called
- THEN only records for `list_libraries` are returned
- AND `summary` aggregates stats for that tool

#### Scenario: query_tool_metrics_history filters by time range
- GIVEN tool metrics history has records spanning multiple days
- WHEN `query_tool_metrics_history(since="2026-07-22T00:00:00Z", until="2026-07-23T00:00:00Z")` is called
- THEN only records within the time range are returned

#### Scenario: query_tool_metrics_history respects limit
- GIVEN tool metrics history has more than 50 records
- WHEN `query_tool_metrics_history(limit=10)` is called
- THEN at most 10 records are returned

# ingestion-mcp delta — profanity-cleaning-mcp-friendly

## ADDED Requirements

### Requirement: Profanity cleaning failure reporting
The system SHALL report per-book cleaning failures in the `organize_books` response
(via `get_job_result`) without aborting the batch.

#### Scenario: organize_books returns cleaning_failures on cleaning error
- GIVEN profanity cleaning is enabled
- AND one book's cleaning fails with `AudioCleaningError`
- WHEN `organize_books` completes
- THEN the response (via `get_job_result`) includes a `cleaning_failures` list
- AND the failed book's entry has `asin`, `title`, and `error` fields
- AND the failed book is absent from `moved`
- AND remaining books continue to be processed

#### Scenario: organize_books result includes cleaning aggregate counters
- GIVEN profanity cleaning is enabled
- WHEN `organize_books` completes
- THEN the result dict includes a `cleaning` field
- AND `cleaning` has integer counters `total_cleaned`, `total_failed`, `total_profanities`

#### Scenario: get_cleaning_progress includes per-stage and counter fields
- GIVEN a cleaning operation is in progress
- WHEN `get_cleaning_progress()` is called
- THEN each per-ASIN entry includes a `stage` field
- AND `stage` is one of: `staging`, `uploading`, `transcribing`, `done`, `failed`, `skipped`
- AND the top-level response includes `books_done` and `books_total` integer counters

### Requirement: Async job pattern for long-running tools (DR-6)
The system SHALL use an async job pattern for `organize_books` and `ingest_books`
to enable concurrent progress polling.

#### Scenario: organize_books returns job handle immediately
- GIVEN the MCP server has no active job
- WHEN `organize_books` is called
- THEN it returns `{"job_id": str, "status": "started"}` within 1 second
- AND the actual work runs in the background (via `asyncio.create_task(asyncio.to_thread(...))`)

#### Scenario: ingest_books returns job handle immediately
- GIVEN the MCP server has no active job
- WHEN `ingest_books` is called
- THEN it returns `{"job_id": str, "status": "started"}` within 1 second
- AND the actual work runs in the background

#### Scenario: get_job_result returns completed result
- GIVEN `organize_books` was called and the background job has finished
- WHEN `get_job_result(job_id)` is called with the returned `job_id`
- THEN it returns `{"status": "completed", "job_id": str, "result": dict}`
- AND the `result` dict matches the previous synchronous `organize_books` response schema

#### Scenario: get_job_result returns running status while job is in progress
- GIVEN `organize_books` was called and the background job is still running
- WHEN `get_job_result(job_id)` is called with the returned `job_id`
- THEN it returns `{"status": "running", "job_id": str}`

#### Scenario: concurrent progress polling succeeds during job
- GIVEN `organize_books` was called and the background job is running
- WHEN `get_cleaning_progress()` is called concurrently with the running job
- THEN it returns a dict with current progress
- AND it does not timeout or block
- AND poll latency is below 1 second (verified empirically at 2–5ms)

#### Scenario: single-slot guard rejects concurrent jobs
- GIVEN a background job is already running
- WHEN `organize_books` is called again
- THEN it returns `{"error": str, "active_job_id": str}`
- AND does NOT start a new job
- AND the existing job continues unaffected

#### Scenario: unknown job after server restart
- GIVEN the MCP server was restarted while a job was running
- WHEN `get_job_result(job_id)` is called with the pre-restart `job_id`
- THEN it returns `{"error": "Unknown job", "job_id": str}`
- AND does NOT crash
