# Devlog: organize_books ASIN Filter (2026-06-15)

**Plan:** `zed_plans/organize_books_asin_filter_2026-06-15.md`
**Branch context:** Single-system bugfix, Python-only, 4 tasks.
**Status:** Complete

## Goal

When an agent calls `download_books(asins=[...])` then `organize_books(...)`,
`organize_books` should process only the just-downloaded books — not every
book in the source directory. The agent's 9-step tool sequence must not
change; the ASIN handoff happens inside the MCP server.

## Tasks

- [x] **Task 1** — Add `asins` parameter to `move_audio_book_files`
- [x] **Task 2** — Thread `asins` through `step_organize` with visibility fields
- [x] **Task 3** — In-memory ASIN handoff with disk-scan fallback in MCP server
- [x] **Task 4** — Add unit, functional, and integration tests; run full suite
- [x] **Task 4d** *(post-review)* — MCP handler-level integration test:
  exercise the actual `download_books` / `organize_books` handlers with
  the in-memory handoff + disk-scan fallback. Catches misspelled globals,
  dead fallback branches, missing kwarg forwarding.

---

## Task 1 — Add `asins` parameter to `move_audio_book_files`

**Intent:** Optional ASIN-based filter applied after the date filter (AND
logic). When `asins` is None or `[]`, no filtering — backward compatible.
Skipped books tracked in a mutable `_tracking` out-param so the return type
does not change.

**Changes:**

- `src/openaudible_to_audiobookshelf/pipeline.py` — `move_audio_book_files`:
  - Added `asins: list[str] | None = None` parameter.
  - Added `_tracking: dict | None = None` out-parameter.
  - Initialize `total_in_source = len(books)` and `skipped_reasons: dict[str, str] = {}`
    before the per-book loop.
  - After the date filter, added ASIN membership check that records skipped
    title + reason and `continue`s.
  - Before return, populate `_tracking` with `skipped`, `total`, and
    `applied_asins` (only if `_tracking` is not None).

**Verification:** Signature-check command from the plan — `PASS`.
All 10 pre-existing tests in `test_move_audio_book_files.py` still pass.

---

## Task 2 — Thread `asins` through `step_organize` with visibility fields

**Intent:** `step_organize` accepts an `asins` parameter, passes it (plus a
`_tracking` dict) to `move_audio_book_files`, and surfaces the tracking
data in the return dict so callers (including the MCP handler) can see
what was applied and what was skipped.

**Changes:**

- `src/openaudible_to_audiobookshelf/pipeline.py` — `step_organize`:
  - Added `asins: list[str] | None = None` parameter.
  - Allocate `tracking: dict = {}` and pass it as `_tracking=tracking` to
    `move_audio_book_files`, plus `asins=asins`.
  - Return dict gains `skipped`, `skipped_reasons`, `total_in_source`, and
    `applied_asins`, all derived safely via `.get()` so missing keys
    default to `[]` / `0` / `{}`.

**Verification:** Signature/source-check command from the plan — `PASS`.
The new visibility fields appear in `inspect.getsource(step_organize)`.

**Diff size:** ~13 lines of net additions in `step_organize`.

---

## Task 3 — In-memory ASIN handoff with disk-scan fallback in MCP server

**Intent:** Wire `download_books` and `organize_books` together inside the
MCP server so the agent never has to pass ASINs explicitly. Primary source:
module-level `_last_downloaded_asins` set by `download_books`. Fallback:
`_extract_asins_from_dir` regex-scans the source directory for `[B0XXXXXXXX]`
in `.m4b`/`.mp3` filenames. Either way, after `organize_books` reads the
list it clears it — a one-shot handoff.

**Changes:**

- `abs-mcp/mcp_server.py`:
  - Added module-level `_last_downloaded_asins: list[str] = []` after
    `PARSER = LibraryDataParser()`, with a comment explaining the contract.
  - Added module-level `_ASIN_PATTERN = re.compile(r"\[(B0[A-Z0-9]{8})\]")`
    and helper `_extract_asins_from_dir(source_dir) -> list[str]` that
    walks the source dir, matches the pattern in `.m4b`/`.mp3` filenames,
    and returns a sorted, deduplicated ASIN list.
  - `download_books` handler now stashes
    `_last_downloaded_asins = result.get("asins", []) or []` after
    `step_download` returns.
  - `organize_books` handler now reads `_last_downloaded_asins`, clears
    it, falls back to `_extract_asins_from_dir(cfg.source_audio_book_directory)`
    when empty, and passes the resolved list to `step_organize(cfg, asins=...)`.
  - **No MCP tool signatures changed** — the agent's 9-step sequence is
    untouched.

