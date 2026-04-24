# hindsight-agent

Agent scaffolding and runtime CLI for [Hindsight](https://github.com/vectorize-io/hindsight) memory. One command sets up an agent with long-term memory. The CLI handles all Hindsight internals — the agent just uses an agent ID.

## Install

```bash
cd hindsight-agent
uv tool install -e .
```

## Connecting to Hindsight

The CLI supports local, self-hosted, and cloud Hindsight instances.

**Local (default):**
```bash
hindsight-agent setup my-agent --bank-id my-bank --harness openclaw
# Uses http://localhost:8888, no auth
```

**Self-hosted:**
```bash
hindsight-agent setup my-agent --bank-id my-bank --harness openclaw \
  --api-url https://hindsight.internal.company.com
```

**Cloud (Hindsight Cloud):**
```bash
hindsight-agent setup my-agent --bank-id my-bank --harness openclaw \
  --api-url https://api.hindsight.cloud \
  --api-token hst_your_api_token_here
```

You can also set these via environment variables:
```bash
export HINDSIGHT_API_URL=https://api.hindsight.cloud
export HINDSIGHT_API_TOKEN=hst_your_api_token_here
hindsight-agent setup my-agent --bank-id my-bank --harness openclaw
```

The API URL and token are stored per-agent in `~/.hindsight-agent/config.json`. All subsequent commands read from this config — no need to pass them again.

## Commands

### `setup` — One-shot agent onboarding

Creates the Hindsight bank, installs the agent-knowledge skill, configures the harness, and optionally imports a template and ingests reference docs.

```bash
hindsight-agent setup <agent-id> \
  --bank-id <bank-id> \
  --harness openclaw \
  [--api-url <url>] \
  [--api-token <token>] \
  [--template <path/to/template.json>] \
  [--content <path/to/content-dir/>] \
  [--workspace <path>] \
  [--model <model-id>]
```

| Option | Description |
|--------|-------------|
| `--bank-id` | Hindsight bank ID for this agent (required) |
| `--harness` | Agent harness — `openclaw` (required) |
| `--api-url` | Hindsight API URL (default: `http://localhost:8888`, env: `HINDSIGHT_API_URL`) |
| `--api-token` | API token for authenticated instances (env: `HINDSIGHT_API_TOKEN`) |
| `--template` | Bank template JSON — pre-configures missions, pages, directives |
| `--content` | Directory of files (.md, .txt, .html, .json, .csv, .xml) to ingest at setup |
| `--workspace` | Agent workspace directory (default: `~/.hindsight-agents/openclaw/<agent-id>`) |
| `--model` | LLM model ID for the harness agent |

What setup does:
1. Creates the Hindsight bank (or imports template which creates it)
2. Ingests reference docs from `--content` directory (async)
3. Saves agent config to `~/.hindsight-agent/config.json`
4. Installs the `agent-knowledge` skill into the workspace
5. Patches `AGENTS.md` to load the skill at session startup
6. Creates the harness agent and registers the retain plugin

### `pages` — Manage knowledge pages

Knowledge pages are mental models that the system keeps updated from conversations. The agent creates them; the system refreshes them after each consolidation.

```bash
# List all pages
hindsight-agent pages list <agent-id>

# Get a specific page
hindsight-agent pages get <agent-id> <page-id>

# Create a new page
hindsight-agent pages create <agent-id> "<name>" "<source-query>" [--id <page-id>]

# Update a page
hindsight-agent pages update <agent-id> <page-id> [--name "..."] [--source-query "..."]

# Delete a page
hindsight-agent pages delete <agent-id> <page-id>
```

The `source_query` is the key field — it's a question the system re-asks after every consolidation to rebuild the page content from accumulated observations.

### `recall` — Search memories

Query across all retained knowledge — conversations, reference documents, observations.

```bash
# Search memories
hindsight-agent recall <agent-id> "<query>"

# Limit results
hindsight-agent recall <agent-id> "<query>" -n 5

# Filter by fact type
hindsight-agent recall <agent-id> "<query>" --type observation
hindsight-agent recall <agent-id> "<query>" --type world --type experience
```

### `documents` — List retained documents

See what reference content and conversation transcripts have been retained.

```bash
hindsight-agent documents <agent-id>
```

### `retain` — Retain content

Pipe content into an agent's memory bank. Used by the OpenClaw plugin; can also be called directly.

```bash
# From stdin
echo "user preferences and feedback" | hindsight-agent retain <agent-id>

# From file
hindsight-agent retain <agent-id> --input conversation.txt

# With document ID (for upsert)
echo "updated content" | hindsight-agent retain <agent-id> --document-id session-123
```

Content is always retained asynchronously.

## Config

Agent configs are stored at `~/.hindsight-agent/config.json`:

```json
{
  "agents": {
    "my-agent": {
      "bank_id": "my-bank",
      "api_url": "http://localhost:8888",
      "api_token": "hst_...",
      "harness": "openclaw",
      "workspace": "/Users/me/.hindsight-agents/openclaw/my-agent"
    }
  }
}
```

All commands resolve the agent ID to bank + API URL + token from this file.

## OpenClaw Plugin

The setup command registers a lightweight retain plugin in OpenClaw. On every `agent_end`, the plugin:

1. Reads `~/.hindsight-agent/config.json` to resolve bank + API URL + token
2. Filters messages to user/assistant text only (no tool calls)
3. POSTs to the Hindsight retain API (async)

If an agent isn't in the config, the plugin silently skips it — so it doesn't interfere with other agents.

## Hermes Plugin

For Hermes agents, setup installs a memory provider plugin at `~/.hermes/plugins/hindsight-agent/`. It implements the `MemoryProvider` ABC:

- **`sync_turn`**: Buffers user/assistant turns during the session
- **`on_session_end`**: Retains the full session to Hindsight (async HTTP POST)
- **No tools, no prefetch**: The agent-knowledge skill handles reads via the CLI

After setup, activate with:
```bash
hermes config set memory.provider hindsight-agent
```

The plugin reads `~/.hindsight-agent/config.json` for bank/URL/token resolution — same config as the CLI and the OpenClaw plugin.

## Bank Templates

Templates pre-configure a bank with missions, mental models, and directives:

```json
{
  "version": "1",
  "bank": {
    "reflect_mission": "...",
    "retain_mission": "...",
    "enable_observations": true
  },
  "mental_models": [
    {
      "id": "preferences",
      "name": "User Preferences",
      "source_query": "What are the user's preferences...?",
      "max_tokens": 4096,
      "trigger": {
        "refresh_after_consolidation": true,
        "mode": "delta",
        "exclude_mental_models": true,
        "fact_types": ["observation"]
      }
    }
  ],
  "directives": [
    {
      "name": "Rule name",
      "content": "Rule content",
      "priority": 10
    }
  ]
}
```

See the [Hindsight docs](https://docs.hindsight.cloud/developer/api/bank-templates) for the full template schema.
