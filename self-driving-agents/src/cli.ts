#!/usr/bin/env node
/**
 * self-driving-agents — install a self-driving agent from a directory.
 *
 * npx @vectorize-io/self-driving-agents install <dir> --harness openclaw [--agent <name>]
 *
 * Directory layout:
 *   bank-template.json   — optional: bank config + mental models + directives
 *   content/             — optional: reference docs to ingest (.md, .txt, etc.)
 *
 * The CLI:
 *   1. Ensures the Hindsight plugin is installed and configured
 *   2. Copies template + content to the agent workspace
 *   3. Creates the harness agent and installs the skill
 *   4. Plugin bootstraps template + content on first session
 */

import { readFileSync, writeFileSync, copyFileSync, mkdirSync, existsSync, readdirSync } from "fs";
import { join, resolve, extname, basename } from "path";
import { homedir } from "os";
import { execSync } from "child_process";
import { createInterface } from "readline";

// ── Interactive prompts ─────────────────────────────────

function prompt(question: string): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  return new Promise((res) => {
    rl.question(question, (answer) => { rl.close(); res(answer.trim()); });
  });
}

async function confirm(question: string): Promise<boolean> {
  const answer = await prompt(`${question} (y/n) `);
  return answer.toLowerCase() === "y" || answer.toLowerCase() === "yes";
}

// ── Skill ───────────────────────────────────────────────

const SKILL_MD = `---
name: agent-knowledge
description: Your long-term knowledge pages. Read them at session start. Create new pages for recurring topics. Pages auto-update from your conversations.
---

# Agent Knowledge

You have knowledge pages that persist across sessions and auto-update from your conversations.

**How it works:** Conversations are retained into Hindsight. The system extracts observations and rebuilds each page via its "source query." You create pages; the system maintains them.

## At session start

Call \`agent_knowledge_list_pages\` to see what pages exist, then \`agent_knowledge_get_page\` for each one you need.

## Tools

- \`agent_knowledge_list_pages()\` — list page IDs and names (no content)
- \`agent_knowledge_get_page(page_id)\` — read the full content of a page
- \`agent_knowledge_create_page(page_id, name, source_query)\` — create a page
- \`agent_knowledge_update_page(page_id, name?, source_query?)\` — update a page
- \`agent_knowledge_delete_page(page_id)\` — delete a page
- \`agent_knowledge_recall(query)\` — search all memories
- \`agent_knowledge_ingest(title, content)\` — upload raw content (never summarize)

## Creating pages

Create when you learn something durable — preferences, procedures, performance data.
The source_query is a question the system re-asks to rebuild the page.

Examples:
- "What are the user's preferences for tone, length, and formatting?"
- "What strategies have performed well or poorly? Include numbers."
- "What are the best practices for [topic], preferring our data over generic advice?"

## Rules

- Pages update automatically — don't edit content directly
- State preferences clearly in responses so the system captures them
- Create pages silently
- Prefer fewer broad pages over many narrow ones
`;

// ── Hindsight plugin management ─────────────────────────

function isPluginInstalled(harness: string): boolean {
  if (harness !== "openclaw") return false;
  try {
    const configPath = join(homedir(), ".openclaw", "openclaw.json");
    if (!existsSync(configPath)) return false;
    const config = JSON.parse(readFileSync(configPath, "utf-8"));
    const plugin = config.plugins?.entries?.["hindsight-openclaw"];
    return plugin?.enabled !== false && plugin !== undefined;
  } catch {
    return false;
  }
}

function isPluginConfigured(harness: string): boolean {
  if (harness !== "openclaw") return false;
  try {
    const configPath = join(homedir(), ".openclaw", "openclaw.json");
    const config = JSON.parse(readFileSync(configPath, "utf-8"));
    const pc = config.plugins?.entries?.["hindsight-openclaw"]?.config || {};
    // Configured if it has an API URL, or embed version, or LLM provider set
    return !!(pc.hindsightApiUrl || pc.embedVersion || pc.llmProvider);
  } catch {
    return false;
  }
}

function getPluginSummary(): string {
  try {
    const configPath = join(homedir(), ".openclaw", "openclaw.json");
    const config = JSON.parse(readFileSync(configPath, "utf-8"));
    const pc = config.plugins?.entries?.["hindsight-openclaw"]?.config || {};
    if (pc.hindsightApiUrl) {
      return `External API: ${pc.hindsightApiUrl}`;
    } else if (pc.embedVersion) {
      return `Embedded daemon v${pc.embedVersion}`;
    } else {
      return "Not configured";
    }
  } catch {
    return "Unknown";
  }
}

async function ensurePlugin(harness: string): Promise<void> {
  if (harness !== "openclaw") return;

  if (!isPluginInstalled(harness)) {
    console.log("Hindsight plugin not found. Installing...\n");
    try {
      execSync("openclaw plugins install @vectorize-io/hindsight-openclaw", { stdio: "inherit" });
      console.log();
    } catch {
      console.error("Failed to install plugin. Install manually:");
      console.error("  openclaw plugins install @vectorize-io/hindsight-openclaw");
      process.exit(1);
    }
  }

  if (!isPluginConfigured(harness)) {
    console.log("Hindsight plugin needs configuration.\n");
    console.log("Running the setup wizard...\n");
    try {
      execSync("npx --package @vectorize-io/hindsight-openclaw hindsight-openclaw-setup", { stdio: "inherit" });
      console.log();
    } catch {
      console.error("Setup wizard failed. Run manually:");
      console.error("  npx --package @vectorize-io/hindsight-openclaw hindsight-openclaw-setup");
      process.exit(1);
    }
  } else {
    const summary = getPluginSummary();
    console.log(`Hindsight plugin: ${summary}`);
    if (process.stdin.isTTY) {
      const ok = await confirm("Continue with this configuration?");
      if (!ok) {
        console.log("\nRun the setup wizard to reconfigure:");
        console.log("  npx --package @vectorize-io/hindsight-openclaw hindsight-openclaw-setup");
        process.exit(0);
      }
    }
    console.log();
  }
}

