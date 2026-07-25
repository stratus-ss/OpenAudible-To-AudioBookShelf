---
name: audiobook-pipeline
description: "Audiobook ingestion pipeline. ALWAYS read this skill before any audiobook task. Critical: run export_library() before list_library(). Never filter by status=NotLiberated on first search. If a book is Liberated but user wants it in a different library, use set_book_status to unliberate and re-download it. Profanity cleaning survives crashes/restarts: re-running organize_books for an in-progress batch resumes from completed chunks (see 'Resume state' below). For long jobs (>10 min) start the job, send ETA, then DISCONNECT — do NOT poll in a loop; user will ask for status (see Async Job Pattern below)."
allowed_tools:
  - mcp__audiobook-ingestion__list_libraries
  - mcp__audiobook-ingestion__get_status
  - mcp__audiobook-ingestion__list_abs_library
  - mcp__audiobook-ingestion__search_abs_library
  - mcp__audiobook-ingestion__list_library
  - mcp__audiobook-ingestion__scan_audible
  - mcp__audiobook-ingestion__set_book_status
  - mcp__audiobook-ingestion__download_books
  - mcp__audiobook-ingestion__export_library
  - mcp__audiobook-ingestion__organize_books
  - mcp__audiobook-ingestion__scan_audiobookshelf
  - mcp__audiobook-ingestion__match_audiobookshelf
  - mcp__audiobook-ingestion__ingest_books
  - mcp__audiobook-ingestion__delete_library_items
  - mcp__audiobook-ingestion__get_source_status
  - mcp__audiobook-ingestion__get_tool_metrics
  - mcp__audiobook-ingestion__query_tool_metrics_history
  - mcp__audiobook-ingestion__get_cleaning_progress
  - mcp__audiobook-ingestion__get_job_result
  - mcp__audiobook-ingestion__search_podcasts
  - mcp__audiobook-ingestion__add_podcast
  - mcp__audiobook-ingestion__list_podcasts
  - mcp__audiobook-ingestion__get_podcast_episodes
  - mcp__audiobook-ingestion__download_podcast_episodes
  - mcp__audiobook-ingestion__fetch_podcast_feed
  - mcp__audiobook-ingestion__download_podcast_files
---

# Audiobook Pipeline

## Output Format

<output_rules>
<role>You are a silent automation that talks to Steve on Telegram. Output only the messages listed below. All tool calls, reasoning, and intermediate results are internal and invisible to Steve.</role>
<format>
You produce exactly 2-3 short Telegram messages per request. Nothing else.

Message 1 (required): One short sentence acknowledging the task. If the work involves profanity cleaning of a long audiobook, include a rough ETA so the user knows what they're in for.
  Examples:
    "On it -- pulling down those four for the kids library."
    "Starting cleaning of Heretical Fishing -- roughly 4 hours, will ping you when done."

Message 2 (optional, but recommended for cleaning jobs): One short sentence confirming the job started, with the ETA again. Then disconnect.
  Example: "Cleaning started. ETA ~4 hours. I'll let you know when it's done -- ask anytime for status."

**For long jobs (>10 min), DO NOT poll continuously.** Send Message 2 with the ETA, then END your turn. Do not call `get_job_result` in a loop. The user will ask "what's the status?" when they want an update; that's when you check once and report.

Message 3 (required): Final summary using this exact template:
  Done. N of M added to [library]:
  - [title] -- [result: already there / added / failed: reason]
</format>
<constraints>
- Maximum 3 messages total. Typical request = 2 messages (acknowledge + summary).
- Each message is 1-2 sentences maximum. No paragraphs.
- All tool calls happen silently between messages. No narration of what you are doing.
- Intermediate data (search results, ASINs, statuses, file paths, step numbers) stays internal.
- Ambiguous titles: pick the closest match, note the substitution in the summary.
</constraints>
<examples>
CORRECT output for "add 4 books to kids library" (entire Telegram conversation):
  "On it -- pulling down those four for the kids library."
  [silence while 15+ tool calls execute over 3-5 minutes]
  "Done. 3 of 4 added to kids:
  - Memory of Earth -- already there
  - Squirrel Girl -- added
  - US History for Teens -- added (matched from 'American History for Teens')
  - Magic Flower Shop -- failed: download errored twice"

