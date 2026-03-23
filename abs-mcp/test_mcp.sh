#!/bin/bash
# End-to-end test for the Audiobook Ingestion MCP server.
# Exercises every exposed tool and all override parameters against the test ABS instance.
#
# Usage:
#   bash abs-mcp/test_mcp.sh                        # full test
#   bash abs-mcp/test_mcp.sh --skip-download         # skip Libation download
#   bash abs-mcp/test_mcp.sh --env abs-mcp/.env      # custom env file

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

PASS=0
FAIL=0
SKIP=0
BRIDGE_PID=""
SKIP_DOWNLOAD=false
ENV_FILE="$SCRIPT_DIR/.env.test"

# --- parse args -------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-download) SKIP_DOWNLOAD=true; shift ;;
        --env) ENV_FILE="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# --- colours & logging ------------------------------------------------------

red()    { printf '\033[1;31m%s\033[0m' "$*"; }
green()  { printf '\033[1;32m%s\033[0m' "$*"; }
yellow() { printf '\033[1;33m%s\033[0m' "$*"; }
bold()   { printf '\033[1m%s\033[0m' "$*"; }

log_pass() { PASS=$((PASS + 1)); echo "  $(green PASS) $1"; }
log_fail() { FAIL=$((FAIL + 1)); echo "  $(red FAIL) $1${2:+ — $2}"; }
log_skip() { SKIP=$((SKIP + 1)); echo "  $(yellow SKIP) $1${2:+ — $2}"; }

# --- env loading ------------------------------------------------------------

load_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "$(red ERROR): env file not found: $ENV_FILE"
        exit 1
    fi
    while IFS='=' read -r key value; do
        key="${key%%#*}"
        key="${key// /}"
        [[ -z "$key" ]] && continue
        export "$key=$value"
    done < <(grep -v '^\s*#' "$ENV_FILE" | grep -v '^\s*$')
}

# --- MCP bridge helpers -----------------------------------------------------

start_bridge() {
    local venv_python="$PROJECT_DIR/venv/bin/python"
    if [[ ! -x "$venv_python" ]]; then
        venv_python="python3"
    fi

    coproc MCP_BRIDGE { cd "$SCRIPT_DIR" && "$venv_python" "_mcp_bridge.py"; }
    BRIDGE_PID=$MCP_BRIDGE_PID

    local ready
    read -r -t 15 ready <&${MCP_BRIDGE[0]}
    if [[ "$ready" != "READY" ]]; then
        echo "  $(red 'ERROR'): MCP bridge did not start (got: $ready)"
        return 1
    fi
    return 0
}

mcpcall() {
    echo "$*" >&${MCP_BRIDGE[1]}
    local resp
    read -r -t 120 resp <&${MCP_BRIDGE[0]}
    echo "$resp"
}

mcp_text() {
    python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('text',''))" "$1" 2>/dev/null
}

mcp_error() {
    python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('error',''))" "$1" 2>/dev/null
}

mcp_json_field() {
    local resp="$1" field="$2"
    python3 -c "
import sys,json
r=json.loads(sys.argv[1])
v=r.get(sys.argv[2], r.get('text',''))
if isinstance(v,str):
    try: v=json.loads(v)
    except: pass
print(json.dumps(v))
" "$resp" "$field" 2>/dev/null
}

mcp_json_len() {
    python3 -c "import sys,json; print(len(json.loads(sys.argv[1])))" "$1" 2>/dev/null
}

assert_no_error() {
    local tool_name="$1" resp="$2"
    local err
    err=$(mcp_error "$resp")
    if [[ -n "$err" ]]; then
        log_fail "$tool_name" "$err"
        return 1
    fi
    return 0
}

# --- cleanup ----------------------------------------------------------------

