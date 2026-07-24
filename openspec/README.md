# OpenSpec — Behavioral Specs for Software Projects

This directory holds living behavioral specifications for software projects in this repo. Specs accumulate across plans, giving agents behavioral context before they touch code.

---

## Structure

```
openspec/
├── specs/          ← current behavioral truth (accretes over time, git-tracked)
│   └── <capability>/
│       └── spec.md
└── changes/        ← active proposals (ephemeral per plan, gitignored when large)
    └── <change-id>/
        ├── proposal.md
        ├── design.md
        ├── tasks.md
        └── specs/  ← delta specs for this change
```

---

## How It Works

No CLI required. Agents follow the workflow documented in `agent_planning/addenda/software.md §S1`.

**Planning phase:**
1. Check if `openspec/specs/<capability>/` exists
2. If it does, read the spec as additional PROJECT CONTEXT before authoring tasks
3. Propose changes by creating `openspec/changes/<change-id>/` with proposal.md and delta specs

**Execution phase:**
4. Agent follows plan tasks
5. Agent reads `openspec/changes/<change-id>/tasks.md` alongside plan tasks (if it exists)

**Archive (final task):**
6. Merge delta specs from `openspec/changes/<change-id>/specs/` into `openspec/specs/<capability>/spec.md`
7. Delete or archive `openspec/changes/<change-id>/` (it is now redundant)

---

## Spec Format

```markdown
# <capability> Specification

## Purpose
[One paragraph: what this capability does and why it exists.]

## Requirements

### Requirement: <name>
The system SHALL <behavior>.

#### Scenario: <description>
- GIVEN <precondition>
- WHEN <action>
- THEN <expected outcome>
- AND <additional assertion>
```

---

## When to Create a Spec

Create a spec for a codebase when:
- This is the 2nd or later plan touching the same codebase
- The codebase exposes a public interface (MCP tools, CLI subcommands, API endpoints)
- The codebase has behavioral invariants that future plans must not break

Skip for: one-off scripts, purely additive changes with no behavioral contract, non-software plans.

---

## Current Specs

| Capability | Spec file | Last updated |
|------------|-----------|--------------|
| `pipeline` | `specs/pipeline/spec.md` | 2026-07-23 (Plan 1: Core Polish) |
| `ingestion-mcp` | `specs/ingestion-mcp/spec.md` | 2026-07-23 (Plan 1: Core Polish) |

---

## Cursor Native Integration

If using Cursor, OpenSpec slash commands are available natively:
- `/opsx:explore` — analyze codebase and refine a fuzzy idea
- `/opsx:propose` — generate proposal, design, delta specs, and tasks
- `/opsx:apply` — execute the tasks
- `/opsx:archive` — merge delta specs into main specs/

These commands work without installing the npm package in Cursor's native integration mode.