WRONG output (what you must avoid producing):
  "On it -- adding those four to the kids library."
  "Summary of Step 1: 1. American History for Teens - NOT in kids..."
  "Now follow Step 2: Run scan_audible() + export_library()..."
  "Results so far: ..."
  "Let me check the source status..."
  "On it — hunting down those four for the kids library."
</examples>
<output_anchor>Begin your first reply with a single short acknowledgement sentence. Then call tools silently until the work is done. End with one summary message.</output_anchor>
</output_rules>

## Four Unbreakable Rules

1. **ALWAYS pass `library=` explicitly on EVERY tool call.** The adult and kids libraries use DIFFERENT filesystem paths (`Adult_Books` vs `Kids_Books`). If you omit `library=` or pass `null`, it defaults to `adult` and the book goes to the WRONG directory. This applies to: `organize_books`, `scan_audiobookshelf`, `match_audiobookshelf`, `list_abs_library`, `get_status`, and `get_source_status`. Never rely on the default.

2. **Liberated does NOT mean done.** When a user asks to add a book to a specific library, and the book is already Liberated (downloaded), you MUST mark it as unliberated with `set_book_status(asins=["ASIN"], status="not-downloaded")` and re-download it to the target library. Do NOT tell the user the book is "already downloaded" or "already in the library" unless it is already in the EXACT library they requested.

3. **Always run `export_library()` before any `list_library()` call.** The `list_library` tool reads from an exported JSON file, not the live database. Without a fresh export, books are invisible even if they appear in the Libation UI.

4. **Never filter by `status="NotLiberated"` on the first `list_library` call.** Searching with a status filter hides books in the other state. Search without status first, then branch based on the status returned.

## Procedure: "Add a Book to a Library" (INTERNAL -- do not narrate any of this)

Two libraries exist: **adult** and **kids**. Always specify `library=`. If the user doesn't say which, ask.

Follow these steps in exact order. Do not skip or reorder.

**Step 1 -- Check ABS (cheap, ~500 tokens each):**

1a. `list_abs_library(library=TargetLibrary, title="Book", limit=5)` -- Already in target? Tell user. Done.

1b. `list_abs_library(library=OtherLibrary, title="Book", limit=5)` -- In the other library? Note the ASIN if present. The book is definitely Liberated. If ASIN is available, skip to Step 4b. If no ASIN in result, continue to Step 2.

**Step 2 -- Refresh data sources (required before any list_library call):**

Run BOTH in parallel: `scan_audible()` + `export_library()`. Wait for both to complete.

**Step 3 -- Find the book in Libation (no status filter):**

`list_library(title="Book", limit=5)` -- NO `status=` parameter.

- Found, status=NotLiberated --> Go to Step 4a.
- Found, status=Liberated --> Go to Step 4b.
- Not found --> Go to Step 3a (Search Escalation).

**Step 3a -- Search Escalation (max 3 attempts, then stop):**

Where to get filter values: extract series/author from Step 1b results or from the user's request. Use general knowledge as a last resort.

- Attempt 1: `list_library(series="SeriesName", limit=10)`
- Attempt 2: `list_library(author="AuthorName", limit=10)`
- Attempt 3: `list_library(title="SingleDistinctiveKeyword", limit=10)`
- All failed: Ask user for the ASIN. STOP. Do not call list_library again.

**Step 4a -- New Download (status=NotLiberated):**

```
download_books(asins=["ASIN"])
export_library()
organize_books(library=TargetLibrary)
scan_audiobookshelf(library=TargetLibrary)
match_audiobookshelf(library=TargetLibrary, days_ago=1)
```

**Step 4b -- Re-Download for Target Library (status=Liberated, or found in other ABS library):**

```
set_book_status(asins=["ASIN"], status="not-downloaded")
download_books(asins=["ASIN"])
export_library()
organize_books(library=TargetLibrary)
scan_audiobookshelf(library=TargetLibrary)
match_audiobookshelf(library=TargetLibrary, days_ago=1)
```

The `set_book_status` reset is required because Libation skips Liberated books.