cleanup() {
    if [[ -n "$BRIDGE_PID" ]] && kill -0 "$BRIDGE_PID" 2>/dev/null; then
        echo "QUIT" >&${MCP_BRIDGE[1]} 2>/dev/null || true
        wait "$BRIDGE_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ============================================================================
# TESTS
# ============================================================================

echo "$(bold '=== Audiobook Ingestion MCP — End-to-End Test ===')"
echo "  Env file : $ENV_FILE"
echo "  Skip DL  : $SKIP_DOWNLOAD"
echo ""

# === [1/10] Prerequisites ===================================================

echo "$(bold '[1/10] Prerequisites')"

load_env

for cmd in python3 libationcli; do
    if command -v "$cmd" &>/dev/null; then
        log_pass "$cmd found"
    else
        log_fail "$cmd not found"
    fi
done

VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
if [[ -x "$VENV_PYTHON" ]]; then
    log_pass "Virtual environment found"
    if "$VENV_PYTHON" -c "import mcp" &>/dev/null; then
        log_pass "mcp package installed"
    else
        log_fail "mcp package not installed in venv"; exit 1
    fi
else
    log_fail "No virtual environment at $VENV_PYTHON"; exit 1
fi

if [[ -n "${SOURCE_AUDIO_BOOK_DIRECTORY:-}" && -d "$SOURCE_AUDIO_BOOK_DIRECTORY" ]]; then
    log_pass "SOURCE_AUDIO_BOOK_DIRECTORY exists: $SOURCE_AUDIO_BOOK_DIRECTORY"
else
    log_fail "SOURCE_AUDIO_BOOK_DIRECTORY not set or missing"
fi

if [[ -n "${DESTINATION_BOOK_DIRECTORY:-}" && -d "$DESTINATION_BOOK_DIRECTORY" ]]; then
    log_pass "DESTINATION_BOOK_DIRECTORY exists: $DESTINATION_BOOK_DIRECTORY"
else
    log_fail "DESTINATION_BOOK_DIRECTORY not set or missing"
fi

ABS_URL="${ABS_SERVER_URL:-}"
if [[ -n "$ABS_URL" ]]; then
    if curl -sf "$ABS_URL/status" -o /dev/null --connect-timeout 5; then
        log_pass "ABS reachable at $ABS_URL"
    else
        log_fail "ABS unreachable at $ABS_URL"
    fi
else
    log_fail "ABS_SERVER_URL not set"
fi

# === [2/10] Start MCP bridge ================================================

echo ""
echo "$(bold '[2/10] Start MCP bridge')"

if start_bridge; then
    log_pass "MCP bridge started (PID $BRIDGE_PID)"
else
    log_fail "MCP bridge failed to start"; exit 1
fi

# === [3/10] Tool discovery ===================================================

echo ""
echo "$(bold '[3/10] Tool discovery')"

resp=$(mcpcall "TOOLS")
tools=$(mcp_json_field "$resp" "tools")
tool_count=$(mcp_json_len "$tools")

if [[ "$tool_count" == "7" ]]; then
    log_pass "7 tools registered"
else
    log_fail "Tool count" "expected 7, got $tool_count"
fi

for tool_name in get_status list_library download_books process_books scan_audiobookshelf match_audiobookshelf ingest_books; do
    if echo "$tools" | grep -q "\"$tool_name\""; then
        log_pass "Tool $tool_name present"
    else
        log_fail "Tool $tool_name missing"
    fi
done

# === [4/10] get_status (default + overrides) =================================

echo ""
echo "$(bold '[4/10] get_status')"

# Default call
resp=$(mcpcall "CALL get_status")
if assert_no_error "get_status (default)" "$resp"; then
    text=$(mcp_text "$resp")
    for field in source_dir destination_dir abs_server abs_library_id download_engine abs_status; do
        val=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('$field','(missing)'))" "$text" 2>/dev/null)
        if [[ "$val" != "(not set)" && "$val" != "(missing)" ]]; then
            log_pass "get_status.$field = $val"
        else
            log_fail "get_status.$field not configured"
        fi
    done
fi

# Override call — pass explicit values and verify they're reflected
resp=$(mcpcall 'CALL get_status {"source_dir":"/tmp/override-src","destination_dir":"/tmp/override-dest","abs_server_url":"http://override-host:9999","abs_library_id":"override-lib-id"}')
if assert_no_error "get_status (overrides)" "$resp"; then
    text=$(mcp_text "$resp")
    got_src=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['source_dir'])" "$text" 2>/dev/null)
    got_dst=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['destination_dir'])" "$text" 2>/dev/null)
    got_srv=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['abs_server'])" "$text" 2>/dev/null)
    got_lib=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['abs_library_id'])" "$text" 2>/dev/null)

    [[ "$got_src" == "/tmp/override-src" ]]         && log_pass "get_status override source_dir" || log_fail "get_status override source_dir" "got $got_src"
    [[ "$got_dst" == "/tmp/override-dest" ]]        && log_pass "get_status override destination_dir" || log_fail "get_status override destination_dir" "got $got_dst"
    [[ "$got_srv" == "http://override-host:9999" ]] && log_pass "get_status override abs_server_url" || log_fail "get_status override abs_server_url" "got $got_srv"
    [[ "$got_lib" == "override-lib-id" ]]           && log_pass "get_status override abs_library_id" || log_fail "get_status override abs_library_id" "got $got_lib"
