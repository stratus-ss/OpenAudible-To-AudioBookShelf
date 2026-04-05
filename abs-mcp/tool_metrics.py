"""Tool call efficiency metrics for MCP endpoints."""

from __future__ import annotations

import json
import math
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


class ToolMetricsRecorder:
    """Track rough response token usage per tool call."""

    def __init__(
        self,
        history_path: str,
        recent_max: int = 50,
        rotate_max_lines: int = 2000,
        rotate_keep_lines: int = 1000,
    ) -> None:
        self._recent: deque[dict] = deque(maxlen=recent_max)
        self._history_path = Path(history_path)
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_max_lines = max(rotate_max_lines, 200)
        self._rotate_keep_lines = max(min(rotate_keep_lines, self._rotate_max_lines), 100)

    def record(self, tool_name: str, result: str, duration_ms: int, success: bool = True) -> str:
        """Record metrics for a tool call and return the original result unchanged."""
        payload = result if isinstance(result, str) else json.dumps(result)
        payload_bytes = len(payload.encode("utf-8"))
        rough_tokens = self.estimate_tokens(payload_bytes)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_name": tool_name,
            "response_bytes": payload_bytes,
            "rough_tokens": rough_tokens,
            "duration_ms": max(duration_ms, 0),
            "success": bool(success),
        }
        self._recent.append(row)
        self._append_jsonl(row)
        return result

    @staticmethod
    def estimate_tokens(payload_bytes: int) -> int:
        """Estimate token count from UTF-8 payload size."""
        return int(math.ceil(max(payload_bytes, 0) / 4))

    def get_recent(self, limit: int) -> list[dict]:
        """Get recent records from in-memory ring buffer."""
        safe_limit = max(min(limit, self._recent.maxlen or 50), 1)
        return list(self._recent)[-safe_limit:]

    def query_history(
        self,
        tool_name: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], dict]:
        """Query persisted history with simple filters."""
        safe_limit = max(min(limit, 500), 1)
        safe_offset = max(offset, 0)
        if not self._history_path.is_file():
            return [], self._aggregate([])

        since_dt = self._parse_iso(since) if since else None
        until_dt = self._parse_iso(until) if until else None

        filtered: list[dict] = []
        with self._history_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tool_name and row.get("tool_name") != tool_name:
                    continue
                ts = self._parse_iso(row.get("timestamp", ""))
                if since_dt and (ts is None or ts < since_dt):
                    continue
                if until_dt and (ts is None or ts > until_dt):
                    continue
                filtered.append(row)

        page = filtered[safe_offset:safe_offset + safe_limit]
        return page, self._aggregate(filtered)

    @staticmethod
    def _parse_iso(value: str) -> datetime | None:
        if not value:
            return None
        try:
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    @staticmethod
    def _aggregate(records: list[dict]) -> dict:
        per_tool: dict[str, list[int]] = {}
        for row in records:
            tool = row.get("tool_name", "unknown")
            per_tool.setdefault(tool, []).append(int(row.get("rough_tokens", 0)))

        summary = {}
        for tool, vals in per_tool.items():
            if not vals:
                continue
            summary[tool] = {
                "count": len(vals),
                "avg_tokens": round(sum(vals) / len(vals), 2),
                "min_tokens": min(vals),
                "max_tokens": max(vals),
            }
        return {"total_records": len(records), "per_tool": summary}

    def _append_jsonl(self, row: dict) -> None:
        self._maybe_rotate()
        with self._history_path.open("a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

    def _maybe_rotate(self) -> None:
        if not self._history_path.is_file():
            return
        with self._history_path.open() as f:
            lines = f.readlines()
        if len(lines) <= self._rotate_max_lines:
            return
        kept = lines[-self._rotate_keep_lines:]
        with self._history_path.open("w") as f:
            f.writelines(kept)
