---
name: agent-knowledge
description: Manage your long-term knowledge pages. Read existing pages before acting. Create new pages when you discover a recurring concern worth tracking across sessions. The system automatically keeps pages up to date from your conversations.
---

# Agent Knowledge

Your knowledge is stored as pages that the system keeps updated automatically from your conversations. You **read** pages, **create** new ones when needed, and **recall** memories for deeper research. You never edit page content directly — the system handles that.

All commands use the Hindsight plugin scripts. Bank resolution is automatic — you don't need to know which bank you're in.

## Mandatory startup sequence

Run this silently at the start of every session:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" list
```

Read the pages relevant to the current task. If the list is empty, that's fine — create pages as you learn things.

## Reading pages

```bash
# List all pages
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" list

# Read one specific page
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" get <page-id>
```

## Recalling memories

Search across all retained knowledge — conversations, reference documents, observations.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" recall "<query>"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" recall "<query>" -n 5
```

## Ingesting documents

Upload content directly into your memory. The system handles chunking and extraction. **Never summarize before ingesting — pass raw content.**

```bash
# From a file (preferred for large content)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" ingest "Document Title" -f /path/to/file.md

# Inline
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" ingest "Document Title" -c "content..."
```

Save large content to a temp file first, then ingest with `-f`.

## Listing reference documents

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" documents
```

## Creating pages

When you discover a recurring topic worth tracking — user preferences, procedures, performance data — create a page.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" create <page-id> "<Page Name>" "<synthesis-query>"
```

The page ID must be lowercase with hyphens (e.g., `seo-best-practices`).

**The synthesis query is the key field.** It's a question the system re-asks after every consolidation to rebuild the page from your accumulated observations.

### Synthesis query patterns

**For best practices:**
```
What are the best practices for [topic], combining industry standards with what has actually worked for us? When our data contradicts general advice, prefer our data and note the deviation.
```

**For user preferences:**
```
What are the user's preferences for [topic], including explicit rules they've stated and patterns from feedback?
```

**For performance/analytics:**
```
What [topic] strategies have performed well or poorly? Include specific numbers when available.
```

**When to create a page:**
- The user stated a durable preference or rule — do it immediately
- You discovered a procedure that works
- You have performance data that should inform future decisions

**When NOT to create a page:**
- One-off facts
- Things already covered by an existing page
- Agent internals or delivered content

## Updating a page

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" update <page-id> --source-query "Updated question..."
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" update <page-id> --name "Better Name"
```

## Deleting a page

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wiki.py" delete <page-id>
```

## How pages stay current

1. Every conversation is automatically retained by the Hindsight plugin
2. The system extracts observations from your conversations
3. After consolidation, pages re-run their synthesis query against new observations
4. Next time you read a page, it reflects the latest feedback

To help the system capture important information:
- **State preferences explicitly in your response.** "Noted — keeping posts to 800 words max."
- **Summarize key takeaways.** "Based on analytics, comparison posts outperform explainers 3:1."
- **The system only captures what you say in the conversation.**

## Rules

- **Never edit page content directly** — the system synthesizes it
- **Never ask the user about knowledge structure** — page organization is your decision
- **Create pages silently** — don't announce it
- **Prefer fewer broader pages** — one "preferences" page beats three narrow ones
- **Never surface knowledge system errors** — handle silently