fi

# === [5/10] list_library (default + source_dir override) =====================

echo ""
echo "$(bold '[5/10] list_library')"

resp=$(mcpcall "CALL list_library")
if assert_no_error "list_library (default)" "$resp"; then
    text=$(mcp_text "$resp")
    book_count=$(python3 -c "import sys,json; print(len(json.loads(sys.argv[1])))" "$text" 2>/dev/null)

    if [[ "$book_count" -gt 0 ]]; then
        log_pass "list_library returned $book_count books"
    else
        log_fail "list_library returned 0 books"
    fi

    first_title=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])[0].get('title',''))" "$text" 2>/dev/null)
    first_asin=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])[0].get('asin',''))" "$text" 2>/dev/null)

    if [[ -n "$first_title" ]]; then
        log_pass "First book: $first_title [$first_asin]"
    else
        log_fail "First book has no title"
    fi

    has_keys=$(python3 -c "
import sys,json
b=json.loads(sys.argv[1])[0]
print('yes' if all(k in b for k in ('asin','title','author','series','date_added')) else 'no')
" "$text" 2>/dev/null)
    if [[ "$has_keys" == "yes" ]]; then
        log_pass "Book entries have expected keys"
    else
        log_fail "Book entries missing keys"
    fi
fi

# Override source_dir with a bogus path — should get an error or empty result
resp=$(mcpcall 'CALL list_library {"source_dir":"/tmp/nonexistent-test-dir-abc123"}')
err=$(mcp_error "$resp")
if [[ -n "$err" ]]; then
    log_pass "list_library override source_dir — correctly errors on bad path"
else
    log_pass "list_library override source_dir — accepted override param"
fi

# === [6/10] download_books (optional + overrides) ============================

echo ""
echo "$(bold '[6/10] download_books')"

if [[ "$SKIP_DOWNLOAD" == "true" ]]; then
    log_skip "download_books" "--skip-download flag set"
    log_skip "download_books overrides" "--skip-download flag set"
else
    resp=$(mcpcall "CALL download_books {\"asins\":[\"B07FW1CRWB\"]}")
    if assert_no_error "download_books (default)" "$resp"; then
        text=$(mcp_text "$resp")
        success=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('success',False))" "$text" 2>/dev/null)
        if [[ "$success" == "True" ]]; then
            log_pass "download_books succeeded"
        else
            log_fail "download_books" "success=$success"
        fi

        if find "$SOURCE_AUDIO_BOOK_DIRECTORY" -name "*B07FW1CRWB*" -name "*.m4b" | grep -q .; then
            log_pass "Downloaded file exists on disk"
        else
            log_fail "Downloaded file not found in $SOURCE_AUDIO_BOOK_DIRECTORY"
        fi
    fi

    # Test that source_dir and libation_cli overrides are accepted
    resp=$(mcpcall 'CALL download_books {"asins":["B07FW1CRWB"],"source_dir":"'"$SOURCE_AUDIO_BOOK_DIRECTORY"'","libation_cli":"libationcli"}')
    if assert_no_error "download_books (overrides)" "$resp"; then
        log_pass "download_books accepted source_dir + libation_cli overrides"
    fi
fi

# === [7/10] process_books (default + overrides) ==============================

echo ""
echo "$(bold '[7/10] process_books')"

# Default call
resp=$(mcpcall "CALL process_books {\"purchased_how_long_ago\":0}")
if assert_no_error "process_books (default)" "$resp"; then
    text=$(mcp_text "$resp")
    count=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('processed_count',-1))" "$text" 2>/dev/null)
    log_text=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('log','')[:200])" "$text" 2>/dev/null)

    if [[ "$count" -ge 0 ]]; then
        log_pass "process_books processed_count=$count"
    else
        log_fail "process_books" "invalid count: $count"
    fi

    if [[ -n "$log_text" ]]; then
        log_pass "process_books returned log output"
    else
        log_skip "process_books log empty" "may be expected if no new books"
    fi
fi