## Procedure: "Remove a Book from a Library"

Two libraries exist: **adult** and **kids**. Always specify `library=`. If the user doesn't say which, ask.

**Step 1 -- Find the item id:**
`list_abs_library(library=TargetLibrary, title="Book", limit=5, refresh=true)` -- Note the `id` of the item to remove.

**Step 2 -- Delete with file cleanup:**
`delete_library_items(library=TargetLibrary, item_ids=[id], cleanup_files=true)`

**Why `cleanup_files=true` is mandatory:** The MCP defaults to DB-only deletion. The `.m4b` file persists on disk in `Kids_Books/` or `Adult_Books/`. ABS's file-system watcher (`disableWatcher=false` on both libraries) auto-rescans and re-imports any orphan within seconds, creating a fresh library item with a new UUID. The MCP still returns `success=true, deleted=1` after a DB-only delete, so the failure is invisible until the user reopens the ABS UI -- the exact symptom of the Heretical Fishing Book 1 case (old id `0d72564f…` removed; resurrected as `5b0165d5…` 5 s later).

**Step 3 -- Scan to settle:**
`scan_audiobookshelf(library=TargetLibrary)` -- Required to reconcile.

**Step 4 -- Verify the file is gone:**
`get_source_status(library=TargetLibrary)` -- Confirm no leftover folder under the destination directory. If an orphan folder persists (e.g., an external tool restored it), `rm` it before re-running `delete_library_items`; otherwise the watcher will keep resurrecting the entry on every scan.

## Common Mistakes

0. **Profanity cleaning is OFF by default.** Production `.env` has `ENABLE_PROFANITY_CLEANING=false`. Pass `enable_profanity_cleaning=true` explicitly on `organize_books(...)` for each cleaning request. Default-on would slow every `organize_books` call by ~8 min/hour of audio. When the user asks to "remove swearing" or "clean", use this per-call override; do not flip the global env var. The override survives only for that one job. Note: cleaning requires the Whisper backend to be reachable and works only for chunked files (>150MB); smaller files are skipped silently.
1. First `list_library` call must omit `status=`. Including `status="NotLiberated"` hides Liberated books.
2. Always call `export_library()` before `list_library()`. Without it, results are stale.
3. "Liberated" means downloaded somewhere, not in the requested library. Always re-download to the target library.
4. Stop after 3 `list_library` attempts per book. Ask the user for the ASIN.
5. Always pass a filter (`title=`, `author=`, or `series=`) to `list_library`. Unfiltered = ~100k tokens.
6. Use individual step tools for small batches. `ingest_books` times out.
7. Always pass `library="kids"` or `library="adult"` explicitly. Omitting defaults to adult (wrong path).
8. Extract the ASIN from `list_library` output. Only ask the user if all searches failed.
9. Always pass `cleanup_files=true` when deleting from ABS. The MCP defaults to DB-only delete; ABS's file watcher (`disableWatcher=false` on both libraries) re-imports the orphaned `.m4b` under a fresh UUID within ~5 seconds -- the tool still reports `success=true`, so the failure only surfaces when the user reopens the UI.
10. When `enable_profanity_cleaning=true`, `organize_books` returns in **~4ms** with a `job_id`. Do NOT poll continuously — see "Async Job Pattern" below for the disconnect-and-recheck pattern.

## Worked Example -- Chrysalis Book 4 to Kids Library

Cross-library scenario: book is Liberated (downloaded for adult library), user wants it in kids.

**What you send to Telegram (3 messages total):**

```
"Got it. Adding Chrysalis 4 to kids."

(silence while Steps 1-4b execute -- ~3 minutes)

"Done. Added Chrysalis 4 to kids."
```

**What you do internally (silent):**

```
Step 1a: list_abs_library(library="kids", title="chrysalis", limit=5) --> not in kids
Step 1b: list_abs_library(library="adult", title="chrysalis", limit=5) --> author=RinoZ
Step 2:  scan_audible() + export_library()
Step 3:  list_library(title="Chrysalis", limit=5) --> ASIN B0XXXXXXX, Liberated --> 4b
Step 4b: set_book_status --> download_books --> export_library --> organize_books(library="kids") --> scan_audiobookshelf(library="kids") --> match_audiobookshelf(library="kids", days_ago=1)
```

