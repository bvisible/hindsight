/**
 * paperclip-plugin-hindsight — worker entrypoint.
 *
 * Gives Paperclip agents persistent long-term memory via Hindsight.
 *
 * Lifecycle:
 *   agent.run.started  → recall relevant memories, store in plugin state for the run
 *   agent.run.finished → retain agent output to Hindsight (if autoRetain is enabled)
 *
 * Agent tools (callable mid-run):
 *   hindsight_recall(query)   → search memory, returns relevant context
 *   hindsight_retain(content) → store content in memory immediately
 */

import { definePlugin, runWorker } from "@paperclipai/plugin-sdk";
import type { ToolRunContext } from "@paperclipai/plugin-sdk";
import { HindsightClient, formatMemories } from "./client.js";
import { deriveBankId } from "./bank.js";

interface PluginConfig {
  hindsightApiUrl: string;
  hindsightApiKeyRef?: string;
  bankGranularity?: Array<"company" | "agent" | "user">;
  recallBudget?: "low" | "mid" | "high";
  autoRetain?: boolean;
}

interface RunStartedPayload {
  agentId: string;
  runId: string;
  issueTitle?: string;
  issueDescription?: string;
}

interface RunFinishedPayload {
  agentId: string;
  runId: string;
  /**
   * Some adapters (hermes_local, claude_local, ...) include the LLM output in
   * the event payload. The `process` adapter does NOT — for those agents we
   * fall back to fetching the agent-authored comments on the active issue
   * (see retainFromIssueComments below). `issueId` is set by the host.
   */
  output?: string;
  result?: string;
  issueId?: string | null;
}

async function getConfig(ctx: {
  config: { get(): Promise<Record<string, unknown>> };
}): Promise<PluginConfig> {
  return (await ctx.config.get()) as unknown as PluginConfig;
}

async function resolveApiKey(
  ctx: { secrets: { resolve(ref: string): Promise<string | null> } },
  config: PluginConfig
): Promise<string | undefined> {
  if (!config.hindsightApiKeyRef) return undefined;
  const resolved = await ctx.secrets.resolve(config.hindsightApiKeyRef);
  return resolved ?? undefined;
}

/**
 * NORA fork — resolve the user identity for the active run, by looking up the
 * agent's currently checked-out issue and parsing its `originId`.
 *
 * Convention used by upstream creators (e.g. paperclip-plugin-whatsapp):
 *   originId = "<channel-key>::<user-email>"
 *      ex.    "+41798279951::jeremy@neoffice.io"
 *
 * Returns the email when present, otherwise undefined (callers fall back to
 * a non-user-scoped bank).
 *
 * Skipped entirely when `bankGranularity` does not include "user" — saves a
 * round-trip to the host when user scoping is disabled.
 */
async function resolveUserIdFromActiveIssue(
  ctx: {
    issues: {
      list(input: {
        companyId: string;
        assigneeAgentId?: string;
        status?: string;
        limit?: number;
      }): Promise<Array<{ originId?: string | null }>>;
    };
    logger: { warn(msg: string, meta?: Record<string, unknown>): void };
  },
  config: PluginConfig,
  companyId: string,
  agentId: string,
): Promise<string | undefined> {
  if (!config.bankGranularity?.includes("user")) return undefined;

  try {
    const issues = await ctx.issues.list({
      companyId,
      assigneeAgentId: agentId,
      status: "in-progress",
      limit: 1,
    });
    const originId = issues[0]?.originId ?? "";
    if (!originId.includes("::")) return undefined;
    const parts = originId.split("::");
    // Take the last segment that looks like an email — robust against
    // additional `::` separators in the channel key.
    const email = [...parts].reverse().find((segment) => segment.includes("@"));
    return email && email.includes("@") ? email : undefined;
  } catch (err) {
    ctx.logger.warn("Could not resolve user from active issue", {
      agentId,
      error: String(err),
    });
    return undefined;
  }
}

