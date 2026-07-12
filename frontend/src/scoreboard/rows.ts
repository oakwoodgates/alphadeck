import type {
  ScoreboardEpisodeOut,
  ScoreboardMetricOut,
  ScoreboardThesisOut,
} from "../api/hooks";

// Pure display logic for the Scoreboard ledger (the buckets.ts model: unit-tested, no React).
// Honest loudness throughout: a running return is labeled running, an inferred price is marked,
// a censored arm says so, and a metric below min_n never renders as a claim.

/** A formatted return: text + tone class. "—" (no tone) when unknowable. */
export function fmtReturn(x: number | null | undefined): { text: string; cls: string } {
  if (x === null || x === undefined) return { text: "—", cls: "" };
  const pct = (x * 100).toFixed(1);
  const signed = x > 0 ? `+${pct}%` : `${pct}%`;
  return { text: signed, cls: x > 0 ? "pos" : x < 0 ? "neg" : "" };
}

/** The episode's return, labeled for what it IS: realized only once closed AND matured; running
 *  (to the last bar ≤ asof) otherwise; "awaiting first bar" for a day-1 arm with no bar yet. */
export function returnLabel(e: ScoreboardEpisodeOut): string {
  if (e.insufficient_prices) return "awaiting first bar";
  if (e.status === "closed" && e.matured) return "realized";
  return "running";
}

export type Badge = { label: string; cls: string; title?: string };

/** The episode row's badges — each marks an exception, never a constant (honest loudness). */
export function episodeBadges(e: ScoreboardEpisodeOut): Badge[] {
  const out: Badge[] = [];
  if (e.status === "open") out.push({ label: "OPEN", cls: "b-open", title: "still armed at the record edge" });
  if (e.matured) out.push({ label: "MATURED", cls: "b-mat", title: "its own exit-by has elapsed — judged" });
  if (e.censored_start)
    out.push({
      label: "CENSORED",
      cls: "b-cen",
      title: "the record began mid-arm — the true arm date is unknowable (excluded from metrics)",
    });
  if (e.truncated && !e.insufficient_prices)
    out.push({ label: "to last bar", cls: "b-trunc", title: "measured to the last bar ≤ as-of" });
  return out;
}

/** The operator cell's one-line story (the wire slot, or the honest capture gap). */
export function operatorLine(e: ScoreboardEpisodeOut): {
  kind: "took" | "passed" | "none";
  text: string;
  ret: { text: string; cls: string } | null;
  inferred: boolean;
} {
  const op = e.operator;
  if (!op) return { kind: "none", text: "no decision logged", ret: null, inferred: false };
  if (op.action === "passed") {
    return { kind: "passed", text: `passed ${op.decision_date}`, ret: null, inferred: false };
  }
  const ret = fmtReturn(op.operator_return);
  const entry = op.entry_price != null ? ` @ ${op.entry_price}` : "";
  const running = op.running ? " · running" : "";
  return {
    kind: "took",
    text: `took ${op.decision_date}${entry}${running}`,
    ret,
    inferred: Boolean(op.entry_inferred || op.exit_inferred),
  };
}

/** Metrics split for the strip: sufficient ones render; the rest collapse into ONE quiet line
 *  (seven "insufficient" rows would be noise — the gate itself is the information). */
export function gateMetrics(
  metrics: ScoreboardMetricOut[],
  minN: number,
): { shown: ScoreboardMetricOut[]; gatedLine: string | null } {
  const shown = metrics.filter((m) => !m.insufficient_n);
  const gated = metrics.length - shown.length;
  if (gated === 0) return { shown, gatedLine: null };
  const maxN = Math.max(0, ...metrics.filter((m) => m.insufficient_n).map((m) => m.n));
  return {
    shown,
    gatedLine: `${gated} of ${metrics.length} metrics await n ≥ ${minN} (largest today: n=${maxN})`,
  };
}

/** One headline number per sufficient metric (median first, then the metric's own summary keys). */
export function metricHeadline(m: ScoreboardMetricOut): string {
  const s = m.summary ?? {};
  const pick = ["median", "rate", "median_lift", "median_days_exit_after_peak"].find(
    (k) => s[k] !== null && s[k] !== undefined,
  );
  if (!pick) return `n=${m.n}`;
  const v = s[pick] as number;
  const text = pick === "median" || pick === "median_lift" ? fmtReturn(v).text : String(v);
  return `${pick} ${text} · n=${m.n}`;
}

/** The thesis group's hint line: record span, plus any OPEN warming-with-conviction run — an
 *  accruing withheld window is worth a quiet mark whether or not episodes already exist
 *  (mockup proposal ⑩, operator-approved). */
export function groupHint(t: ScoreboardThesisOut): string {
  if (t.record_error) return "record error";
  if (!t.first_call_asof) return "no call-of-record yet";
  const span =
    t.first_call_asof === t.last_call_asof
      ? `record ${t.first_call_asof}`
      : `record ${t.first_call_asof} → ${t.last_call_asof}`;
  if (t.warming_since) return `${span} · warming since ${t.warming_since}`;
  return span;
}

/** Group tone class from the record-edge state (reuses the lifecycle --gc idiom). */
export function groupToneClass(t: ScoreboardThesisOut): string {
  if (t.episodes.some((e) => e.status === "open")) return "sbg-armed";
  if (t.current_state === "warming") return "sbg-warm";
  return "sbg-quiet";
}

/** Rows-worth of content a group has (episodes + off-record spans) — drives the header count. */
export function groupCount(t: ScoreboardThesisOut): number {
  return t.episodes.length + t.operator_spans.length;
}