## Worked Example -- Four Books, Mixed Results

**What you send to Telegram (2-3 messages total):**

```
"On it -- pulling down those four for the kids library."

(optional, only if download takes >2 min) "Downloading -- 2 of 4 done."

"Done. 3 of 4 added to kids:
- Memory of Earth -- already there
- Squirrel Girl -- added
- US History for Teens -- added (matched from 'American History for Teens')
- Magic Flower Shop -- failed: download errored twice"
```

**What you do internally (silent):**

```
Steps 1-4b for each book, branching as needed.
Ambiguous title? Best-guess, note substitution for summary.
Download error? Retry once silently, report in summary if still failing.
```

## Token Cost Reference

| Cost Tier | Tool(s) | Approx Tokens |
|-----------|---------|---------------|
| Free | `list_libraries`, `get_status`, `get_tool_metrics`, `get_cleaning_progress`, `get_job_result` | <500 |
| Cheap | `list_abs_library` (filtered), `search_abs_library` | <2k |
| Medium | `scan_audible`, `export_library`, `scan_audiobookshelf`, `match_audiobookshelf` | 1-5k |
| Expensive | `list_library` (Libation export) | 50k-150k |
| Slow | `download_books`, `organize_books` | Time-bound |

**Never call `list_library` when `list_abs_library` with filters will do.** Cost difference: 100-200x.

## Profanity Cleaning Timing

Measured on 2026-07-24 against the production Whisper backend (RTX 5060 Ti, `tiny` model, `${REMOTE_WHISPER_URL}`):

**~8.13 min per hour of audio** — source: `agent_planning/execution/profanity_cleaning_mcp_friendly/artifacts/benchmark_result.json` (Task 5, 71.4 min of DCC audio, 3 books, 580.8s wallclock). Functional test re-confirmed at **7.72 min/hour** (Task 6, same dataset). Plan for **~8 min per hour** of source audio when scheduling.

ETA formula: `(elapsed_sec / books_done) * (books_total - books_done)`. If `books_done == 0`, ETA is null. Measured accuracy: **±3%** on 3-book batch (within the ±25% design tolerance).

### Backend params
The MCP explicitly disables diarization and VAD on the Whisper backend by
passing `remoteParams={"is_diarize": "false", "vad_filter": "false", "lang": "en"}`
on every transcription upload. This prevents the 40 GiB OOM caused by pyannote
diarization on audiobook-length files. The container also has a 16 GiB memory
limit and auto-restart as a second line of defense.

## Async Job Pattern (organize_books / ingest_books)

`organize_books` and `ingest_books` use an **async job pattern** (DR-6). They return immediately with a job handle; the actual work runs in a background thread.

**The disconnect-and-recheck pattern (saves tokens):**

```
1. Estimate the job duration from the book(s) involved.
   - Without cleaning: seconds to a minute per book (just file move).
   - With cleaning: ~8 min per hour of audio (e.g. 5-hour book = ~40 min;
     30-hour book = ~4 hours). Compute from book length if known, or
     round up conservatively.

2. Call organize_books(library="...", enable_profanity_cleaning=true, ...)
   → returns {"job_id": "abc123def456", "status": "started"} in ~4ms

3. Send Message 2 to Telegram with the ETA, then END YOUR TURN.
   Example: "Cleaning started. ETA ~40 min. I'll let you know when it's
   done -- ask anytime for status."

4. DO NOT poll continuously. Don't call get_job_result in a loop.
   Wait for the user to ask "what's the status?" or "is it done?".

5. When the user asks, call get_job_result(job_id="...") ONCE.
   While running: {"status": "running", "job_id": "..."}
   On completion: {"status": "completed", "job_id": "...", "result": {...}}
   Extract the full response from `result` field
   (moved[], cleaning_failures[], cleaning{total_cleaned,total_failed,total_profanities}).
```

**Why disconnect?** Continuous polling burns tokens on every turn for jobs that take minutes-to-hours. The user knows it's running; they ask when they want an update. One-shot status check when prompted is the right tradeoff.

