import type { PaperclipPluginManifestV1 } from "@paperclipai/plugin-sdk";

const manifest: PaperclipPluginManifestV1 = {
  id: "paperclip-plugin-hindsight",
  apiVersion: 1,
  version: "0.2.0",
  displayName: "Hindsight Memory",
  author: "Vectorize <support@vectorize.io>",
  description:
    "Persistent long-term memory for Paperclip agents. Automatically recalls relevant context before each run and retains agent output after — so every agent gets smarter over time.",
  categories: ["automation"],
  capabilities: [
    "events.subscribe",
    "agent.tools.register",
    "plugin.state.read",
    "plugin.state.write",
    "http.outbound",
    "secrets.read-ref",
    "agents.read",
    // NORA fork — needed to look up the active issue and parse user identity
    // from its `originId` (format `<phone>::<email>`) when `bankGranularity`
    // includes `"user"`.
    "issues.read",
    // NORA fork — fallback when the adapter (e.g. `process`) doesn't propagate
    // LLM output in `agent.run.finished.payload.output`: pull the agent's own
    // comments and use them as the retain content.
    "issue.comments.read",
  ],
  entrypoints: {
    worker: "./dist/worker.js",
  },
  instanceConfigSchema: {
    type: "object",
    required: ["hindsightApiUrl"],
    properties: {
      hindsightApiUrl: {
        type: "string",
        title: "Hindsight API URL",
        description:
          "Base URL of your Hindsight instance. Use http://localhost:8888 for self-hosted.",
        default: "http://localhost:8888",
      },
      hindsightApiKeyRef: {
        type: "string",
        title: "Hindsight API Key (secret ref)",
        description:
          "Name of the Paperclip secret holding your Hindsight Cloud API key. Leave empty for self-hosted.",
      },
      bankGranularity: {
        type: "array",
        title: "Bank Granularity",
        description:
          "Controls memory isolation. Default ['company','agent'] = each agent has its own bank shared by all users of the company. " +
          "Add 'user' (e.g. ['company','agent','user']) to scope memories per Frappe user — required when the same agent serves multiple human users (RGPD multi-user privacy). " +
          "User scoping requires the upstream issue's `originId` to be set in the format `<key>::<email>` (NORA WhatsApp plugin convention).",
        items: { type: "string", enum: ["company", "agent", "user"] },
        default: ["company", "agent"],
      },
      recallBudget: {
        type: "string",
        title: "Recall Budget",
        description: "'low' is fastest, 'mid' balances speed and depth, 'high' is most thorough.",
        enum: ["low", "mid", "high"],
        default: "mid",
      },
      autoRetain: {
        type: "boolean",
        title: "Auto-retain on Run Finished",
        description: "Automatically retain agent run output to Hindsight when a run completes.",
        default: true,
      },
    },
  },
  tools: [
    {
      name: "hindsight_recall",
      displayName: "Recall from Memory",
      description:
        "Search Hindsight long-term memory for context relevant to a query. Use this before starting a task to surface relevant past decisions, preferences, and knowledge.",
      parametersSchema: {
        type: "object",
        required: ["query"],
        properties: {
          query: {
            type: "string",
            description: "What to search for in memory",
          },
        },
      },
    },
    {
      name: "hindsight_retain",
      displayName: "Save to Memory",
      description:
        "Store important facts, decisions, or outcomes in Hindsight long-term memory for future runs.",
      parametersSchema: {
        type: "object",
        required: ["content"],
        properties: {
          content: {
            type: "string",
            description: "The content to store in memory",
          },
        },
      },
    },
  ],
};

export default manifest;
