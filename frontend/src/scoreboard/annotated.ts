import type { ScoreboardSummaryOut } from "../api/hooks";

// The annotated-night banner line + its reading toggle — D3b, widened by F10. An honest recorded row can
// still be set aside: (D3b) before D3a the fundamentals read dated a re-reported comparative revenue quarter
// at its later filing, so some nightly arms rested only on a year-old inflection — a PHANTOM ARM; (F10) a
// daytime hand run recorded its card under a day with NO market session (a weekend). Both carry an `exclude`
// annotation (never deleted, never rewritten), and the server's ONE READ POLICY decides whether this reading
// sets them all aside (the default) or scores them. The line says which reading the operator is looking at and
// names the KIND of night by the annotation's rule family; the toggle flips the reading for this view only.
// Pure helpers, so the copy and the flip are testable on their own.

type AnnotatedSummary = Pick<ScoreboardSummaryOut, "annotated_nights" | "annotated_policy">;

// `rule` is `<family>@<image sha>`; the families the two annotators write (call_annotations_repo.RULE_FAMILY_*).
const PHANTOM_FAMILY = "d3a-first-knowability";
const NO_SESSION_FAMILY = "no-session-night";
const familyOf = (rule: string) => rule.split("@", 1)[0];

/** How many annotated nights are of each kind. A night judged by BOTH rules counts under both; a night with
 *  neither recognized family counts as `other` (an honest "annotated", never mislabeled a phantom arm). */
function kinds(s: AnnotatedSummary) {
  let phantom = 0;
  let noSession = 0;
  let other = 0;
  for (const n of s.annotated_nights) {
    const fams = (n.rules ?? []).map(familyOf);
    const isPhantom = fams.includes(PHANTOM_FAMILY);
    const isNoSession = fams.includes(NO_SESSION_FAMILY);
    if (isPhantom) phantom += 1;
    if (isNoSession) noSession += 1;
    if (!isPhantom && !isNoSession) other += 1;
  }
  return { phantom, noSession, other };
}

/** The quiet banner line, or null when no night is annotated (honest loudness: nothing to say). */
export function annotatedNightsLine(s: AnnotatedSummary): string | null {
  const n = s.annotated_nights.length;
  if (n === 0) return null;
  const nights = n === 1 ? "night" : "nights";
  const { phantom, noSession, other } = kinds(s);
  const present = [
    phantom > 0 ? { count: phantom, word: "phantom-arm" } : null,
    noSession > 0 ? { count: noSession, word: "no-session" } : null,
    other > 0 ? { count: other, word: "other" } : null,
  ].filter((k): k is { count: number; word: string } => k !== null);
  // one kind: name it ("3 no-session nights"); several: say "annotated" and break the kinds out
  const label =
    present.length === 1
      ? `${n} ${present[0].word === "other" ? "annotated" : present[0].word} ${nights}`
      : `${n} annotated ${nights} (${present.map((k) => `${k.count} ${k.word}`).join(", ")})`;
  return s.annotated_policy === "excluded"
    ? `${label} set aside · not scored`
    : `${label} included in this reading · scored`;
}

const REASON_WORDS: Record<string, string> = {
  stale_comparative_revenue: "a phantom arm resting only on a stale-dated comparative revenue quarter",
  no_session_day: "dated a day with no market session",
};

/** The hover: which thesis-nights, why, and how to reverse it. */
export function annotatedNightsTitle(s: AnnotatedSummary): string {
  const list = s.annotated_nights.map((n) => `${n.thesis_name} ${n.asof}`).join(", ");
  const reading =
    s.annotated_policy === "excluded"
      ? "This reading sets them aside, like a reconstructed night"
      : "This reading scores them, as before the annotation";
  const tail = `${reading}; nothing is deleted, and a number quoted from this view should name its reading.`;
  const { noSession, other } = kinds(s);
  const tokens = [...new Set(s.annotated_nights.flatMap((n) => n.reasons))];
  if (noSession === 0 && other === 0) {
    // phantom arms only: the D3b wording, unchanged
    const reasons = tokens
      .map((r) => (r === "stale_comparative_revenue" ? "a stale-dated comparative revenue quarter" : r.replace(/_/g, " ")))
      .join("; ");
    return `annotated as phantom arms — the arm rested only on ${reasons}: ${list}. ${tail}`;
  }
  const reasons = tokens.map((r) => REASON_WORDS[r] ?? r.replace(/_/g, " ")).join("; ");
  return `annotated nights — ${reasons}: ${list}. ${tail}`;
}

/** One click flips the reading; the next returns to the server's default (null = do not send the param). */
export function nextAnnotatedOverride(
  current: boolean | null,
  policy: ScoreboardSummaryOut["annotated_policy"],
): boolean | null {
  return current === null ? policy === "excluded" : null;
}
