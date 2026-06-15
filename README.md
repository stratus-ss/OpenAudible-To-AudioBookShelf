# Import-To-AudioBookShelf

Automates moving audiobook files from OpenAudible or Libation to an organized folder structure and updating [AudioBookShelf](https://www.audiobookshelf.org/) accordingly.

> [!IMPORTANT]
> This project is being renamed from `OpenAudible-To-AudioBookShelf` to **`Import-To-AudioBookShelf`**. Update clone paths and remotes to use the new name.

## Features

- **Step-based pipeline:** `scan` → `download` → `export` → `organize` → `scan-abs` → `match`
- **Multiple sources:** Works with both OpenAudible and Libation (CLI); MCP requires **Libation only**
- **Metadata mapping:** Normalizes OpenAudible and Libation JSON schemas to a common format
- **AudioBookShelf integration:** Triggers library scans and matches books to Audible metadata
- **MCP server:** [FastMCP server](abs-mcp/README.md) exposes pipeline tools for AI agents
- **Profanity cleaning:** Optional MonkeyPlug integration for Whisper-based profanity removal

## Quick Start

```bash
# 1. Install
git clone https://github.com/stratus-ss/Import-To-AudioBookShelf.git
cd Import-To-AudioBookShelf
pip install -r requirements.txt

# 2. Configure (Libation recommended for MCP)
cp arguments.yaml.example arguments.yaml
# Edit with your ABS URL, token, library ID, and paths

# 3. Run full pipeline
openaudible-to-abs --yaml-arguments arguments.yaml

# 4. Or run step-by-step
openaudible-to-abs --step export --yaml-arguments arguments.yaml
openaudible-to-abs --step scan-abs --yaml-arguments arguments.yaml
```

## Documentation

| Document | Purpose |
|----------|---------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Module map, pipeline, layer boundaries |
| [CODEFLOW.md](CODEFLOW.md) | Runtime flow diagrams |
| [AGENTS.md](AGENTS.md) | AI agent rules and MCP usage |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Full CLI flag and path reference |
| [abs-mcp/README.md](abs-mcp/README.md) | MCP server setup and tools |

## Pipeline Steps

| Step | Description |
|------|-------------|
| `scan` | Read books from OpenAudible or Libation JSON |
| `download` | Download books (OpenAudible only) |
| `export` | Move/copy files to Author/Series/Title structure |
| `organize` | Restructure directories |
| `scan-abs` | Trigger AudioBookShelf library rescan |
| `match` | Match books to Audible metadata in ABS |

## MCP Server (Libation Only)

The `abs-mcp/` FastMCP server exposes pipeline tools for AI agents:

```bash
cd abs-mcp
python mcp_server.py
```

Tools: `list_abs_library`, `scan_abs_library`, `get_tool_metrics`, `query_tool_metrics_history`.

Requires Libation (not OpenAudible) due to `libationcli` CLI dependency.

## Testing

```bash
pytest tests/ -v
```

## License

AGPL-3.0 -- see [LICENSE](LICENSE)
