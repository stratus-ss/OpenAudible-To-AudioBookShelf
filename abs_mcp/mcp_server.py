#!/usr/bin/env python3
"""Audiobook Ingestion MCP Server -- assembly point.

Exposes tools for downloading audiobooks via Libation, organizing them,
and ingesting them into AudioBookShelf. Supports multiple libraries
(including podcasts) via a YAML library registry.

Designed for streamable-http transport to be consumed by Moltis or other
MCP clients.

Calls directly into the openaudible_to_audiobookshelf package -- no
duplication of logic.

This module is a thin assembly point: it creates the FastMCP server and
transfers tools registered on each `abs_mcp.tools.*` submodule's own
FastMCP instance onto this server. All tool logic lives in `tools/*.py`
and shared config/auth/metrics wiring lives in `config.py`. Invoke via
`python -m abs_mcp.mcp_server` so package-relative imports resolve.
"""

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import EventStore
from mcp.types import JSONRPCMessage

from .config import _env, LOGGER
from .tools import discovery as _discovery_mod
from .tools import ingestion as _ingestion_mod
from .tools import metrics as _metrics_mod
from .tools import podcast as _podcast_mod


class InMemoryEventStore(EventStore):
    """Simple in-memory event store for SSE/streamable-http session resumability."""

    def __init__(self, max_events: int = 500):
        self._events: dict[str, tuple[str, JSONRPCMessage | None]] = {}
        self._streams: dict[str, list[str]] = {}
        self._counter = 0
        self._max_events = max_events

    async def store_event(self, stream_id, message):
        self._counter += 1
        event_id = f"evt-{self._counter}"
        self._events[event_id] = (stream_id, message)
        self._streams.setdefault(stream_id, []).append(event_id)
        if len(self._events) > self._max_events:
            oldest = next(iter(self._events))
            sid, _ = self._events.pop(oldest)
            if sid in self._streams:
                self._streams[sid] = [e for e in self._streams[sid] if e != oldest]
        return event_id

    async def replay_events_after(self, last_event_id, send_callback):
        if last_event_id not in self._events:
            return None
        stream_id, _ = self._events[last_event_id]
        event_list = self._streams.get(stream_id, [])
        found = False
        for eid in event_list:
            if eid == last_event_id:
                found = True
                continue
            if found:
                _, msg = self._events[eid]
                if msg is not None:
                    await send_callback(msg)
        return stream_id if found else None


mcp = FastMCP(
    "Audiobook Ingestion",
    instructions=(
        "This server manages audiobook and podcast ingestion into AudioBookShelf. "
        "WORKFLOW FOR AUDIOBOOK INGESTION (use individual step tools for reliability):\n"
        "1. list_libraries — discover available libraries and their types\n"
        "2. list_library — browse all books in the Audible/Libation library\n"
        "3. scan_audible — refresh the Audible library list (~10s)\n"
        "4. download_books — download/decrypt books via Libation (pass ASINs or omit for all)\n"
        "5. export_library — export metadata to libation.json (~2s)\n"
        "6. organize_books — move files into Author/Series/Title tree\n"
        "7. scan_audiobookshelf — trigger ABS library scan (~20s)\n"
        "8. match_audiobookshelf — match ABS items to Audible metadata\n\n"
        "The ingest_books tool runs all steps sequentially but can timeout on "
        "long-running operations. Prefer individual step tools for LLM orchestration.\n\n"
        "DISCOVERY & VERIFICATION TOOLS:\n"
        "- list_library — filter Libation library by author/series/title for exact add-targeting\n"
        "- get_source_status — inspect source/destination directories (file counts, extensions)\n"
        "- list_abs_library — query ABS library from cache using targeted author/series/title filters\n"
        "- search_abs_library — direct ABS text search for quick lookups\n"
        "- delete_library_items — remove items with optional cleanup_files to delete disk files\n"
        "- get_tool_metrics — inspect recent response bytes/tokens for MCP tool calls\n"
        "- query_tool_metrics_history — query persisted tool metrics with time/tool filters\n\n"
        "For low-token precision lookups, avoid broad list calls. "
        "Always pass author/series/title/query and set limit=0 to return all matches in one response.\n\n"
        "Always specify library= to target the correct ABS instance (e.g. 'adult', 'kids')."
    ),
    event_store=InMemoryEventStore(),
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8765")),
    streamable_http_path=os.environ.get("MCP_STREAMABLE_PATH", "/mcp"),
)

for _mod in (_ingestion_mod, _discovery_mod, _podcast_mod, _metrics_mod):
    for _name, _tool in _mod.mcp._tool_manager._tools.items():
        mcp.add_tool(_tool.fn, name=_tool.name, description=_tool.description)


if __name__ == "__main__":
    transport = _env("MCP_TRANSPORT", "streamable-http")
    LOGGER.info(
        "Starting Audiobook Ingestion MCP (%s, path=%s)",
        transport,
        mcp.settings.streamable_http_path,
    )
    mcp.run(transport=transport)
