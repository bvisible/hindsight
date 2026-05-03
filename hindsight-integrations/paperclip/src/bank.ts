/**
 * Bank ID derivation — maps Paperclip company/agent/user identity onto
 * Hindsight bank IDs.
 *
 * Default format: "paperclip::{companyId}::{agentId}"
 *
 * bankGranularity: ['company']                  → "paperclip::{companyId}"
 * bankGranularity: ['agent']                    → "paperclip::{agentId}"
 * bankGranularity: ['company','agent']          → "paperclip::{companyId}::{agentId}"
 * bankGranularity: ['company','agent','user']   → "paperclip::{companyId}::{agentId}::user::{userId}"
 *
 * NORA fork extension (2026-05-02) — adds optional `user` granularity for
 * multi-user privacy (RGPD): each Frappe user gets their own bank slot when
 * the runtime can resolve a `userId`. When `userId` is absent, the `user`
 * level is silently skipped so the bank falls back to the company/agent
 * shared slot — keeps backwards compatibility with vanilla deployments.
 */

export interface BankContext {
  companyId: string;
  agentId: string;
  /**
   * Optional user identifier (typically the Frappe user email). When set
   * and `bankGranularity` includes `"user"`, the resulting bank id is
   * scoped per-user, isolating memories between humans sharing one
   * Paperclip company.
   */
  userId?: string;
}

export interface BankConfig {
  bankGranularity?: Array<"company" | "agent" | "user">;
}

export function deriveBankId(context: BankContext, config: BankConfig): string {
  const granularity = config.bankGranularity ?? ["company", "agent"];
  const parts: string[] = ["paperclip"];

  for (const field of granularity) {
    if (field === "company") parts.push(context.companyId);
    if (field === "agent") parts.push(context.agentId);
    if (field === "user" && context.userId) {
      // Two-segment marker so a bank id like
      //   paperclip::cc6bc6f2::compta::user::jeremy@neoffice.io
      // remains parseable as 5 segments. Without `userId` we silently
      // skip the level (graceful fallback to shared bank).
      parts.push("user", context.userId);
    }
  }

  return parts.join("::");
}
