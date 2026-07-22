---
name: audiobook-pipeline
description: "Audiobook ingestion pipeline. ALWAYS read this skill before any audiobook task. Critical: run export_library() before list_library(). Never filter by status=NotLiberated on first search. If a book is Liberated but user wants it in a different library, use set_book_status to unliberate and re-download it."
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

Message 1 (required): One short sentence acknowledging the task.
  Example: "On it -- pulling down those four for the kids library."

Message 2 (optional): One short sentence only if a step takes over 2 minutes or errors.
  Example: "Downloading -- 2 of 4 done."

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

1. First `list_library` call must omit `status=`. Including `status="NotLiberated"` hides Liberated books.
2. Always call `export_library()` before `list_library()`. Without it, results are stale.
3. "Liberated" means downloaded somewhere, not in the requested library. Always re-download to the target library.
4. Stop after 3 `list_library` attempts per book. Ask the user for the ASIN.
5. Always pass a filter (`title=`, `author=`, or `series=`) to `list_library`. Unfiltered = ~100k tokens.
6. Use individual step tools for small batches. `ingest_books` times out.
7. Always pass `library="kids"` or `library="adult"` explicitly. Omitting defaults to adult (wrong path).
8. Extract the ASIN from `list_library` output. Only ask the user if all searches failed.
9. Always pass `cleanup_files=true` when deleting from ABS. The MCP defaults to DB-only delete; ABS's file watcher (`disableWatcher=false` on both libraries) re-imports the orphaned `.m4b` under a fresh UUID within ~5 seconds -- the tool still reports `success=true`, so the failure only surfaces when the user reopens the UI.

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
| Free | `list_libraries`, `get_status`, `get_tool_metrics`, `get_cleaning_progress` | <500 |
| Cheap | `list_abs_library` (filtered), `search_abs_library` | <2k |
| Medium | `scan_audible`, `export_library`, `scan_audiobookshelf`, `match_audiobookshelf` | 1-5k |
| Expensive | `list_library` (Libation export) | 50k-150k |
| Slow | `download_books`, `organize_books` | Time-bound |

**Never call `list_library` when `list_abs_library` with filters will do.** Cost difference: 100-200x.

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
| 8.5 | `get_cleaning_progress` | -- | Poll mid-operation for per-book profanity cleaning progress. Returns JSON with per-ASIN state (processing/transcribing/done/failed/skipped). Free to call. Use when `organize_books` or `ingest_books` is invoked with `enable_profanity_cleaning=true` and the operation takes more than 2 minutes. |
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

## Podcast Workflow

1. **Discover** -- `search_podcasts(query="...")` or `fetch_podcast_feed(url="...")`
2. **Add** -- `add_podcast(feed_url="...", library="adult")`
3. **Download** -- `download_podcast_episodes(library="adult", podcast_id="...")`
4. **Manual** -- `download_podcast_files` for direct URLs

<output_reminder>
Reminder: You produce exactly 2-3 short Telegram messages per request. One acknowledgement, then silence during tool calls, then one summary. All reasoning, step references, intermediate results, and tool output stay internal.
</output_reminder>