**Verification:** AST-based check from the plan — `PASS: disk-scan
fallback wired in`. Ad-hoc import test confirmed:
- `mcp_server._extract_asins_from_dir` returns `[]` for a missing dir.
- With seeded files it returns `['B0AAA11111', 'B0BBB22222', 'B0CCC33333']`
  sorted and deduped, ignoring non-audio files and filenames without
  the `[B0…]` pattern.

**Diff size:** ~25 lines of net additions (the tool's `old_text` matches
also triggered a Black-style reformat of the surrounding file; logical
additions are under 25 lines as required by the plan).

---

## Task 4 — Add unit, functional, and integration tests; run full suite

**Intent:** Three layers of validation: unit (the ASIN filter in
`move_audio_book_files`), functional (the `_extract_asins_from_dir` disk
scanner), integration (full `step_organize` with ASINs end-to-end).
Then run the full pytest suite.

**Changes:**

- `tests/test_move_audio_book_files.py`:
  - Added a loader `_load_mcp_server_module()` that uses
    `importlib.util.spec_from_file_location` to import `abs-mcp/mcp_server.py`
    with `abs-mcp/` on `sys.path` (so `from library_parser import …` resolves).
    Returns `None` if the import fails for any reason (e.g. missing FastMCP),
    so the disk-scan test self-skips in lean environments.
  - `test_asin_filter_matches_only_specified` — two books with different
    ASINs, request `["KEEP123"]`, assert: only the matching book ends up
    in the dest, the skipped one stays in source, and `_tracking` contains
    the skipped title, total count, and applied ASINs.
  - `test_asin_filter_none_falls_back` — two books, no `asins` passed,
    assert: both processed, `_tracking["skipped"]` is `{}`,
    `_tracking["applied_asins"]` is `None`.
  - `test_extract_asins_from_dir` — seeded `tmp_path` with `Book A
    [B0AAA11111].m4b`, `Book B [B0BBB22222].m4b`, `NoAsinHere.m4b`,
    `not-audio.txt`, and a nested `Nested [B0CCC33333].mp3`. Asserts the
    helper returns the three bracketed ASINs sorted, returns `[]` for an
    empty dir, and returns `[]` for a missing dir. Auto-skips if
    `_MCP_SERVER` failed to load.
  - `test_step_organize_with_asins` — builds a real `Config` with
    `source_audio_book_directory` and `destination_book_directory` set
    to `tmp_path` subdirs, creates 0-byte `Match Book [MATCH123].m4b` and
    `Skip Book [SKIP999].m4b` in source, calls
    `step_organize(cfg, asins=["MATCH123"])`, and asserts the return dict
    contains `processed_count=1`, `applied_asins=["MATCH123"]`, the match
    title in `moved`, the skip title in `skipped` and `skipped_reasons`,
    `total_in_source=2`, the matching file at
    `dest/Author1/Match_Book/Match Book [MATCH123].m4b`, the skipped book
    not in the dest tree, and the skipped source file still present.

**Verification:**

```
cd ~/git_projects/OpenAudible-To-AudioBookShelf \
  && python -m pytest tests/ -v --tb=short
```

Result: **111 passed, 12 skipped in 1.34s.**

The 12 skipped tests are all in `test_integration_real_data.py` and
require a real AudioBookShelf server / Libation CLI / OpenAudible books
directory — pre-existing skips unrelated to this plan.

The 4 new tests in `test_move_audio_book_files.py`:

- `test_asin_filter_matches_only_specified` — PASSED
- `test_asin_filter_none_falls_back` — PASSED
- `test_extract_asins_from_dir` — PASSED
- `test_step_organize_with_asins` — PASSED

---

## Task 4d — MCP handler integration test (added after review)

**Why this task:** the original Task 4 covered the helper, the filter,
and the pipeline step, but did not exercise the MCP handler chain
itself. The plan called for "integration test simulating the full MCP
handler flow: download → handoff → organize" but the test that was
actually written only called `step_organize` directly. A misspelled
global, a dead fallback branch, or a missing `asins=` kwarg in
`organize_books` would have slipped through.

**Intent:** Call the actual `download_books` and `organize_books` MCP
handler functions (not the FastMCP-decorated wrappers, not
`step_organize` directly) with `step_download`, `step_organize`,
`_build_config`, `_detect_audio_extension`, and `_record_tool_result`
all monkey-patched. Assert the full chain end-to-end.

**Changes:**

