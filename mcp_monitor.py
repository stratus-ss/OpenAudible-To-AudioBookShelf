#!/usr/bin/env python3
"""
MCP progress monitor for the current streamable-HTTP server.

Usage:
  ./mcp_monitor.py [host] [base_url]
  ./mcp_monitor.py [base_url]
"""

import json
import subprocess
import sys
import time
import urllib.request
from urllib.error import URLError

DEFAULT_HOST = "stratus@open-audible.x86experts.com"
DEFAULT_BASE_URL = "http://open-audible.x86experts.com:8765/mcp"

HOST = DEFAULT_HOST
BASE_URL = DEFAULT_BASE_URL
if len(sys.argv) > 1:
    if sys.argv[1].startswith("http://") or sys.argv[1].startswith("https://"):
        BASE_URL = sys.argv[1]
    else:
        HOST = sys.argv[1]
if len(sys.argv) > 2:
    BASE_URL = sys.argv[2]

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def post(payload, session_id=None, timeout=15):
    headers = HEADERS.copy()
    if session_id:
        headers["mcp-session-id"] = session_id
    request = urllib.request.Request(
        BASE_URL,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.headers, response.read().decode()


def parse_streamable_http(body):
    data_lines = [line[len("data:") :].strip() for line in body.splitlines() if line.startswith("data:")]
    if data_lines:
        return json.loads("\n".join(data_lines))
    return json.loads(body.strip())


def call_tool(session_id, tool_name, arguments=None):
    _, body = post(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        },
        session_id=session_id,
    )
    return parse_streamable_http(body)


def extract_text_payload(message):
    return message.get("result", {}).get("content", [{}])[0].get("text", "{}")


def get_chunk_progress():
    cmd = [
        "ssh",
        "-o",
        "ConnectTimeout=5",
        HOST,
        """python3 - <<'PY'
import json
from pathlib import Path

resume = Path('/home/stratus/.cache/profanity_cleaning_resume.json')
base = Path('/home/stratus/.cache/monkeyplug-cleaning')

asin = None
working_dir = None
state = None
if resume.exists():
    data = json.loads(resume.read_text() or "{}")
    if data:
        asin, state = next(iter(data.items()))
        working_dir = Path(state.get('working_dir', '')) if state.get('working_dir') else None

if not asin:
    candidates = sorted([p for p in base.iterdir() if p.is_dir()]) if base.exists() else []
    if candidates:
        asin = candidates[0].name
        working_dir = candidates[0]

result = {"asin": asin, "working_dir": str(working_dir) if working_dir else None}
if working_dir and working_dir.exists():
    chunk_dirs = list(working_dir.rglob('chunks'))
    if chunk_dirs:
        chunk_dir = chunk_dirs[0]
        audio = transcripts = 0
        for path in chunk_dir.iterdir():
            if not path.is_file():
                continue
            if path.name.endswith('_transcript.json'):
                transcripts += 1
            elif '_chunk_' in path.name and '_cleaned' not in path.name:
                audio += 1
        result["audio_chunks"] = audio
        result["transcripts"] = transcripts
        result["percent"] = (transcripts * 100 // audio) if audio else None

print(json.dumps(result))
PY""",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return json.loads(result.stdout.strip())


now = time.strftime("%H:%M:%S")
print("╔══════════════════════════════════════════════════╗")
print(f"║  MCP Agent View  —  {now}          ║")
print("╚══════════════════════════════════════════════════╝")
print()

try:
    init_headers, _ = post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-monitor", "version": "1.0"},
            },
        }
    )
    session_id = init_headers.get("mcp-session-id")
    if not session_id:
        raise RuntimeError("No mcp-session-id returned by server")

    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id=session_id, timeout=10)
    response = call_tool(session_id, "get_cleaning_progress")
    payload = json.loads(extract_text_payload(response))

    if not payload:
        print("No active cleaning progress reported.")
    else:
        chunk_progress = get_chunk_progress()
        for key in ("asin", "book_title", "status", "stage"):
            if key in payload:
                print(f"{key}: {payload[key]}")
        if chunk_progress:
            if chunk_progress.get("audio_chunks") is not None:
                print(f"audio_chunks: {chunk_progress['audio_chunks']}")
                print(f"transcripts: {chunk_progress['transcripts']}")
                if chunk_progress.get("percent") is not None:
                    print(f"chunk_progress: {chunk_progress['percent']}%")
            if chunk_progress.get("working_dir"):
                print(f"working_dir: {chunk_progress['working_dir']}")
except URLError as exc:
    print(f"Connection error: {exc}")
    print("Hint: verify the MCP server is listening on /mcp")
except Exception as exc:
    print(f"Failed to query MCP progress: {exc}")
