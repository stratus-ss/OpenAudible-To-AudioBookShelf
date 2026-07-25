#!/usr/bin/env python3
"""Personal-infrastructure leak detector.

Scans skills, docs, and source files for hardcoded hostnames, IP addresses,
and library UUIDs that should not be committed to a portable/public repo.

Run as a pre-commit hook (via .pre-commit-config.yaml) or standalone:

    # Scan all files passed as args (or use --staged/-)
    scripts/check-infra-leaks.py file1.md file2.py ...
    scripts/check-infra-leaks.py --staged
    scripts/check-infra-leaks.py -                  # read paths from stdin

Exits 0 if clean, 1 if leaks found. Use --strict to also flag private RFC1918
IPs.

Allowlist (files that legitimately contain hostnames/UUIDs as config values):
  - libraries.yaml*, arguments.yaml (config templates)
  - abs_mcp/.env, .env, .env.example (runtime configuration)
  - abs_mcp/libraries.example.yaml, abs_mcp/libraries.yaml.example
  - agent_planning/** (planning artifacts may quote infra specifics)
  - scripts/check-infra-leaks.py (this file documents the patterns)
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hostname patterns — what we're trying to keep OUT of the repo
HOSTNAME_PATTERNS = [
    (r"\b[a-zA-Z0-9_-]+\.x86experts\.com\b", "*.x86experts.com hostname"),
    (r"\b[a-zA-Z0-9_-]+\.x86innovations\.com\b", "*.x86innovations.com hostname"),
]

# Library UUID pattern — only flagged in markdown/yaml context that suggests
# it's a library_id, not a generic UUID.
LIBRARY_UUID_PATTERN = (
    r'(?:library_id|libraryId|libraryID|abs_library_id|absLibraryId)["\s:=]+'
    r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})'
)
# Generic UUID detection in non-config files
UUID_PATTERN = r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b"

# Optional: RFC1918 private IPs (off by default, --strict enables)
IP_PATTERNS_STRICT = [
    (r"\b(?:10|127|172\.(?:1[6-9]|2[0-9]|3[01])|192\.168)\.\d+\.\d+\b", "private IP"),
]

# Files that legitimately contain infrastructure references as config values.
# Add to this list when you create new config templates.
ALLOWLIST = {
    # Top-level config
    "libraries.yaml",
    "libraries.yaml.example",
    "arguments.yaml",
    # abs_mcp runtime config
    "abs_mcp/.env",
    "abs_mcp/.env.example",
    "abs_mcp/libraries.example.yaml",
    "abs_mcp/libraries.yaml.example",
    "abs_mcp/libraries.yaml",
    "abs_mcp/audiobook-ingestion-mcp.service",
    # Planning/working artifacts (not user-facing docs)
    "agent_planning/",
    "zed_plans/",
    # This file documents the patterns, so it references them
    "scripts/check-infra-leaks.py",
}

# Files where we additionally allow *any* UUID (not just library_id context).
# These are config-like files that need real UUIDs.
UUID_ALLOWLIST = ALLOWLIST | {
    "libraries.yaml",
    "libraries.yaml.example",
    "abs_mcp/libraries.example.yaml",
    "abs_mcp/libraries.yaml.example",
    "abs_mcp/.env",
    "abs_mcp/.env.example",
    # Test files commonly contain mock UUIDs as fixture data
    "tests/",
    "tests/mcp/",
}


def is_allowlisted(path: str) -> bool:
    p = path.replace("\\", "/")
    for allowed in ALLOWLIST:
        if allowed.endswith("/"):
            if p.startswith(allowed):
                return True
        else:
            # Match against basename or full path match
            if p == allowed or p.endswith("/" + allowed):
                return True
    return False


def is_uuid_allowlisted(path: str) -> bool:
    p = path.replace("\\", "/")
    for allowed in UUID_ALLOWLIST:
        if allowed.endswith("/"):
            if p.startswith(allowed):
                return True
        else:
            if p == allowed or p.endswith("/" + allowed):
                return True
    return False


def scan_file(path: Path, check_ips: bool = False, check_uuids: bool = True) -> list:
    """Scan a single file for leaks. Returns list of (line_no, pattern_name, line_text)."""
    issues = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):
        return issues

    rel = str(path.relative_to(REPO_ROOT))

    # Hostname patterns
    for pattern, name in HOSTNAME_PATTERNS:
        for m in re.finditer(pattern, text):
            line_no = text.count("\n", 0, m.start()) + 1
            line_text = text.splitlines()[line_no - 1] if line_no <= len(text.splitlines()) else m.group(0)
            issues.append((line_no, name, line_text.strip()))

    # IP patterns (only with --strict)
    if check_ips:
        for pattern, name in IP_PATTERNS_STRICT:
            for m in re.finditer(pattern, text):
                line_no = text.count("\n", 0, m.start()) + 1
                line_text = text.splitlines()[line_no - 1] if line_no <= len(text.splitlines()) else m.group(0)
                issues.append((line_no, name, line_text.strip()))

    # Library UUID pattern (any context)
    if check_uuids and not is_uuid_allowlisted(rel):
        for m in re.finditer(LIBRARY_UUID_PATTERN, text):
            line_no = text.count("\n", 0, m.start()) + 1
            line_text = text.splitlines()[line_no - 1] if line_no <= len(text.splitlines()) else m.group(0)
            issues.append((line_no, "library_id UUID", line_text.strip()))

        # Generic UUIDs (only in md/yaml/json context to reduce false positives)
        if path.suffix in (".md", ".mdc", ".yaml", ".yml", ".json"):
            for m in re.finditer(UUID_PATTERN, text):
                line_no = text.count("\n", 0, m.start()) + 1
                line_text = text.splitlines()[line_no - 1] if line_no <= len(text.splitlines()) else m.group(0)
                # Skip if it's clearly a UUIDs.json registry file
                if "/uuid" in rel or "schema" in rel.lower():
                    continue
                issues.append((line_no, "UUID (verify if library_id)", line_text.strip()))

    return issues


def get_staged_files() -> list:
    """Get list of staged file paths from `git diff --cached`."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, cwd=REPO_ROOT
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Personal-infrastructure leak detector")
    ap.add_argument("paths", nargs="*", help="Files to scan (default: staged or none)")
    ap.add_argument("--staged", action="store_true", help="Scan staged files")
    ap.add_argument("--stdin", action="store_true", help="Read paths from stdin (use '-' as the path-source flag)")
    ap.add_argument("--strict", action="store_true", help="Also flag RFC1918 private IPs")
    ap.add_argument("--allow-inline", action="store_true",
                    help="Allow any line with an 'allow-infra-leak: <reason>' comment (escape hatch)")
    args = ap.parse_args()

    if args.staged:
        files = get_staged_files()
    elif args.paths:
        files = args.paths
    elif args.stdin:
        files = [line.strip() for line in sys.stdin if line.strip()]
    else:
        ap.error("provide paths, --staged, or --stdin")

    total_issues = 0
    scanned = 0
    for rel in files:
        path = REPO_ROOT / rel
        if not path.exists() or not path.is_file():
            continue
        # Skip binary/extensionless files
        if path.suffix not in (
            ".md", ".mdc", ".py", ".sh", ".yaml", ".yml", ".json",
            ".toml", ".cfg", ".ini", ".txt", ".example",
        ):
            continue
        scanned += 1
        issues = scan_file(path, check_ips=args.strict)
        if not issues:
            continue

        # Apply escape hatch
        if args.allow_inline:
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_no, name, line_text in list(issues):
                # Look at surrounding lines for an opt-out comment
                lines = text.splitlines()
                window = lines[max(0, line_no - 2):line_no + 1]
                if any("allow-infra-leak" in ln for ln in window):
                    issues.remove((line_no, name, line_text))

        if issues:
            if total_issues == 0:
                print("\n❌ Personal-infrastructure leaks found:\n")
            print(f"  {rel}:")
            for line_no, name, line_text in issues:
                print(f"    line {line_no}: [{name}] {line_text[:120]}")
            total_issues += len(issues)

    if total_issues > 0:
        print(f"\n{total_issues} issue(s) found across {scanned} file(s).")
        print()
        print("Fix by replacing hardcoded references with one of:")
        print("  - ${ENV_VAR} substitution (e.g., ${REMOTE_WHISPER_URL})")
        print("  - <placeholder-name> syntax (e.g., <whisper-backend-url>)")
        print("  - generic role names (e.g., 'the Whisper backend')")
        print()
        print("To bypass (use sparingly), add a comment on the line above:")
        print("  # allow-infra-leak: legitimate config example")
        print()
        return 1

    if scanned > 0:
        print(f"✓ Scanned {scanned} file(s), no personal-infra leaks found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