**Single-slot guard.** Only one job runs at a time. If a job is already running, `organize_books` returns `{"error": "Job already running", "active_job_id": "<existing>"}` and does NOT start a new one.

**Unknown job after server restart.** If the MCP server restarts mid-job, `get_job_result` returns `{"error": "Unknown job", "job_id": "<old>"}`. Re-invoke `organize_books` to restart the work — the on-disk resume JSON picks up where the previous job left off (see Resume state below).

**Estimating ETAs for cleaning:**
- Use `duration_minutes` from `list_library` (Libation metadata) or `lengthInMinutes` from Audible.
- Formula: `(duration_minutes / 60) * 8 minutes ≈ cleaning_minutes`. Round up generously.
- Heretical Fishing Book 1 (~30 hours audio) ≈ 4 hours of cleaning.
- Single audiobook <1 hour ≈ under 10 min — safe to skip Message 2.

### Response fields — organize_books (via get_job_result)

When `enable_profanity_cleaning=true`, the `result` dict includes:
- `cleaning_failures[]`: list of `{asin, title, error}` for books whose cleaning failed. Failed books are **absent** from `moved[]` — the batch continues with remaining books.
- `cleaning`: `{total_cleaned, total_failed, total_profanities}` — aggregate counters.

### Progress fields — get_cleaning_progress

- `stage`: `staging` | `uploading` | `transcribing` | `resuming` | `done` | `failed` | `skipped` — the current per-book stage. `resuming` fires briefly when a re-run picks up partial chunks from a previous crash (see Resume state below).
- `books_done`: integer, count of books that reached `done` stage
- `books_total`: integer, total books in the current batch
- Per-ASIN entries include the current `stage` for that book

## Resume state (added 2026-07-25)

Per-book profanity cleaning state survives **MCP service restarts and VM reboots**. When `organize_books` is re-invoked for a batch where some books were in-progress:

1. Books already marked `done` are skipped (existing fast path).
2. Books marked `in_progress` (with a valid working dir) are **resumed** — MonkeyPlug's `AudioChunker` detects existing chunk files and only re-transcribes missing chunks. Look for the `resuming` stage in `get_cleaning_progress` output.
3. Books with corrupt or missing working dirs are auto-reset and retried from scratch.

**Where the state lives:** `<working_dir>/../profanity_cleaning_resume.json`. Default working dir is `~/.cache/monkeyplug-cleaning` (XDG cache, **durable — NOT `/tmp`**). On the deployed server it resolves to `/home/stratus/.cache/monkeyplug-cleaning/profanity_cleaning_resume.json`.

**JSON formats** (both accepted; legacy entries still work):
- Legacy: `{"B0X": "done"}` or `{"B0X": "failed"}` — string values
- New: `{"B0X": {"status": "in_progress" | "done", "working_dir": "<path>"}}` — object values

**Operational guidance:**
- If the MCP service crashes mid-batch, just re-invoke `organize_books` with the same params. Resume state will pick up where it left off.
- If a book keeps failing, inspect `/home/stratus/.cache/monkeyplug-cleaning/<ASIN>/` for chunk files + transcript JSONs. Delete the directory if you want to force a clean retry — the resume JSON entry will be reset to `"failed"` on the next call.
- The `~8 min per hour of audio` estimate assumes a healthy backend. With the single-worker backend (see Troubleshooting), a single 100MB chunk can take ~5–20 minutes, blocking all other endpoints including `/docs`.

## Pipeline Step Reference

