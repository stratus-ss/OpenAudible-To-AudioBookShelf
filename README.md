# Import-To-AudioBookShelf

Automates moving audiobook files from OpenAudible or Libation to an organized folder structure and updating [AudioBookShelf](https://www.audiobookshelf.org/) accordingly.

## Features

- **Step-based pipeline:** `scan` → `download` → `export` → `organize` → `scan-abs` → `match`
- **Multiple sources:** Works with both OpenAudible and Libation (CLI); MCP requires **Libation only**
- **Metadata mapping:** Normalizes OpenAudible and Libation JSON schemas to a common format
- **AudioBookShelf integration:** Triggers library scans and matches books to Audible metadata
- **MCP server:** [FastMCP server](abs-mcp/README.md) exposes pipeline tools for AI agents
- **Profanity cleaning:** Optional MonkeyPlug integration for Whisper-based profanity removal

## Quick Start

**Choose your deployment path:**

- **Container (recommended for new deploys):** the MCP image bundles Libation + .NET + Python together. See [abs-mcp/README.md](abs-mcp/README.md) "Docker Deployment" section. ~10 min setup, no host installs beyond Docker.
- **Python (bare-metal):** follow the commands below. ~30 min setup, requires native Libation install.

### Container path (MCP only)

```bash
# Pull and start the combined Libation + MCP image
docker pull ghcr.io/stratus-ss/mcps/audiobook-ingestion-mcp:latest
docker compose -f docker/docker-compose.yml up -d  # uses docker/docker-compose.yml + docker/docker-compose.override.yml
```

The image contains **no credentials, library UUIDs, or environment files** — you MUST mount your own `.env` and `libraries.yaml` from the host. See [abs-mcp/README.md](abs-mcp/README.md) Step 5c for full setup.

### Python path (CLI + MCP)

```bash
# 1. Install
git clone https://github.com/stratus-ss/Import-To-AudioBookShelf.git
cd Import-To-AudioBookShelf
pip install -r requirements.txt

# 2. Configure (Libation recommended for MCP)
cp arguments.yaml arguments.yaml.bak 2>/dev/null  # back up any existing config
# Edit arguments.yaml with your ABS URL, token, library ID, and paths

# 3. Run full pipeline
openaudible-to-abs --yaml-arguments arguments.yaml

# 4. Or run step-by-step
openaudible-to-abs --step export --yaml-arguments arguments.yaml
openaudible-to-abs --step scan-abs --yaml-arguments arguments.yaml
```

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module map, pipeline, layer boundaries |
| [docs/CODEFLOW.md](docs/CODEFLOW.md) | Runtime flow diagrams |
| [docs/AGENTS.md](docs/AGENTS.md) | AI agent rules, MCP usage, deployment options |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Full CLI flag and path reference |
| [abs-mcp/README.md](abs-mcp/README.md) | MCP server setup, tools, Docker deployment |

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

The `abs-mcp/` FastMCP server exposes 20+ pipeline tools for AI agents — see [abs-mcp/README.md](abs-mcp/README.md) for the full list.

**Run locally (Python):**

```bash
cd abs-mcp
python mcp_server.py
```

**Run as container (recommended):**

```bash
docker pull ghcr.io/stratus-ss/mcps/audiobook-ingestion-mcp:latest
docker compose -f docker/docker-compose.yml up -d
```

For full container setup including volume mounts, env vars, and IDE integration, see [abs-mcp/README.md](abs-mcp/README.md) "Docker Deployment" section.

Requires Libation (not OpenAudible) due to `libationcli` CLI dependency.

## Testing

```bash
pytest tests/ -v
```

## License

AGPL-3.0 -- see [LICENSE](LICENSE)
