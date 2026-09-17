// The BREADTH fields (B3), joined to a ledger row.
//
// They ride B6's own `episodes` payload rather than the Scoreboard's `ScoredEpisode`, deliberately:
// widening the shared episode model would push a backtest-only concept into the record's ledger, where
// it has no meaning. So the run response carries the raw episodes beside the ledger and this joins the
// two on the episode's own key — `(thesis_id, security_id, arm_date)`, the key the backend derives
// episodes on.
//
// Why breadth matters enough to surface at all: 82% of arm episodes on the measured record arrived
// alongside a co-member the same thesis-session. Without this, the 18% that armed alone are
// indistinguishable from the burst that armed with twelve others, and a 13-name broadcast reads as
// thirteen independent observations.

export type Breadth = {
  co_arm_count: number;
  armed_count_that_night: number;
  co_arm_bucket: string;
  key1_source: string | null;
  key1_sources: string[];
  confirmation_grade: string | null;
};

export function episodeKey(thesisId: string, securityId: string, armDate: string): string {
  return `${thesisId}|${securityId}|${armDate}`;
}

/** Index the run's raw episodes by their own key. Tolerant by design: the payload is an untyped
 *  pass-through so a run written by an older engine simply yields fewer fields, and a row missing its
 *  key is skipped rather than throwing — a research surface must not white-screen on an old artifact. */
export function indexBreadth(episodes: readonly unknown[]): Map<string, Breadth> {
  const out = new Map<string, Breadth>();
  for (const raw of episodes) {
    if (!raw || typeof raw !== "object") continue;
    const e = raw as Record<string, unknown>;
    const tid = e.thesis_id;
    const sid = e.security_id;
    const arm = e.arm_date;
    if (typeof tid !== "string" || typeof sid !== "string" || typeof arm !== "string") continue;
    out.set(episodeKey(tid, sid, arm), {
      co_arm_count: typeof e.co_arm_count === "number" ? e.co_arm_count : 0,
      armed_count_that_night:
        typeof e.armed_count_that_night === "number" ? e.armed_count_that_night : 0,
      co_arm_bucket: typeof e.co_arm_bucket === "string" ? e.co_arm_bucket : "",
      key1_source: typeof e.key1_source === "string" ? e.key1_source : null,
      key1_sources: Array.isArray(e.key1_sources)
        ? e.key1_sources.filter((k): k is string => typeof k === "string")
        : [],
      confirmation_grade: typeof e.confirmation_grade === "string" ? e.confirmation_grade : null,
    });
  }
  return out;
}

/** The one-line breadth sentence for an episode, or null when the run carries no breadth for it.
 *
 *  TWO counts, because they answer different questions and one number cannot tell them apart: how many
 *  OTHER members newly armed that session (the decision that was made that night) versus how many were
 *  armed in total, new and sticky alike (the loudness the operator actually saw). */
export function breadthLine(b: Breadth | undefined): string | null {
  if (!b) return null;
  const co =
    b.co_arm_count === 0
      ? "armed alone that session"
      : `armed alongside ${b.co_arm_count} other newly-armed name${b.co_arm_count === 1 ? "" : "s"}`;
  const loud =
    b.armed_count_that_night > 0
      ? ` · ${b.armed_count_that_night} armed in total that night (new and sticky)`
      : "";
  const bucket = b.co_arm_bucket ? ` · bucket ${b.co_arm_bucket}` : "";
  return `${co}${loud}${bucket}`;
}

/** The Key-1 line: which lock turned, and whether more than one claim was present. 70 armed
 *  member-nights on the measured record carry two, so the plural is not hypothetical. */
export function key1Line(b: Breadth | undefined): string | null {
  if (!b || (!b.key1_source && b.key1_sources.length === 0)) return null;
  const all = b.key1_sources.length ? b.key1_sources : b.key1_source ? [b.key1_source] : [];
  const strongest = b.key1_source ?? all[0];
  if (all.length <= 1) return `Key 1: ${strongest}`;
  return `Key 1: ${strongest} (also ${all.filter((k) => k !== strongest).join(", ")})`;
}