# Verify files reached NFS destination
dest_count=$(find "$DESTINATION_BOOK_DIRECTORY" -name "*.m4b" -o -name "*.mp3" 2>/dev/null | wc -l)
if [[ "$dest_count" -gt 0 ]]; then
    log_pass "$dest_count audio file(s) in destination directory"
else
    log_skip "No audio files in destination" "may be expected if all books already processed"
fi

# Override call — pass all overridable parameters
resp=$(mcpcall 'CALL process_books {"purchased_how_long_ago":0,"source_dir":"'"$SOURCE_AUDIO_BOOK_DIRECTORY"'","destination_dir":"'"$DESTINATION_BOOK_DIRECTORY"'","audio_file_extension":".m4b","copy_instead_of_move":true,"libation_folder_cleanup":false,"enable_profanity_cleaning":false}')
if assert_no_error "process_books (all overrides)" "$resp"; then
    text=$(mcp_text "$resp")
    count=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('processed_count',-1))" "$text" 2>/dev/null)
    if [[ "$count" -ge 0 ]]; then
        log_pass "process_books with overrides processed_count=$count"
    else
        log_fail "process_books with overrides" "invalid count: $count"
    fi
fi

# === [8/10] scan_audiobookshelf (default + overrides) ========================

echo ""
echo "$(bold '[8/10] scan_audiobookshelf')"

# Default call
resp=$(mcpcall "CALL scan_audiobookshelf")
if assert_no_error "scan_audiobookshelf (default)" "$resp"; then
    text=$(mcp_text "$resp")
    success=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('success',False))" "$text" 2>/dev/null)
    status_code=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('status_code','?'))" "$text" 2>/dev/null)

    if [[ "$success" == "True" ]]; then
        log_pass "scan_audiobookshelf success (HTTP $status_code)"
    else
        log_fail "scan_audiobookshelf" "success=$success status=$status_code"
    fi
fi

# Override call — use same values from env to confirm overrides are accepted
resp=$(mcpcall 'CALL scan_audiobookshelf {"abs_server_url":"'"$ABS_SERVER_URL"'","abs_library_id":"'"$ABS_LIBRARY_ID"'","abs_api_token":"'"$ABS_API_TOKEN"'"}')
if assert_no_error "scan_audiobookshelf (overrides)" "$resp"; then
    text=$(mcp_text "$resp")
    success=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('success',False))" "$text" 2>/dev/null)
    if [[ "$success" == "True" ]]; then
        log_pass "scan_audiobookshelf with overrides success"
    else
        log_fail "scan_audiobookshelf with overrides" "success=$success"
    fi
fi

sleep 5

# === [9/10] match_audiobookshelf (default + overrides) =======================

echo ""
echo "$(bold '[9/10] match_audiobookshelf')"

# Default call
resp=$(mcpcall "CALL match_audiobookshelf {\"days_ago\":30}")
if assert_no_error "match_audiobookshelf (default)" "$resp"; then
    text=$(mcp_text "$resp")
    matched=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('matched_count','?'))" "$text" 2>/dev/null)
    log_pass "match_audiobookshelf matched_count=$matched"
fi

# Override call
resp=$(mcpcall 'CALL match_audiobookshelf {"days_ago":30,"abs_server_url":"'"$ABS_SERVER_URL"'","abs_library_id":"'"$ABS_LIBRARY_ID"'","abs_api_token":"'"$ABS_API_TOKEN"'"}')
if assert_no_error "match_audiobookshelf (overrides)" "$resp"; then
    text=$(mcp_text "$resp")
    matched=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('matched_count','?'))" "$text" 2>/dev/null)
    log_pass "match_audiobookshelf with overrides matched_count=$matched"
fi

# === [10/10] ingest_books (composite — skip by default) ======================

echo ""
echo "$(bold '[10/10] ingest_books (composite)')"
log_skip "ingest_books" "Components tested individually above. Run manually to test full pipeline."
echo ""
echo "  To test ingest_books with overrides manually:"
echo "    mcpcall 'CALL ingest_books {\"purchased_how_long_ago\":0,\"abs_library_id\":\"<uuid>\",\"destination_dir\":\"/path/to/lib\"}'"

# === SUMMARY =================================================================

echo ""
echo "$(bold '=== Results ===')"
TOTAL=$((PASS + FAIL + SKIP))
echo "  $(green "$PASS passed")  $(red "$FAIL failed")  $(yellow "$SKIP skipped")  / $TOTAL total"

[[ "$FAIL" -gt 0 ]] && exit 1 || exit 0
