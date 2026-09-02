import type {
  ScoreboardEpisodeOut,
  ScoreboardMetricOut,
  ScoreboardSummaryOut,
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

/** The Timing view's "Past peak" cell (Slice 2): `exit_vs_peak_days` as a compact `Nd` gap; "—"
 *  when the horizon-vs-peak gap is unknowable. A real 0 ("0d" — exited AT the peak) is meaningful,
 *  so it stays; the degenerate no-forward-bar case is dashed at the call site (see `noForwardBar`). */
export function fmtPastPeak(days: number | null | undefined): string {
  if (days === null || days === undefined) return "—";
  return `${days}d`;
}

/** True when the only bar on/after the arm is the arm-day bar itself — the last bar ≤ asof IS the
 *  arm bar (`exit_date === arm_date`), so `forward_return` is a degenerate 0.0% over a single bar,
 *  not a flat move. Distinct from `insufficient_prices` (no bar at all). Once a forward bar lands,
 *  `exit_date > arm_date` and the return is a real (running) number, even if ~0%. */
export function awaitingForwardBar(e: ScoreboardEpisodeOut): boolean {
  return e.exit_date != null && e.exit_date === e.arm_date;
}

/** The episode's return, labeled for what it IS: realized only once closed AND matured; running
 *  (to the last bar ≤ asof) otherwise; "awaiting first bar" for a day-1 arm with no bar yet;
 *  "awaiting forward bar" for a single-bar arm (only the arm-day bar — 0.0% is not a flat move).
 *  The single-bar check runs AFTER the realized check, so a degenerate matured single-bar episode
 *  still reads "realized" (it overrides only the "running" outcome). */
export function returnLabel(e: ScoreboardEpisodeOut): string {
  if (e.insufficient_prices) return "awaiting first bar";
  if (e.status === "closed" && e.matured) return "realized";
  if (awaitingForwardBar(e)) return "awaiting forward bar";
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
  if (e.ingest_flagged)
    out.push({
      label: "INGEST",
      cls: "b-ing",
      title:
        (e.ingest_note ?? "the arm rested on partial or late-ingested data") +
        " — excluded from metrics",
    });
  if (e.truncated && !e.insufficient_prices)
    out.push({ label: "to last bar", cls: "b-trunc", title: "measured to the last bar ≤ as-of" });
  return out;
}

// The de-arm tokens replay stamps on an episode (`backend/replay/episodes.py::_close_reason`) in the
// operator's English. A CLASSIFICATION, never a judgement — "aged out" says the horizon elapsed, not that
// the call was wrong. Additive-safe: a token this map doesn't know renders RAW rather than "unknown", so
// a new backend reason surfaces as itself instead of vanishing (#9) — and every render site keeps the raw
// token reachable in a `title=`, so the translation never hides what the record actually says.
const CLOSE_REASON_LABEL: Record<string, string> = {
  arm_until_lapsed: "entry window lapsed",
  conviction_aged_out: "conviction aged out (past exit-by)",
  managing: "position taken — managing",
  window_end: "still armed at the record edge",
  dearmed_other: "de-armed (see de-arm day)",
};

/** One de-arm token → its plain-English label; an unknown token returns itself (never a guess). */
export function closeReasonLabel(token: string): string {
  return CLOSE_REASON_LABEL[token] ?? token;
}

/** The close-reason line WITH the Slice-C composed detail: a `dearmed_other` close that carries a
 *  backend-authored `dearm_detail` reads "de-armed — <detail>" — the "(see de-arm day)" placeholder
 *  replaced by the actual answer (one authority for the copy: the backend composes, this only
 *  renders). Every other case — the four self-explaining tokens, or an opaque de-arm the record
 *  couldn't explain — falls through to `closeReasonLabel` unchanged, so no render site loses the
 *  raw-token-in-`title=` discipline. */
export function closeReasonLine(token: string, detail: string | null | undefined): string {
  if (token === "dearmed_other" && detail) return `de-armed — ${detail}`;
  return closeReasonLabel(token);
}

/** The drawer's ONE ingest-provenance line — null (render nothing at all) when the arm's ingest was
 *  healthy. Loudness marks the exception (#7): a line under every episode saying "ingest fine" would
 *  carry no information, so the healthy case is silence. Flagged means any of the three wire signals:
 *  the rollup `ingest_flagged`, the 2026-07 EDGAR freeze window, or an explicitly stale arm-date run
 *  (`arm_ingest_fresh === false` — a null is UNKNOWN, and unknown is not a judgement). The server's
 *  composed `ingest_note` is the "why" verbatim where it exists; the thaw lag rides beside it as the
 *  measured number (#6 — the flag always shows its work). */
export function ingestProvenanceLine(e: ScoreboardEpisodeOut): string | null {
  const flagged = e.ingest_flagged || e.freeze_era || e.arm_ingest_fresh === false;
  if (!flagged) return null;
  const parts = [e.ingest_note ?? "the arm rested on partial or late-ingested data"];
  if (e.thaw_lag_days != null) parts.push(`worst source lag ${e.thaw_lag_days}d`);
  return `ingest provenance: ${parts.join(" · ")}`;
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

/** The maturity-horizon countdown line (2e) — null when nothing lies ahead (no line at all, not an
 *  empty shell). Asof-pure: derived fields, coherent on a scrubbed view too. The projection wording
 *  stays honest — over currently-recorded episodes; "not reachable" when the clean pool can't get
 *  the eligible count to min_n. */
export function maturityHorizon(s: ScoreboardSummaryOut): string | null {
  if (s.next_maturity == null) return null;
  const projection =
    s.projected_min_n_date != null
      ? `first metric could clear n ≥ ${s.min_n} around ${s.projected_min_n_date}`
      : `n ≥ ${s.min_n} not reachable from current episodes`;
  return `next episode matures ${s.next_maturity} · ${s.n_maturing_30d} mature within 30d · ${projection}`;
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

// -------- Slice 2: the Summary | Timing ledger view ------------------------------------------------

/** Which lens the ledger renders — a VIEW control (swaps the middle columns), never a data change. */
export type LedgerView = "summary" | "timing";

/** The ledger's column count for the current view — the group-row/note-row `colSpan` tracks it so a
 *  full-width group header spans exactly the rendered columns (Summary = 8, Timing = 6). */
export function ledgerColCount(view: LedgerView): number {
  return view === "timing" ? 6 : 8;
}
