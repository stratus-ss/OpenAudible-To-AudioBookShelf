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
  - mcp__audiobook-ingestion__search_podcasts
  - mcp__audiobook-ingestion__add_podcast
  - mcp__audiobook-ingestion__list_podcasts
  - mcp__audiobook-ingestion__get_podcast_episodes
  - mcp__audiobook-ingestion__download_podcast_episodes
  - mcp__audiobook-ingestion__fetch_podcast_feed
  - mcp__audiobook-ingestion__download_podcast_files
---

# Audiobook Pipeline

## Four Unbreakable Rules

1. **ALWAYS pass `library=` explicitly on EVERY tool call.** The adult and kids libraries use DIFFERENT filesystem paths (`Adult_Books` vs `Kids_Books`). If you omit `library=` or pass `null`, it defaults to `adult` and the book goes to the WRONG directory. This applies to: `organize_books`, `scan_audiobookshelf`, `match_audiobookshelf`, `list_abs_library`, `get_status`, and `get_source_status`. Never rely on the default.

2. **Liberated does NOT mean done.** When a user asks to add a book to a specific library, and the book is already Liberated (downloaded), you MUST mark it as unliberated with `set_book_status(asins=["ASIN"], status="not-downloaded")` and re-download it to the target library. Do NOT tell the user the book is "already downloaded" or "already in the library" unless it is already in the EXACT library they requested.

3. **Always run `export_library()` before any `list_library()` call.** The `list_library` tool reads from an exported JSON file, not the live database. Without a fresh export, books are invisible even if they appear in the Libation UI.

4. **Never filter by `status="NotLiberated"` on the first `list_library` call.** Searching with a status filter hides books in the other state. Search without status first, then branch based on the status returned.

## Procedure: "Add a Book to a Library"

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

The `set_book_status` reset is required because Libation skips Liberated books. Report progress after each step.

## DON'T

1. **Don't use `status="NotLiberated"` on the first `list_library` call.** This hides Liberated books and causes search loops.
2. **Don't skip `export_library()` before `list_library()`.** Results will be stale.
3. **Don't tell the user a book is "already downloaded" when they asked for a specific library.** Liberated means downloaded somewhere, not delivered to the requested library. Re-download it.
4. **Don't call `list_library` more than 3 times for the same book.** Ask for the ASIN after 3 failures.
5. **Don't call `list_library` without a filter.** Always pass at least one of `title=`, `author=`, or `series=`. An unfiltered call returns ~100k tokens.
6. **Don't use `ingest_books` for single/few books.** Timeout-prone. Use individual step tools.
7. **Don't omit `library=` or pass `library=null` on any tool call.** Adult path is `~/Adult_Books`, kids path is `~/Kids_Books`. Omitting `library=` defaults to adult and puts files in the WRONG directory. Always pass the explicit string `"kids"` or `"adult"`.
8. **Don't ask the user for the ASIN if `list_library` already returned it.** Extract it from the output.

## Worked Example -- Chrysalis Book 4 to Kids Library

Cross-library scenario: book is Liberated (downloaded for adult library), user wants it in kids.

```
User: "add chrysalis book 4 to the kids library"

Step 1a: list_abs_library(library="kids", title="chrysalis", limit=5)
  --> Not found in kids.

Step 1b: list_abs_library(library="adult", title="chrysalis", limit=5)
  --> Found "Chrysalis, Books 1-3" by RinoZ. Book 4 not in adult ABS either.
  --> We now know the author is RinoZ. Continue to Steps 2-3.

Step 2: scan_audible() + export_library() in parallel.

Step 3: list_library(title="Chrysalis", limit=5)
  --> Returns matches including "Chrysalis 4: Between a Rock and a Carapace"
  --> ASIN: B0XXXXXXX, status=Liberated.
  --> Branch to Step 4b.

  (If not in first 5 results, escalate:)
  (list_library(series="Chrysalis", limit=10))
  (list_library(author="RinoZ", limit=10) -- author from Step 1b)

Step 4b: set_book_status(asins=["B0XXXXXXX"], status="not-downloaded")
Step 4b: download_books(asins=["B0XXXXXXX"])
Step 4b: export_library()
Step 4b: organize_books(library="kids")
Step 4b: scan_audiobookshelf(library="kids")
Step 4b: match_audiobookshelf(library="kids", days_ago=1)

Done. Book added to kids library.
```

## Worked Example -- Pilgrim's Progress (New Purchase)

New book, never downloaded.

```
User: "add Pilgrim's Progress to the kids library"

Step 1a: list_abs_library(library="kids", title="Pilgrim", limit=5) --> Not found.
Step 1b: list_abs_library(library="adult", title="Pilgrim", limit=5) --> Not found.

Step 2: scan_audible() + export_library() in parallel.

Step 3: list_library(title="Pilgrim", limit=5)
  --> Found "Pilgrim's Progress: Updated, Modern English" by John Bunyan
  --> ASIN: B01L2MJMEO, status=NotLiberated. Branch to Step 4a.

Step 4a: download_books(asins=["B01L2MJMEO"])
Step 4a: export_library()
Step 4a: organize_books(library="kids")
Step 4a: scan_audiobookshelf(library="kids")
Step 4a: match_audiobookshelf(library="kids", days_ago=1)

Done.
```

## Token Cost Reference

| Cost Tier | Tool(s) | Approx Tokens |
|-----------|---------|---------------|
| Free | `list_libraries`, `get_status`, `get_tool_metrics` | <500 |
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

## Troubleshooting

| Problem | Action |
|---------|--------|
| Book not appearing in list_library | Run `scan_audible() + export_library()` first |
| Book is Liberated, user wants different library | `set_book_status` to reset, then re-download |
| All 3 search filters failed | Ask user for the ASIN |
| Downloads failing | `get_source_status(library=X)` to check directories |
| Matching failing | Ensure `scan_audiobookshelf` completed before `match_audiobookshelf` |
| MCP unresponsive / "Session not found" | Call `mcp__moltis-admin__restart_mcp_server(server_name="audiobook-ingestion")`. If that fails, ask the user to restart it manually. |

## Podcast Workflow

1. **Discover** -- `search_podcasts(query="...")` or `fetch_podcast_feed(url="...")`
2. **Add** -- `add_podcast(feed_url="...", library="adult")`
3. **Download** -- `download_podcast_episodes(library="adult", podcast_id="...")`
4. **Manual** -- `download_podcast_files` for direct URLs
