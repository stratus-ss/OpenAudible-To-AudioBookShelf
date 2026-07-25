"""Server-side parsing helpers for cached library data."""


class LibraryDataParser:
    """Parse, filter, sort, and optionally paginate library datasets."""

    def filter_libation_books(
        self,
        books: list[dict],
        status: str | None,
        author: str | None,
        series: str | None,
        title: str | None,
        min_duration: int | None,
        max_duration: int | None,
    ) -> list[dict]:
        """Filter Libation books using case-insensitive substring matching."""
        result = books
        if status:
            status_lower = status.lower()
            result = [b for b in result if b.get("BookStatus", "").lower() == status_lower]
        if author:
            author_lower = author.lower()
            result = [b for b in result if author_lower in b.get("AuthorNames", "").lower()]
        if series:
            series_lower = series.lower()
            result = [
                b for b in result
                if series_lower in b.get("SeriesNames", "").lower()
                or series_lower in b.get("Title", "").lower()
                or series_lower in b.get("Subtitle", "").lower()
            ]
        if title:
            title_lower = title.lower()
            result = [
                b for b in result
                if title_lower in b.get("Title", "").lower()
                or title_lower in b.get("Subtitle", "").lower()
            ]
        if min_duration is not None:
            result = [b for b in result if b.get("LengthInMinutes", 0) >= min_duration]
        if max_duration is not None:
            result = [b for b in result if b.get("LengthInMinutes", 0) <= max_duration]
        return result

    def sort_libation_books(self, books: list[dict], sort_by: str | None) -> list[dict]:
        """Sort Libation books by supported keys."""
        if not sort_by:
            return books
        key_map = {
            "duration": lambda b: b.get("LengthInMinutes", 0),
            "title": lambda b: b.get("Title", "").lower(),
            "author": lambda b: b.get("AuthorNames", "").lower(),
            "date_added": lambda b: b.get("DateAdded", ""),
        }
        key_fn = key_map.get(sort_by)
        return sorted(books, key=key_fn) if key_fn else books

    def filter_abs_items(
        self,
        items: list[dict],
        query: str | None,
        title: str | None,
        author: str | None,
        series: str | None,
    ) -> list[dict]:
        """Filter flattened ABS items by substring across selected fields."""
        result = items
        if query:
            q = query.lower()
            result = [
                item for item in result
                if q in item.get("title", "").lower()
                or q in item.get("author", "").lower()
                or q in item.get("series", "").lower()
            ]
        if title:
            t = title.lower()
            result = [item for item in result if t in item.get("title", "").lower()]
        if author:
            a = author.lower()
            result = [item for item in result if a in item.get("author", "").lower()]
        if series:
            s = series.lower()
            result = [
                item for item in result
                if s in item.get("series", "").lower()
                or s in item.get("title", "").lower()
            ]
        return result

    def sort_abs_items(self, items: list[dict], sort_by: str | None) -> list[dict]:
        """Sort flattened ABS items by supported keys."""
        if not sort_by:
            return items
        key_map = {
            "title": lambda i: i.get("title", "").lower(),
            "author": lambda i: i.get("author", "").lower(),
            "series": lambda i: i.get("series", "").lower(),
            "duration": lambda i: i.get("duration", 0),
            "added_at": lambda i: i.get("added_at", ""),
        }
        key_fn = key_map.get(sort_by)
        return sorted(items, key=key_fn) if key_fn else items

    def select_output_slice(
        self,
        items: list[dict],
        limit: int,
        offset: int,
        *,
        filters_present: bool,
        default_limit: int = 25,
        max_limit: int = 200,
    ) -> tuple[list[dict], int, int, int | None, bool]:
        """Return all matches when limit<=0, paginate only when limit>0."""
        safe_offset = max(offset, 0)
        if limit <= 0:
            page = items[safe_offset:]
            return page, 0, safe_offset, None, False

        safe_limit = limit if limit > 0 else default_limit
        safe_limit = min(safe_limit, max_limit)
        page = items[safe_offset:safe_offset + safe_limit]
        next_offset = safe_offset + safe_limit if (safe_offset + safe_limit) < len(items) else None
        return page, safe_limit, safe_offset, next_offset, True
