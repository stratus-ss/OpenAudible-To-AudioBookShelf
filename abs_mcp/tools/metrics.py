"""Metrics MCP tools.

Extracted from mcp_server.py as part of Plan 3 (DR-1 module decomposition).
Tool registration happens when this module is imported from mcp_server.py
(Task 7); the @mcp.tool() decorators below cause FastMCP to auto-discover
and register both metrics tools.
"""

from ..config import METRICS, _summarize_metric_records


from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Audiobook Metrics Tools")


@mcp.tool()
def get_tool_metrics(limit: int = 10) -> dict:
    """Return recent in-memory response efficiency metrics."""
    safe_limit = max(min(limit, 50), 1)
    records = METRICS.get_recent(safe_limit)
    return {
        "limit": safe_limit,
        "records": records,
        "summary": _summarize_metric_records(records),
    }


@mcp.tool()
def query_tool_metrics_history(
    tool_name: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Query persisted JSONL response efficiency metrics with filters."""
    records, summary = METRICS.query_history(
        tool_name=tool_name,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return {
        "filters": {
            "tool_name": tool_name,
            "since": since,
            "until": until,
            "limit": max(min(limit, 500), 1),
            "offset": max(offset, 0),
        },
        "records": records,
        "summary": summary,
    }
