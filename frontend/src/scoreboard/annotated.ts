import type { ScoreboardSummaryOut } from "../api/hooks";

// The annotated-night (phantom-arm) banner line + its reading toggle — D3b. An honest recorded row can
// still be wrong: before D3a the fundamentals read dated a re-reported comparative revenue quarter at its
// later filing, so some nightly arms rested only on a year-old inflection. Those rows carry an `exclude`
// annotation (never deleted, never rewritten), and the server's READ POLICY decides whether this reading
// sets them aside (the default) or scores them. The line says which reading the operator is looking at;
// the toggle flips it for this view only. Pure helpers, so the copy and the flip are testable on their own.

type AnnotatedSummary = Pick<ScoreboardSummaryOut, "annotated_nights" | "annotated_policy">;

/** The quiet banner line, or null when no night is annotated (honest loudness: nothing to say). */
export function annotatedNightsLine(s: AnnotatedSummary): string | null {
  const n = s.annotated_nights.length;
  if (n === 0) return null;
  const nights = n === 1 ? "night" : "nights";
  return s.annotated_policy === "excluded"
    ? `${n} phantom-arm ${nights} set aside · not scored`
    : `${n} phantom-arm ${nights} included in this reading · scored`;
}

/** The hover: which thesis-nights, why, and how to reverse it. */
export function annotatedNightsTitle(s: AnnotatedSummary): string {
  const reasons = [...new Set(s.annotated_nights.flatMap((n) => n.reasons))]
    .map((r) => (r === "stale_comparative_revenue" ? "a stale-dated comparative revenue quarter" : r.replace(/_/g, " ")))
    .join("; ");
  const list = s.annotated_nights.map((n) => `${n.thesis_name} ${n.asof}`).join(", ");
  const reading =
    s.annotated_policy === "excluded"
      ? "This reading sets them aside, like a reconstructed night"
      : "This reading scores them, as before the annotation";
  return `annotated as phantom arms — the arm rested only on ${reasons}: ${list}. ${reading}; nothing is deleted, and a number quoted from this view should name its reading.`;
}

/** One click flips the reading; the next returns to the server's default (null = do not send the param). */
export function nextAnnotatedOverride(
  current: boolean | null,
  policy: ScoreboardSummaryOut["annotated_policy"],
): boolean | null {
  return current === null ? policy === "excluded" : null;
}
