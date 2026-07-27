#!/usr/bin/env bash
# Run this from your local machine (not on the servers).
# Shows pipeline status in a human-readable format.
set -euo pipefail

OA_HOST="${1:-stratus@open-audible.x86experts.com}"
GPU_HOST="${2:-root@containers-gpu.x86experts.com}"
LOCAL_FQDN="$(uname -n 2>/dev/null || printf 'localhost')"
LOCAL_SHORT="${LOCAL_FQDN%%.*}"

host_is_local() {
  local host="$1"
  case "$host" in
    "$LOCAL_SHORT"|"$LOCAL_FQDN"|"$USER@$LOCAL_SHORT"|"$USER@$LOCAL_FQDN"|localhost|127.0.0.1)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

run_host() {
  local host="$1"
  local cmd="$2"
  if host_is_local "$host"; then
    bash -lc "$cmd"
  else
    ssh -o ConnectTimeout=5 "$host" "$cmd"
  fi
}

NOW=$(date '+%H:%M:%S')
echo "╔══════════════════════════════════════════╗"
echo "║  Pipeline Monitor  —  $NOW     ║"
echo "╚══════════════════════════════════════════╝"
echo ""

echo "── MCP Service ──────────────────────────────"
run_host "$OA_HOST" "$(cat <<'EOF'
  UNIT_STATUS=$(systemctl --user is-active audiobook-ingestion-mcp 2>/dev/null || true)
  [ -z "$UNIT_STATUS" ] && UNIT_STATUS="unknown"
  echo "  Unit status: $UNIT_STATUS"

  PID=$(pgrep -f "abs_mcp.mcp_server" 2>/dev/null | head -1)
  [ -z "$PID" ] && PID=$(pgrep -f "audiobook-mcp" 2>/dev/null | head -1)
  if [ -n "$PID" ]; then
    echo "  PID:         $PID"
    echo "  Uptime:      $(ps -o etime= -p "$PID" 2>/dev/null | xargs)"
  else
    echo "  PID:         (none)"
  fi

  LISTENER=$(ss -ltnp "( sport = :8765 )" 2>/dev/null | awk "NR==2 {print \$4}")
  echo "  Listener:    ${LISTENER:-not listening}"
EOF
)" 2>/dev/null || echo "  (unreachable)"

echo ""
echo "── Job Progress ─────────────────────────────"
if host_is_local "$OA_HOST"; then
  python3 - <<'PY'
import json
from pathlib import Path

resume = Path("/home/stratus/.cache/profanity_cleaning_resume.json")
base = Path("/home/stratus/.cache/monkeyplug-cleaning")

asin = None
working_dir = None
status = None
if resume.exists():
    data = json.loads(resume.read_text() or "{}")
    if data:
        asin, state = next(iter(data.items()))
        working_dir = Path(state.get("working_dir", "")) if state.get("working_dir") else None
        status = state.get("status")

if not asin:
    candidates = sorted([p for p in base.iterdir() if p.is_dir()]) if base.exists() else []
    if candidates:
        asin = candidates[0].name
        working_dir = candidates[0]

if not asin or not working_dir or not working_dir.exists():
    print("  No active job")
    raise SystemExit(0)

chunk_dirs = list(working_dir.rglob("chunks"))
chunk_dir = chunk_dirs[0] if chunk_dirs else None
audio = 0
transcripts = 0
if chunk_dir and chunk_dir.exists():
    for path in chunk_dir.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith("_transcript.json"):
            transcripts += 1
        elif "_chunk_" in path.name and "_cleaned" not in path.name:
            audio += 1

print(f"  ASIN:            {asin}")
print(f"  Resume status:   {status or 'unknown'}")
print(f"  Working dir:     {working_dir}")
print(f"  Audio chunks:    {audio}")
print(f"  Transcripts:     {transcripts}")
if audio:
    print(f"  Progress:        {transcripts * 100 // audio}%")
PY
else
  ssh -o ConnectTimeout=5 "$OA_HOST" 'python3 - <<'"'"'"'"'"'"'"'"'PY'"'"'"'"'"'"'"'"'
import json
from pathlib import Path

resume = Path("/home/stratus/.cache/profanity_cleaning_resume.json")
base = Path("/home/stratus/.cache/monkeyplug-cleaning")

asin = None
working_dir = None
status = None
if resume.exists():
    data = json.loads(resume.read_text() or "{}")
    if data:
        asin, state = next(iter(data.items()))
        working_dir = Path(state.get("working_dir", "")) if state.get("working_dir") else None
        status = state.get("status")

if not asin:
    candidates = sorted([p for p in base.iterdir() if p.is_dir()]) if base.exists() else []
    if candidates:
        asin = candidates[0].name
        working_dir = candidates[0]

if not asin or not working_dir or not working_dir.exists():
    print("  No active job")
    raise SystemExit(0)

chunk_dirs = list(working_dir.rglob("chunks"))
chunk_dir = chunk_dirs[0] if chunk_dirs else None
audio = 0
transcripts = 0
if chunk_dir and chunk_dir.exists():
    for path in chunk_dir.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith("_transcript.json"):
            transcripts += 1
        elif "_chunk_" in path.name and "_cleaned" not in path.name:
            audio += 1

print(f"  ASIN:            {asin}")
print(f"  Resume status:   {status or 'unknown'}")
print(f"  Working dir:     {working_dir}")
print(f"  Audio chunks:    {audio}")
print(f"  Transcripts:     {transcripts}")
if audio:
    print(f"  Progress:        {transcripts * 100 // audio}%")
PY' 2>/dev/null || true
fi

echo ""
echo "── Whisper Backend ──────────────────────────"
run_host "$GPU_HOST" "$(cat <<'EOF'
  STATUS=$(docker exec backend-app-1 curl -so /dev/null -w "%{http_code}" --max-time 10 http://127.0.0.1:8000/docs 2>/dev/null)
  echo "  HTTP status:     $STATUS"
  docker exec backend-app-1 python - <<'"'"'PY'"'"'
import sqlite3

conn = sqlite3.connect("/Whisper-WebUI-Swear-Removal/backend/records.db")
cur = conn.cursor()
rows = cur.execute("select status, count(*) from tasks group by status order by status").fetchall()
recent = cur.execute("select uuid, status from tasks order by created_at desc limit 5").fetchall()

if rows:
    print("  Task breakdown:")
    for status, count in rows:
        print(f"    {status}: {count}")

if recent:
    print("  Recent tasks:")
    for uuid, status in recent:
        print(f"    {uuid} {status}")
PY
EOF
)" 2>/dev/null || echo "  (unreachable)"

echo ""
echo "── Resources ────────────────────────────────"
run_host "$GPU_HOST" "$(cat <<'EOF'
  echo "  GPU: $(nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null)"
  free -h | awk "/Mem:/{printf \"  RAM: %s used / %s total\\n\", \$3, \$2}"
EOF
)" 2>/dev/null || true

echo ""
echo "── Recent MCP Logs (last 5 min) ────────────"
run_host "$OA_HOST" 'journalctl --user -u audiobook-ingestion-mcp --since "5 min ago" --no-pager 2>/dev/null | tail -10' 2>/dev/null || echo "  (no recent logs)"
echo ""