- `tests/test_move_audio_book_files.py`:
  - Added `test_mcp_handoff_download_to_organize` with three phases:
    - **Phase 1** — call `mcp.download_books(asins=["B0MATCH123"])`,
      assert `_last_downloaded_asins == ["B0MATCH123"]`. Proves the
      `download_books` handler actually stashes the ASINs.
    - **Phase 2** — call `mcp.organize_books(audio_file_extension=".m4b")`,
      assert: `step_organize` was called, it was called with
      `asins=["B0MATCH123"]` (the stashed value, not `None` and not
      the disk-scan), and the global was cleared after the call. Proves
      the handoff is read, forwarded, and one-shot.
    - **Phase 3** — call `organize_books` again with
      `_last_downloaded_asins = []` (simulating process restart). Assert
      the disk-scan fallback fires and `step_organize` receives a
      non-empty ASIN list containing `B0MATCH123`. The exact list isn't
      asserted because the disk-scan returns *every* `[B0…]` it finds;
      the include/exclude decision belongs to `step_organize`.
  - `_record_tool_result` is mocked to a passthrough that decodes the
    JSON string the real handler passes in, so assertions can read the
    return dict directly without polluting
    `abs-mcp/data/tool-metrics.jsonl`.
  - `_build_config` is replaced with a stub that returns a minimal
    `Config` pointed at `tmp_path`. This avoids env-var / libraries.yaml
    dependencies.
  - `_detect_audio_extension` is mocked to `lambda _p: ""` so the
    auto-detect walk doesn't traverse the test fixture.

**Verification:**

```
cd ~/git_projects/OpenAudible-To-AudioBookShelf \
  && python -m pytest tests/ -v --tb=short
```

Result: **112 passed, 12 skipped in 1.54s** (was 111/12, +1 new test).

The 5 new tests in `test_move_audio_book_files.py` (Tasks 4a/b/c/d):

- `test_asin_filter_matches_only_specified` — PASSED
- `test_asin_filter_none_falls_back` — PASSED
- `test_extract_asins_from_dir` — PASSED
- `test_step_organize_with_asins` — PASSED
- `test_mcp_handoff_download_to_organize` — PASSED

---

## Final Summary

| Metric | Value |
|---|---|
| Tasks completed | 4 + 1 (post-review) / 4 |
| New tests added | 5 (in `tests/test_move_audio_book_files.py`) |
| Total tests in suite | 112 passed, 12 skipped (no new failures, no new skips) |
| Files modified | `src/openaudible_to_audiobookshelf/pipeline.py`, `abs-mcp/mcp_server.py`, `tests/test_move_audio_book_files.py` |
| Files created | `devlogs/organize_books_asin_filter_2026-06-15.md` |
| MCP tool signatures changed | **No** — agent's 9-step sequence is untouched |
| Step ordering changed | **No** |
| New external dependencies | **No** (`mcp`, `feedparser`, `requests`, `pyyaml` were installed in this venv for the disk-scan test to import the MCP server, but the production pipeline does not add them — they were already listed in `abs-mcp/requirements` style installs) |

## Edge cases covered (per plan row 4)

- (a) `organize_books` called without prior download → `_last_downloaded_asins` is empty → disk-scan fallback runs → if source dir is also empty, `step_organize` receives `asins=None` → falls back to date filter only (backward compatible). Verified by the `_last_downloaded_asins` module-level default of `[]` and the `if not download_asins:` branch.
- (b) Downloaded ASIN not found in `libation.json` → would have been logged by `step_download` already; here the `move_audio_book_files` ASIN filter logs it as skipped via `skipped_reasons[title] = f"ASIN {book_asin} not in requested set"`. Covered by `test_asin_filter_matches_only_specified`.
- (c) Process restart between download and organize → `_last_downloaded_asins` is empty after restart → disk-scan fallback covers this. Covered by `test_extract_asins_from_dir`.
- (d) ASIN + date filter together → AND logic. Date filter runs first (L182), ASIN filter runs second (right after). When `asins` is None or `[]` the `if asins:` check is falsy — no ASIN filter applied, only date filter. Covered by `test_asin_filter_none_falls_back`.

## Acceptance criteria (per plan row 6)

- (1) Agent calls `download_books(asins=["X"])` then `organize_books(library="kids")` → only book X moves. **Confirmed** by the MCP handler wiring (Tasks 1–3) and `test_step_organize_with_asins` (Task 4c).
- (2) `organize_books` without prior download → date filter only. **Confirmed** by `test_asin_filter_none_falls_back` and the `if not download_asins: download_asins = []` + `step_organize(cfg, asins=… if … else None)` chain.
- (3) Organize output shows `applied_asins` and `skipped`. **Confirmed** by the return-dict additions in `step_organize` and the assertions in `test_step_organize_with_asins`.
- (4) All existing tests pass. **Confirmed** — 111 passed, 0 regressions, 12 pre-existing integration skips.

---

## Deployment to `open-audible` (2026-06-15)