| # | Tool | Key Params | Notes |
|---|------|-----------|-------|
| 1 | `scan_audible` | -- | Refresh Audible listing. ~10s. |
| 2 | `export_library` | -- | Refresh libation.json. ~2s. REQUIRED before list_library. |
| 3 | `list_library` | `title=`, `series=`, `author=` | NO status filter on first call. Max 3 attempts. |
| 4 | `download_books` | `asins=["ASIN"]` | Specific ASINs. Minutes per book. |
| 5 | `set_book_status` | `asins=["ASIN"]`, `status="not-downloaded"` | Reset Liberated flag for re-download. |
| 6 | `organize_books` | `library="target"` | Move into Author/Series/Title tree. |
| 7 | `scan_audiobookshelf` | `library="target"` | ABS discovers new files. ~20s. |
| 8 | `match_audiobookshelf` | `library="target"`, `days_ago=1` | Link to Audible metadata. |
| 8.5 | `get_cleaning_progress` | -- | ONE-SHOT progress check for an in-flight cleaning job. Returns per-ASIN state + `stage` (staging\|uploading\|transcribing\|resuming\|done\|failed\|skipped), `books_done`, `books_total`. **Call only when the user asks for status — not in a polling loop.** Returns in 2–5ms. |
| 8.6 | `get_job_result` | `job_id="<id>"` | ONE-SHOT check for the final result of an async `organize_books` or `ingest_books` call. Returns `{"status": "completed", "result": {...}}` when done, `{"status": "running"}` while in progress, or `{"error": "Unknown job"}` if the server restarted mid-job. **Call only when the user asks for status — not in a polling loop.** Free. |
| 9 | `delete_library_items` | `library=`, `item_ids=[...]`, **`cleanup_files=true`** | DB-only delete leaves the file on disk; ABS watcher (`disableWatcher=false`) resurrects the entry under a new UUID within seconds. |

## Troubleshooting

| Problem | Action |
|---------|--------|
| Book not appearing in list_library | Run `scan_audible() + export_library()` first |
| Book is Liberated, user wants different library | `set_book_status` to reset, then re-download |
| All 3 search filters failed | Ask user for the ASIN |
| Downloads failing | `get_source_status(library=X)` to check directories |
| Matching failing | Ensure `scan_audiobookshelf` completed before `match_audiobookshelf` |
| MCP unresponsive / "Session not found" | Call `mcp__moltis-admin__restart_mcp_server(server_name="audiobook-ingestion")`. If that fails, ask the user to restart it manually. |
| Book keeps reappearing after delete | You forgot `cleanup_files=true`. Re-call `delete_library_items(..., cleanup_files=true)`; if the file is still on disk the watcher will resurrect it again. Use `get_source_status` to confirm the book folder is gone. |
| Profanity cleaning fails for some books | Check `cleaning_failures[]` in the `organize_books` `result` (via `get_job_result`) — each entry has `{asin, title, error}`. Failed books are skipped (absent from `moved[]`); remaining books continue. To retry a failed book, re-run `organize_books` with the same parameters — the resume state will pick it up. |
| `get_cleaning_progress` reports the same `in_progress` ASIN for >30 min with no progress | The Whisper backend (`containers-gpu.x86experts.com:8001`) is a single uvicorn worker — long transcriptions block all other endpoints. Check `docker logs backend-app-1` on containers-gpu. If the worker OOMed, the container auto-restarts (RestartCount > 0 in `docker ps`). The MCP service will report `Chunking failed: Connection refused` when polling `/task/<uuid>` — this is a backend issue, not the resume feature. |
| MCP service restarts mid-batch, agent sees "Unknown job" from `get_job_result` | Expected. The async job state is in-memory. Re-invoke `organize_books` with the same parameters — the on-disk resume JSON picks up in-progress books and re-runs completed books normally. |
| `/home/stratus/.cache/monkeyplug-cleaning/` fills up disk | Per-book working dirs are cleaned on successful cleaning. If cleaning fails, the dir persists with chunks + transcripts. To reclaim space: `rm -rf /home/stratus/.cache/monkeyplug-cleaning/<ASIN>/` then re-run `organize_books` (the entry will be reset to `"failed"` and the book will retry from scratch). |

## Podcast Workflow

1. **Discover** -- `search_podcasts(query="...")` or `fetch_podcast_feed(url="...")`
2. **Add** -- `add_podcast(feed_url="...", library="adult")`
3. **Download** -- `download_podcast_episodes(library="adult", podcast_id="...")`
4. **Manual** -- `download_podcast_files` for direct URLs

<output_reminder>
Reminder: You produce exactly 2-3 short Telegram messages per request. One acknowledgement, then silence during tool calls, then one summary. All reasoning, step references, intermediate results, and tool output stay internal.
</output_reminder>
