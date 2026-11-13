// The API's two FIXED refusals on a user-triggered fetch (security hardening PR 1b, docs/AUTH.md): the own-cards
// 404 (a plain user fetches only for a company on one of their own cards) and the daily-allowance 429 (a per-user
// ceiling on each kind of fetch, naming when it resets). They are answers, not breakages, so every screen that
// makes one of these calls shows the message on its own visible line, worded as the API words it.

export const NOT_ON_YOUR_CARDS = "This company is not on any of your cards. Add it to a card first.";
export const ALLOWANCE_PREFIX = "Daily allowance reached:";

/** The API's own words when `e` is one of the two fetch refusals, else null (any other error keeps its screen's
 *  usual handling). */
export function refusalText(e: unknown): string | null {
  const d = (e as { detail?: unknown } | null)?.detail;
  if (typeof d !== "string") return null;
  return d === NOT_ON_YOUR_CARDS || d.startsWith(ALLOWANCE_PREFIX) ? d : null;
}