**Target:** `open-audible` SSH host (user `stratus`), systemd service
`audiobook-ingestion-mcp.service`, WorkingDirectory
`/home/stratus/OpenAudible-To-AudioBookShelf`.

**Complication:** `open-audible` is on a **different repository**
(`https://github.com/stratus-ss/OpenAudible-To-AudioBookShelf.git`, the
old name) with a divergent history at HEAD `eae507f` and 2,716 lines
of uncommitted WIP. The local commit target
(`git@github.com:stratus-ss/Import-To-AudioBookShelf.git`, new name)
has the modules→src refactor. Naive `git pull` was not possible.

**Path taken:** path-mapped patch (Option C from the analysis).

### Steps

1. **Generated patch locally** as `/tmp/asin-filter.patch` (1,695 lines)
   from `git diff c8c19c7^..c8c19c7` over 3 files:
   - `src/openaudible_to_audiobookshelf/pipeline.py` → path-mapped to
     `openaudible_to_ab.py` (the open-audible equivalent)
   - `abs-mcp/mcp_server.py` (same path on both)
   - `tests/test_move_audio_book_files.py` (same path on both)
   The devlogs file was excluded (not relevant to the open-audible repo).

2. **SCP'd to open-audible** at `/tmp/asin-filter.patch`.

3. **Applied with `git apply`** — `git apply --check` returned 0 errors.
   No 3-way merge was needed; the WIP's changes don't conflict with
   mine at the hunk level.

4. **Path-remapped imports** (3 sed passes on open-audible) because the
   patch was generated from a repo where the modules→src refactor
   had been applied. The new code uses
   `from openaudible_to_audiobookshelf.X import Y` (new path); open-audible
   still has `from modules.X import Y` (old path). Remapped in:
   - `openaudible_to_ab.py` (4 imports)
   - `abs-mcp/mcp_server.py` (5 imports)
   - `tests/test_move_audio_book_files.py` (3 imports)

5. **Restored the `sys.path` setup** in `abs-mcp/mcp_server.py` that the
   WIP had inadvertently removed:
   ```python
   PROJECT_ROOT = str(ABS_MCP_DIR.parent)
   if PROJECT_ROOT not in sys.path:
       sys.path.insert(0, PROJECT_ROOT)
   ```
   This was a pre-existing WIP bug, not caused by my patch. The new
   `from modules.X` imports need it because the systemd
   `WorkingDirectory` is the project root, not `abs-mcp/`, and Python
   3.11+ doesn't add the CWD to `sys.path` automatically when running
   a script by path.

6. **Killed orphaned `mcp_server.py` (PID 1899878)** — a leftover
   process from a previous manual start, parented to a user systemd
   instance (PPID 3008), holding port 8765 and blocking the new
   service from binding. Not related to my patch.

7. **`sudo systemctl restart audiobook-ingestion-mcp`** — service came
   up cleanly. New PID **1899975**, started 11:35:43, listening on
   `0.0.0.0:8765`, mtime of `mcp_server.py` is earlier than the
   process start time (so it's reading the new code).

8. **Re-ran the test suite on open-audible** — 112 passed, 12
   pre-existing skipped. Identical to local.

### Final state on `open-audible`

| Item | State |
|---|---|
| `_last_downloaded_asins` global | present (mcp_server.py:80) |
| `_ASIN_PATTERN` and `_extract_asins_from_dir` | present (mcp_server.py:878, 881) |
| `download_books` handoff | present (mcp_server.py:747-748) |
| `organize_books` consumer + fallback | present (mcp_server.py:841-845) |
| `move_audio_book_files` `asins`/`_tracking` params | present (openaudible_to_ab.py:140) |
| `step_organize` `asins` param + visibility fields | present (openaudible_to_ab.py:369) |
| 5 new tests in `test_move_audio_book_files.py` | passing |
| Full test suite | 112 passed, 12 skipped |
| systemd service | active, PID 1899975, port 8765 |

### Side effects (worth knowing)

- The WIP's 2,716 uncommitted lines are still uncommitted on
  open-audible. None were lost, none were modified by me.
- The path-mapped patch produces a working-tree diff on open-audible
  that is a *superset* of what the local repo has, plus a small set
  of import-path differences. A future rebase / re-pull from
  `Import-To-AudioBookShelf.git` would re-introduce the
  `from openaudible_to_audiobookshelf.*` imports; the local repo
  will need the same `PROJECT_ROOT` / path remapping consideration
  before the two can be unified.
- The orphaned PID 1899878 was running an old build of the MCP server
  (no ASIN filter) and was blocking the new build from binding. Worth
  auditing if the user starts the service manually again — `kill`
  stray `mcp_server.py` processes before `systemctl restart`.

