#!/usr/bin/env node
/**
 * hindsight-agent-setup — set up self-learning wiki for AI agents.
 *
 * npx @vectorize-io/hindsight-agent-setup <harness> <agent-id> [options]
 *
 * Supports: openclaw, hermes, claude-code, codex
 *
 * Reads the harness config to resolve Hindsight API + bank ID, then:
 * 1. Health check
 * 2. Import bank template (if provided)
 * 3. Ingest content files (if provided)
 * 4. Install agent-knowledge skill
 * 5. Patch startup file to auto-load skill
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync } from "fs";
import { join, resolve, extname } from "path";
import { homedir } from "os";
import { execSync } from "child_process";

// ── Skill template ──────────────────────────────────────

const SKILL_MD = `---
name: agent-knowledge
description: Manage your long-term knowledge pages. Read pages before acting. Create new pages for recurring topics. The system keeps pages updated from your conversations.
---

# Agent Knowledge

Use the \`hindsight_wiki_*\` tools to manage your knowledge pages.

## Startup

At session start, call \`hindsight_wiki_list\` to read your pages.

## Tools

- \`hindsight_wiki_list\` — list all pages
- \`hindsight_wiki_get(page_id)\` — read a page
- \`hindsight_wiki_create(page_id, name, source_query)\` — create a page
  Always set: trigger_refresh_after_consolidation=true, trigger_mode="delta",
  trigger_exclude_mental_models=true, trigger_fact_types=["observation"], max_tokens=4096
- \`hindsight_wiki_update(page_id, name?, source_query?)\` — update a page
- \`hindsight_wiki_delete(page_id)\` — delete a page
- \`hindsight_wiki_recall(query)\` — search memories
- \`hindsight_wiki_ingest(title, content)\` — upload a document (never summarize, pass raw)

## Creating pages

Create when: user states a durable preference, you find a working procedure, you have performance data.
The source_query is a question the system re-asks to rebuild the page from observations.

Source query patterns:
- Best practices: "What are the best practices for [topic], preferring our data over industry advice?"
- Preferences: "What are the user's preferences for [topic]?"
- Performance: "What [topic] strategies have performed well or poorly? Include numbers."

## Rules

- Never edit page content directly — the system synthesizes it
- Create pages silently — don't announce it
- Prefer fewer broader pages
- State preferences explicitly in responses so the system captures them
`;

// ── HTTP helper ─────────────────────────────────────────

async function api(
  baseUrl: string,
  path: string,
  method: string,
  body?: unknown,
  token?: string,
): Promise<unknown> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const opts: RequestInit = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${baseUrl}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status} on ${method} ${path}: ${text.slice(0, 200)}`);
  }
  return resp.json().catch(() => ({}));
}

// ── Harness resolvers ───────────────────────────────────

interface HarnessInfo {
  apiUrl: string;
  bankId: string;
  apiToken?: string;
  workspaceDir: string;
  startupFile?: string; // file to patch for auto-loading skill
  startupPatch?: string; // text to inject
}

function resolveOpenclaw(agentId: string): HarnessInfo {
  const configPath = join(homedir(), ".openclaw", "openclaw.json");
  if (!existsSync(configPath)) throw new Error("OpenClaw config not found at ~/.openclaw/openclaw.json");
  const config = JSON.parse(readFileSync(configPath, "utf-8"));
  const pc = config.plugins?.entries?.["hindsight-openclaw"]?.config || {};

  const apiUrl = pc.hindsightApiUrl || `http://localhost:${pc.apiPort || 9077}`;
  const apiToken = pc.hindsightApiToken || undefined;

  // Bank resolution (same logic as deriveBankId)
  let bankId: string;
  if (pc.dynamicBankId === false && pc.bankId) {
    bankId = pc.bankId;
  } else {
    const granularity: string[] = pc.dynamicBankGranularity || ["agent", "channel", "user"];
    const fieldMap: Record<string, string> = { agent: agentId, channel: "unknown", user: "anonymous", provider: "unknown" };
    const base = granularity.map((f) => encodeURIComponent(fieldMap[f] || "unknown")).join("::");
    bankId = pc.bankIdPrefix ? `${pc.bankIdPrefix}-${base}` : base;
  }

  // Find workspace
  const agents = config.agents || {};
  let workspace = join(homedir(), ".hindsight-agents", "openclaw", agentId);
  for (const [, agentConf] of Object.entries(agents) as [string, any][]) {
    if (agentConf.name === agentId && agentConf.workspaceDir) {
      workspace = agentConf.workspaceDir;
      break;
    }
  }

  return {
    apiUrl, bankId, apiToken, workspaceDir: workspace,
    startupFile: join(workspace, "AGENTS.md"),
    startupPatch: '5. Read `skills/agent-knowledge/SKILL.md` and **execute its mandatory startup sequence**',
  };
}

function resolveHermes(agentId: string): HarnessInfo {
  const hermesHome = join(homedir(), ".hermes");
  // Read hermes config for the profile
  const profileDir = agentId === "default" ? hermesHome : join(hermesHome, "profiles", agentId);
  let configPath = join(profileDir, "config.yaml");
  // For bank, read the hindsight memory provider config
  // Simple: use static bank = agentId
  const bankId = agentId;
  const apiUrl = "http://localhost:8888"; // default, overridable via --api-url

  return {
    apiUrl, bankId, workspaceDir: hermesHome,
    startupFile: existsSync(join(profileDir, "SOUL.md")) ? join(profileDir, "SOUL.md") : undefined,
    startupPatch: "## Mandatory: Agent Knowledge\n\nAt the start of every session, load the `agent-knowledge` skill and execute its mandatory startup sequence.",
  };
}

function resolveClaudeCode(_agentId: string): HarnessInfo {
  const settingsPath = join(homedir(), ".claude", "plugins", "hindsight-claude-code", "settings.json");
  let apiUrl = "http://localhost:8888";
  let bankId = "claude_code";
  let apiToken: string | undefined;

  if (existsSync(settingsPath)) {
    const settings = JSON.parse(readFileSync(settingsPath, "utf-8"));
    if (settings.hindsightApiUrl) apiUrl = settings.hindsightApiUrl;
    if (settings.bankId) bankId = settings.bankId;
    if (settings.hindsightApiToken) apiToken = settings.hindsightApiToken;
  }

  return { apiUrl, bankId, apiToken, workspaceDir: join(homedir(), ".claude") };
}

function resolveHarness(harness: string, agentId: string): HarnessInfo {
  switch (harness) {
    case "openclaw": return resolveOpenclaw(agentId);
    case "hermes": return resolveHermes(agentId);
    case "claude-code": return resolveClaudeCode(agentId);
    default: throw new Error(`Unknown harness: ${harness}. Supported: openclaw, hermes, claude-code`);
  }
}

// ── Main ────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);

  if (args.length < 2 || args.includes("--help")) {
    console.log(`Usage: npx @vectorize-io/hindsight-agent-setup <harness> <agent-id> [options]

Harnesses: openclaw, hermes, claude-code

Options:
  --template <path>   Bank template JSON to import
  --content <dir>     Directory of files to ingest (.md, .txt, .html, .json)
  --api-url <url>     Override Hindsight API URL
  --api-token <token> Override API token`);
    process.exit(0);
  }

  const harness = args[0];
  const agentId = args[1];
  let templatePath: string | undefined;
  let contentDir: string | undefined;
  let apiUrlOverride: string | undefined;
  let apiTokenOverride: string | undefined;

  for (let i = 2; i < args.length; i++) {
    if (args[i] === "--template" && args[i + 1]) templatePath = args[++i];
    else if (args[i] === "--content" && args[i + 1]) contentDir = args[++i];
    else if (args[i] === "--api-url" && args[i + 1]) apiUrlOverride = args[++i];
    else if (args[i] === "--api-token" && args[i + 1]) apiTokenOverride = args[++i];
  }

  // Resolve harness config
  const info = resolveHarness(harness, agentId);
  const apiUrl = apiUrlOverride || info.apiUrl;
  const apiToken = apiTokenOverride || info.apiToken;
  const bankId = info.bankId;

  console.log(`Setting up wiki for '${agentId}' on ${harness}`);
  console.log(`  API:       ${apiUrl}`);
  console.log(`  Bank:      ${bankId}`);
  console.log(`  Workspace: ${info.workspaceDir}`);
  console.log();

  // Health check
  try {
    await api(apiUrl, "/health", "GET", undefined, apiToken);
  } catch {
    console.error(`Error: Cannot reach Hindsight at ${apiUrl}`);
    process.exit(1);
  }

  // Import template
  if (templatePath) {
    console.log(`Importing template from ${templatePath}...`);
    const template = JSON.parse(readFileSync(resolve(templatePath), "utf-8"));
    await api(apiUrl, `/v1/default/banks/${bankId}/import`, "POST", template, apiToken);
    console.log("  Done.");
  }

  // Ingest content
  if (contentDir) {
    const dir = resolve(contentDir);
    const exts = new Set([".md", ".txt", ".html", ".json", ".csv", ".xml"]);
    const files = readdirSync(dir).filter((f) => exts.has(extname(f).toLowerCase())).sort();
    if (files.length > 0) {
      console.log(`Ingesting ${files.length} file(s) from ${dir}...`);
      for (const file of files) {
        const content = readFileSync(join(dir, file), "utf-8");
        if (!content.trim()) continue;
        const docId = file.replace(/\.[^.]+$/, "");
        await api(apiUrl, `/v1/default/banks/${bankId}/memories`, "POST", {
          items: [{ content, document_id: docId }],
          async: true,
        }, apiToken);
        console.log(`  ${file} → queued`);
      }
    }
  }

  // Install skill
  const skillDir = join(info.workspaceDir, "skills", "agent-knowledge");
  mkdirSync(skillDir, { recursive: true });
  writeFileSync(join(skillDir, "SKILL.md"), SKILL_MD);
  console.log(`Skill installed to ${skillDir}`);

  // Patch startup file
  if (info.startupFile && info.startupPatch && existsSync(info.startupFile)) {
    let text = readFileSync(info.startupFile, "utf-8");
    if (!text.includes("agent-knowledge")) {
      if (text.includes("Don't ask permission.")) {
        text = text.replace("Don't ask permission. Just do it.", `${info.startupPatch}\n\nDon't ask permission. Just do it.`);
      } else {
        text += `\n\n${info.startupPatch}\n`;
      }
      writeFileSync(info.startupFile, text);
      console.log(`Startup patched: ${info.startupFile}`);
    }
  }

  console.log();
  console.log(`Wiki ready for '${agentId}'.`);
  if (harness === "openclaw") console.log("  Restart gateway: openclaw gateway restart");
  if (harness === "hermes") console.log(`  Chat: hermes --profile ${agentId}`);
}

main().catch((err) => {
  console.error(`Error: ${err.message}`);
  process.exit(1);
});
