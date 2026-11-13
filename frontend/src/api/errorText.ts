// What the API said, as one plain line for an error line on screen. A route's own refusal is a sentence in `detail`;
// a request over an input limit (security hardening PR 3, docs/AUTH.md §Request limits) is FastAPI's 422 LIST
// (`[{loc, msg, type}]`), shown as its FIRST message with the field it names: "name: String should have at most 200
// characters."

interface ValidationItem {
  loc?: unknown;
  msg?: unknown;
}

const PLACES = new Set(["body", "query", "path", "header", "cookie"]);

function fieldOf(loc: unknown): string | null {
  if (!Array.isArray(loc)) return null;
  const names = loc.filter((p): p is string => typeof p === "string" && !PLACES.has(p));
  return names.length > 0 ? names[names.length - 1] : null;
}

function sentence(text: string): string {
  const t = text.replace(/^Value error, /, "").trim();
  return /[.!?]$/.test(t) ? t : `${t}.`;
}

/** The API's own words for `e` (an openapi-fetch error body): the `detail` sentence as it is, or the first message
 *  of a validation list naming its field; null when there is nothing readable. */
export function apiErrorText(e: unknown): string | null {
  const d = (e as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d !== "" ? d : null;
  if (!Array.isArray(d) || d.length === 0) return null;
  const first = d[0] as ValidationItem | null;
  if (!first || typeof first.msg !== "string" || first.msg === "") return null;
  const field = fieldOf(first.loc);
  return field ? `${field}: ${sentence(first.msg)}` : sentence(first.msg);
}