const plugin = definePlugin({
  async setup(ctx) {
    ctx.logger.info("Hindsight memory plugin starting");

    // ---------------------------------------------------------------------------
    // agent.run.started — recall memories and cache them for this run
    // ---------------------------------------------------------------------------
    ctx.events.on("agent.run.started", async (event) => {
      const payload = event.payload as RunStartedPayload;
      const config = await getConfig(ctx);
      const { agentId, runId, issueTitle, issueDescription } = payload;
      const companyId = event.companyId;

      const query = [issueTitle, issueDescription].filter(Boolean).join("\n");
      if (!query.trim()) return;

      try {
        const apiKey = await resolveApiKey(ctx, config);
        const client = new HindsightClient(config.hindsightApiUrl, apiKey);
        const userId = await resolveUserIdFromActiveIssue(ctx, config, companyId, agentId);
        const bankId = deriveBankId({ companyId, agentId, userId }, config);

        const response = await client.recall(bankId, query, config.recallBudget ?? "mid");

        const memories = formatMemories(response.results);
        if (memories) {
          await ctx.state.set(
            { scopeKind: "run", scopeId: runId, stateKey: "recalled-memories" },
            memories
          );
          ctx.logger.info("Recalled memories for run", {
            runId,
            bankId,
            count: response.results.length,
          });
        }
      } catch (err) {
        // Non-fatal: agent runs without memory context.
        ctx.logger.warn("Failed to recall memories on run start", {
          runId,
          error: String(err),
        });
      }
    });

    // ---------------------------------------------------------------------------
    // agent.run.finished — retain run output to Hindsight
    // ---------------------------------------------------------------------------
    ctx.events.on("agent.run.finished", async (event) => {
      const payload = event.payload as RunFinishedPayload;
      const config = await getConfig(ctx);

      if (config.autoRetain === false) return;

      const { agentId, runId, output, result, issueId } = payload;
      const companyId = event.companyId;

      // Adapters that don't propagate LLM output in the event payload (e.g.
      // the bare `process` adapter used by NORA) fall back to a transcript
      // built from the issue's title/description and the agent's own comments
      // — Hindsight needs both sides of the exchange to extract useful
      // semantic claims from a short reply like "C'est noté".
      let content = (output ?? result)?.trim() ?? "";
      if (!content && issueId) {
        try {
          const issue = await ctx.issues.get(issueId, companyId);
          const userMessage = [issue?.title, issue?.description].filter(Boolean).join("\n").trim();
          const comments = await ctx.issues.listComments(issueId, companyId);
          const ourComments = comments
            .filter((c) => c.authorAgentId === agentId)
            .sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));
          const agentReply = ourComments.map((c) => c.body).join("\n\n").trim();
          const parts: string[] = [];
          if (userMessage) parts.push(`User: ${userMessage}`);
          if (agentReply) parts.push(`Nora: ${agentReply}`);
          content = parts.join("\n\n").trim();
        } catch (err) {
          ctx.logger.warn("Could not fall back to issue comments for retain", {
            runId,
            issueId,
            error: String(err),
          });
        }
      }

      if (!content) return;

      try {
        const apiKey = await resolveApiKey(ctx, config);
        const client = new HindsightClient(config.hindsightApiUrl, apiKey);
        // Resolve the user identity from the explicit issueId in the payload
        // first — at run.finished time the issue is usually already `done`,
        // so the legacy `list({ status: "in-progress" })` lookup misses it.
        let userId: string | undefined;
        if (config.bankGranularity?.includes("user")) {
          if (issueId) {
            try {
              const issue = await ctx.issues.get(issueId, companyId);
              const originId = issue?.originId ?? "";
              if (originId.includes("::")) {
                const email = [...originId.split("::")].reverse().find((s) => s.includes("@"));
                if (email && email.includes("@")) userId = email;
              }
            } catch {
              // fall through to legacy lookup
            }
          }
          if (!userId) {
            userId = await resolveUserIdFromActiveIssue(ctx, config, companyId, agentId);
          }
        }
        const bankId = deriveBankId({ companyId, agentId, userId }, config);

        await client.retain(bankId, content, runId, { agentId, companyId, runId });
        ctx.logger.info("Retained run output to memory", { runId, bankId, length: content.length });
      } catch (err) {
        ctx.logger.warn("Failed to retain run output", {
          runId,
          error: String(err),
        });
      }
    });

    // ---------------------------------------------------------------------------
    // Tool: hindsight_recall
    //
    // NORA fork extension 2026-05-16 — additional `bank` parameter
    // -----------------------------------------------------------------
    // The default bankId is derived from companyId+agentId (+optional userId)
    // and points at the agent's instance-scoped memory bank. That's perfect
    // for "what did I do last week ?" type queries.
    //
    // BUT we also store curated knowledge in sub-banks of the form
    // `paperclip::<companyId>::<agentId>::knowledge::<slot>` :
    //   - `compta::knowledge::ch`     — Swiss accounting RAG (#27 R-V14)
    //   - `compta::knowledge::tenant` — local plan comptable (#35)
    //   - `ocr::knowledge::tenant`    — OCR-category statistics (#35)
    //   - <agentId>::collective       — cross-tenant pulled claims (#34)
    //
    // Without an override, these sub-banks were unreachable from the agent
    // SKILL prompt — the recall tool always hit only the instance bank.
    // The new optional `bank` parameter lets a SKILL target a specific
    // sub-bank using a slot suffix :
    //
    //   hindsight_recall(query="...", bank="tenant")       → ::knowledge::tenant
    //   hindsight_recall(query="...", bank="ch")           → ::knowledge::ch
    //   hindsight_recall(query="...", bank="collective")   → ::collective
    //   hindsight_recall(query="...")                       → instance (default)
    //
    // For multi-bank scans, callers issue separate calls (the runtime can
    // parallelise them ; we keep the tool API explicit rather than
    // smart-routing inside the worker).
    // ---------------------------------------------------------------------------
    ctx.tools.register(
      "hindsight_recall",
      {
        displayName: "Recall from Memory",
        description:
          "Search Hindsight long-term memory for context relevant to a query. " +
          "Optional 'bank' parameter targets a specific sub-bank (tenant / ch / " +
          "collective). Omit to search the agent's default instance bank.",
        parametersSchema: {
          type: "object",
          required: ["query"],
          properties: {
            query: { type: "string", description: "What to search for" },
            bank: {
              type: "string",
              description:
                "Optional sub-bank slot. Use 'tenant' for the local tenant " +
                "knowledge (#35), 'ch' for the curated Swiss accounting RAG, " +
                "'collective' for cross-tenant validated claims. Omit for the " +
                "default agent instance bank.",
            },
          },
        },
      },
      async (params: unknown, runCtx: ToolRunContext) => {
        const { query, bank: requestedBank } = params as {
          query: string;
          bank?: string;
        };
        const config = await getConfig(ctx);
        const userId = await resolveUserIdFromActiveIssue(
          ctx,
          config,
          runCtx.companyId,
          runCtx.agentId,
        );

        // Resolve target bankId. The default is the agent instance bank
        // (via deriveBankId). When `bank` is provided, append the slot
        // suffix to address one of the curated sub-banks. We do NOT
        // accept fully-qualified bank ids on purpose : the agent should
        // never be able to read another tenant's memory.
        let bankId = deriveBankId(
          { companyId: runCtx.companyId, agentId: runCtx.agentId, userId },
          config,
        );
        if (requestedBank && typeof requestedBank === "string") {
          const slot = requestedBank.trim();
          // Whitelist of accepted slots — fail-closed defense against
          // an SKILL prompt that asks for an arbitrary path injection.
          const accepted = new Set([
            "tenant",
            "ch",
            "collective",
            "knowledge",
          ]);
          if (!accepted.has(slot)) {
            return {
              content:
                `Unknown bank slot '${slot}'. Accepted : tenant, ch, collective. ` +
                `Falling back to default agent bank.`,
            };
          }
          // `collective` is a peer of the agent bank, not a knowledge sub-slot.
          // Layout :
          //   paperclip::<cid>::<aid>             ← instance
          //   paperclip::<cid>::<aid>::collective ← #34 cross-tenant pulled
          //   paperclip::<cid>::<aid>::knowledge::<slot> ← #27/#35 curated
          // Use deriveBankId() output as the base agent-scoped prefix.
          const baseAgentBank = deriveBankId(
            { companyId: runCtx.companyId, agentId: runCtx.agentId },
            { bankGranularity: ["company", "agent"] },
          );
          if (slot === "collective") {
            bankId = `${baseAgentBank}::collective`;
          } else {
            bankId = `${baseAgentBank}::knowledge::${slot}`;
          }
        }

        // Return cached memories from run start if available, but ONLY
        // when the caller is hitting the default bank. Curated sub-banks
        // bypass the run-start prefetch cache (which only fills from the
        // instance bank).
        if (!requestedBank) {
          const cached = await ctx.state.get({
            scopeKind: "run",
            scopeId: runCtx.runId,
            stateKey: "recalled-memories",
          });
          if (cached && typeof cached === "string") {
            return { content: cached };
          }
        }

        // Live recall
        try {
          const apiKey = await resolveApiKey(ctx, config);
          const client = new HindsightClient(config.hindsightApiUrl, apiKey);
          const response = await client.recall(
            bankId,
            query,
            config.recallBudget ?? "mid",
          );
          const memories = formatMemories(response.results);
          return {
            content:
              memories ||
              `No relevant memories found in bank '${bankId.split("::").slice(-2).join("::")}' for "${query}".`,
          };
        } catch (err) {
          return { content: `Memory recall failed: ${String(err)}` };
        }
      },
    );

    // ---------------------------------------------------------------------------
    // Tool: hindsight_retain
    // ---------------------------------------------------------------------------
    ctx.tools.register(
      "hindsight_retain",
      {
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
      async (params: unknown, runCtx: ToolRunContext) => {
        const { content } = params as { content: string };
        const config = await getConfig(ctx);
        const userId = await resolveUserIdFromActiveIssue(
          ctx,
          config,
          runCtx.companyId,
          runCtx.agentId,
        );
        const bankId = deriveBankId(
          { companyId: runCtx.companyId, agentId: runCtx.agentId, userId },
          config
        );

        try {
          const apiKey = await resolveApiKey(ctx, config);
          const client = new HindsightClient(config.hindsightApiUrl, apiKey);
          await client.retain(bankId, content, undefined, {
            agentId: runCtx.agentId,
            companyId: runCtx.companyId,
            runId: runCtx.runId,
          });
          return { content: "Memory saved." };
        } catch (err) {
          return { content: `Failed to save memory: ${String(err)}` };
        }
      }
    );

    ctx.logger.info("Hindsight memory plugin ready");
  },

  async onHealth() {
    return { status: "ok" };
  },

  async onValidateConfig(config) {
    const c = config as Partial<PluginConfig>;
    if (!c.hindsightApiUrl?.trim()) {
      return { ok: false, errors: ["hindsightApiUrl is required"] };
    }

    try {
      const client = new HindsightClient(c.hindsightApiUrl);
      const healthy = await client.health();
      if (!healthy) {
        return {
          ok: false,
          errors: [`Cannot reach Hindsight at ${c.hindsightApiUrl}`],
        };
      }
    } catch (err) {
      return { ok: false, errors: [`Connection failed: ${String(err)}`] };
    }

    return { ok: true };
  },
});

export default plugin;
runWorker(plugin, import.meta.url);
