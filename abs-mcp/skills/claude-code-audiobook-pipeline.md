Use this skill when the user wants to manage audiobooks or podcasts via the audiobook-ingestion MCP server. Trigger if the conversation involves:
- audiobook, audiobooks, audio book
- podcast, podcasts
- libation, audible
- AudioBookShelf, ABS
- "add book", "download book", "ingest"
- "add podcast", "download podcast"
Do NOT trigger for unrelated topics.

## The One Rule

**Never call `list_library` when `list_abs_library` with filters will do.** Cost difference: 100x–200x.

## Token Cost Reference

| Cost Tier | Tool(s) | Approx Tokens |
|-----------|---------|---------------|
| Free | `list_libraries`, `get_status`, `get_tool_metrics` | <500 |
| Cheap | `list_abs_library` (filtered), `search_abs_library` | <2k |
| Medium | `scan_audible`, `export_library`, `scan_audiobookshelf`, `match_audiobookshelf` | 1–5k |
| Expensive | `list_library` (full Libation export) | 50k–150k |
| Slow | `download_books`, `organize_books` | Time-bound, not token-bound |

## Libraries

Two libraries configured: **adult** and **kids**. Always specify `library=`. If the user doesn't specify, ask.

## Decision Tree: "Add a Book"

This is the most common request. Follow these steps **in order**:

### Step 1 — Check ABS first (cheap)

```
list_abs_library(library="adult", title="Book Title", limit=5)
```

If the book is already in ABS → tell the user. Done.

### Step 2 — Refresh Audible listing (medium)

```
scan_audible()
```

Fast (~10s). Run once per day before any download work. Ensures new purchases are visible.

### Step 3 — Find ASINs (expensive — minimize with filters)

```
list_library(title="Book Title", status="NotLiberated", limit=5)
```

**Always include `status="NotLiberated"` plus at least one of `title=`, `author=`, or `series=`.** This is the only expensive call. Narrowing filters = fewer tokens returned.

For series: `list_library(series="Series Name", status="NotLiberated", limit=20)`

Extract the ASIN(s) from the results.

### Step 4 — Download (slow)

```
download_books(asins=["B0XXXXXXX"])
```

Downloads and decrypts. Can take minutes per book.

### Step 5 — Run the remaining pipeline (medium each)

Execute sequentially — each depends on the previous:

```
export_library()
organize_books(library="adult")
scan_audiobookshelf(library="adult")
match_audiobookshelf(library="adult", days_ago=1)
```

Report progress after each step.

## Token-Efficient Query Patterns

### Check if a book exists in ABS
```json
{"library": "adult", "title": "The Way of Kings", "limit": 5}
```
→ list_abs_library: **~500 tokens**

### Check series completion
```json
{"library": "adult", "series": "Stormlight Archive", "sort_by": "title"}
```
→ list_abs_library: **~1k tokens**

### Find new books to download (unavoidable expensive call)
```json
{"title": "The Way of Kings", "author": "Sanderson", "status": "NotLiberated", "limit": 5}
```
→ list_library: **~2–5k tokens** (filtered) vs **~100k+ tokens** (unfiltered)

### Quick text search across ABS
```json
{"library": "adult", "query": "Sanderson", "limit": 10}
```
→ search_abs_library: **~1k tokens**

## Anti-Patterns — Do NOT Do These

1. **`list_library` with no filters** — returns the entire Audible library. ~100k tokens wasted.
2. **`ingest_books` for single/few books** — runs all 6 steps in one call. Timeout-prone, no progress visibility. Use individual step tools instead.
3. **`refresh=true` on `list_abs_library` without reason** — forces a full ABS API fetch. Only use after adding books.
4. **Missing `library=` parameter** — causes ambiguity or targets the wrong library.

## Pipeline Step Reference

| # | Tool | Key Params | Notes |
|---|------|-----------|-------|
| 1 | `scan_audible` | — | Refresh Audible listing. ~10s. |
| 2 | `download_books` | `asins=["B0X"]` | Specific ASINs. Omit for all new. Minutes. |
| 3 | `export_library` | — | Refresh libation.json. ~2s. |
| 4 | `organize_books` | `library="adult"` | Move into Author/Series/Title tree. |
| 5 | `scan_audiobookshelf` | `library="adult"` | ABS discovers new files. ~20s. |
| 6 | `match_audiobookshelf` | `library="adult"`, `days_ago=1` | Link ABS items to Audible metadata. |

## Podcast Workflow

For podcasts, use podcast-specific tools (all cheap):

1. **Discover** — `search_podcasts(query="...")` or `fetch_podcast_feed(url="...")`
2. **Add** — `add_podcast(feed_url="...", library="adult")`
3. **Download** — `download_podcast_episodes(library="adult", podcast_id="...")`
4. **Manual downloads** — `download_podcast_files` for direct URLs when ABS can't subscribe

## MCP Server Ops

The audiobook-ingestion MCP server runs remotely. If you need to adjust settings or restart:
```
ssh stratus@open-audible
```

## Troubleshooting

- Downloads failing → `get_source_status(library="adult")` to check directories
- Matching failing → ensure `scan_audiobookshelf` completed before `match_audiobookshelf`
- MCP unresponsive → SSH to `stratus@open-audible` and restart the server
- Need to re-download → `set_book_status(asins=["B0X"], status="not-downloaded")` then `download_books`

## Activation

When activated, confirm the target library, then proceed with the decision tree. Don't read domain memory files — this is a tool-oriented skill, not a domain.
