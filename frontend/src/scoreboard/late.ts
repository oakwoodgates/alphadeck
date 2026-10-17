import type { ScoreboardSummaryOut } from "../api/hooks";

// The late-night banner line + its reading toggle — L1 (operator Q5 = A). A call-of-record recorded at or
// after its night's on-time window closed (the next weekday's 06:00 ET EDGAR open) can have read a filing
// SEC accepted after the night — the 2026-10-12 boot catch-up recorded 9 theses ~18 h late. The server's
// READ POLICY decides whether this reading sets those rows aside (the default: an on-time row for the night
// wins, a night with none reads like a cron gap) or scores them as before. Lateness is derived from the
// row's stamp, so nothing is deleted or marked either way. The line says which reading the operator is
// looking at; the toggle flips it for this view only. Pure helpers (the `annotated.ts` twin), so the copy
// and the flip are testable on their own.

type LateSummary = Pick<ScoreboardSummaryOut, "late_nights" | "late_policy">;
type LateNight = LateSummary["late_nights"][number];

/** The quiet banner line, or null when no night was recorded late (honest loudness: nothing to say). */
export function lateNightsLine(s: LateSummary): string | null {
  const n = s.late_nights.length;
  if (n === 0) return null;
  const nights = n === 1 ? "night" : "nights";
  return s.late_policy === "excluded"
    ? `${n} late-recorded ${nights} · late rows set aside`
    : `${n} late-recorded ${nights} · late rows scored in this reading`;
}

/** Whole hours between the EDGAR open the night missed and its first late row (zone-free: both are instants). */
export function hoursLate(n: Pick<LateNight, "recorded_at" | "edgar_open">): number {
  return Math.round((Date.parse(n.recorded_at) - Date.parse(n.edgar_open)) / 3_600_000);
}

/** The hover: which thesis-nights, how late, what the default reading scores instead, how to reverse it. */
export function lateNightsTitle(s: LateSummary): string {
  const list = s.late_nights
    .map(
      (n) =>
        `${n.thesis_name} ${n.asof} (+${hoursLate(n)} h; ${
          n.earlier_recorded_at ? "an earlier row scored instead" : "no earlier row: a gap"
        })`,
    )
    .join(", ");
  const reading =
    s.late_policy === "excluded"
      ? "This reading sets the late rows aside: an earlier row for the night is scored instead, and a night with none reads like a cron gap"
      : "This reading scores them, as before the late-row rule: the late row wins its night";
  return `recorded after the next EDGAR open (06:00 ET the next weekday), so a late row can have read filings its night could not: ${list}. ${reading}; nothing is deleted, and a number quoted from this view should name its reading.`;
}

/** One click flips the reading; the next returns to the server's default (null = do not send the param). */
export function nextLateOverride(
  current: boolean | null,
  policy: ScoreboardSummaryOut["late_policy"],
): boolean | null {
  return current === null ? policy === "excluded" : null;
}