// ── Main ────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);

  if (args.length < 1 || args[0] === "--help" || args[0] === "-h") {
    console.log(`Usage: npx @vectorize-io/self-driving-agents install <dir> --harness <harness> [--agent <name>]

Arguments:
  <dir>              Agent directory (contains optional bank-template.json + content/)

Options:
  --harness <h>      Required. openclaw | hermes | claude-code
  --agent <name>     Agent name (defaults to directory name)`);
    process.exit(0);
  }

  let dirArg = args[0] === "install" ? args[1] : args[0];
  const restArgs = args[0] === "install" ? args.slice(2) : args.slice(1);

  if (!dirArg) {
    console.error("Error: directory argument required");
    process.exit(1);
  }

  let harness: string | undefined;
  let agentName: string | undefined;

  for (let i = 0; i < restArgs.length; i++) {
    if (restArgs[i] === "--harness" && restArgs[i + 1]) harness = restArgs[++i];
    else if (restArgs[i] === "--agent" && restArgs[i + 1]) agentName = restArgs[++i];
  }

  if (!harness) {
    console.error("Error: --harness is required (openclaw | hermes | claude-code)");
    process.exit(1);
  }

  const dir = resolve(dirArg);
  if (!existsSync(dir)) {
    console.error(`Error: directory not found: ${dir}`);
    process.exit(1);
  }

  const agentId = agentName || basename(dir);

  // Step 1: Ensure Hindsight plugin is installed and configured
  await ensurePlugin(harness);

  // Resolve workspace
  let workspaceDir: string;
  switch (harness) {
    case "openclaw":
      workspaceDir = join(homedir(), ".hindsight-agents", "openclaw", agentId);
      break;
    case "hermes":
      workspaceDir = join(homedir(), ".hermes");
      break;
    case "claude-code":
      workspaceDir = join(homedir(), ".claude");
      break;
    default:
      console.error(`Unknown harness: ${harness}`);
      process.exit(1);
  }

  console.log(`Installing '${agentId}' on ${harness}`);
  console.log(`  Source:    ${dir}`);
  console.log(`  Workspace: ${workspaceDir}`);
  console.log();

  mkdirSync(workspaceDir, { recursive: true });

  // Step 2: Copy bank-template.json
  const templateSrc = join(dir, "bank-template.json");
  const hindsightDir = join(workspaceDir, ".hindsight");
  if (existsSync(templateSrc)) {
    mkdirSync(hindsightDir, { recursive: true });
    copyFileSync(templateSrc, join(hindsightDir, "bank-template.json"));
    console.log("Copied bank-template.json");
  }

  // Step 3: Copy content/
  const contentSrc = join(dir, "content");
  if (existsSync(contentSrc)) {
    const contentDst = join(hindsightDir, "content");
    mkdirSync(contentDst, { recursive: true });
    const exts = new Set([".md", ".txt", ".html", ".json", ".csv", ".xml"]);
    const files = readdirSync(contentSrc).filter((f) => exts.has(extname(f).toLowerCase()));
    for (const file of files) {
      copyFileSync(join(contentSrc, file), join(contentDst, file));
    }
    if (files.length > 0) console.log(`Copied ${files.length} content file(s)`);
  }

  // Step 4: Install skill
  const skillDir = join(workspaceDir, "skills", "agent-knowledge");
  mkdirSync(skillDir, { recursive: true });
  writeFileSync(join(skillDir, "SKILL.md"), SKILL_MD);
  console.log("Skill installed.");

  // Step 5: Create harness agent
  if (harness === "openclaw") {
    try {
      const listOut = execSync("openclaw agents list --json 2>/dev/null", { encoding: "utf-8" });
      const agents = JSON.parse(listOut).agents || [];
      if (!agents.some((a: any) => a.name === agentId)) {
        execSync(`openclaw agents add ${agentId} --workspace ${workspaceDir} --non-interactive`, { stdio: "pipe" });
        console.log(`Created agent '${agentId}'.`);
      } else {
        console.log(`Agent '${agentId}' already exists.`);
      }
    } catch {
      console.log(`Note: create agent manually:\n  openclaw agents add ${agentId} --workspace ${workspaceDir} --non-interactive`);
    }
  }

  // Step 6: Patch startup file
  const startupFile = harness === "openclaw" ? join(workspaceDir, "AGENTS.md") : undefined;
  const startupPatch = '5. Read `skills/agent-knowledge/SKILL.md` and **execute its mandatory startup sequence**';

  if (startupFile && existsSync(startupFile)) {
    let text = readFileSync(startupFile, "utf-8");
    if (!text.includes("agent-knowledge")) {
      text = text.replace("Don't ask permission. Just do it.", `${startupPatch}\n\nDon't ask permission. Just do it.`);
      writeFileSync(startupFile, text);
      console.log("Startup patched.");
    }
  }

  console.log();
  console.log(`'${agentId}' installed.`);
  console.log();
  console.log("Next steps:");
  if (harness === "openclaw") {
    console.log("  1. openclaw gateway restart");
    console.log(`  2. openclaw tui --session agent:${agentId}:main:session1`);
    console.log();
    console.log("The first session will import the template and content automatically.");
  }
}

main().catch((err) => {
  console.error(`Error: ${err.message}`);
  process.exit(1);
});
