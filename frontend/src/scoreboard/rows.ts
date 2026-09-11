import type {
  ScoreboardEpisodeOut,
  ScoreboardMetricOut,
  ScoreboardSummaryOut,
  ScoreboardThesisOut,
} from "../api/hooks";
import { fmtDate } from "../util/format";

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
 *  still reads "realized" (it overrides only the "running" outcome).
 *
 *  `insufficient_prices` covers TWO shapes and they get different words, because "awaiting" is a
 *  promise the second one cannot keep. On an immature episode a bar really is still coming. On a
 *  MATURED one the window has closed holding no bars at all, and nothing further will ever land in
 *  it — the real instance is an episode whose arm and exit-by are the same non-trading day. Telling
 *  the operator to await a bar that can no longer arrive is the wrong sentence, not a softer one. */
export function returnLabel(e: ScoreboardEpisodeOut): string {
  if (e.insufficient_prices)
    return e.matured ? "no bars in the scored window" : "awaiting first bar";
  if (e.status === "closed" && e.matured) return "realized";
  if (awaitingForwardBar(e)) return "awaiting forward bar";
  return "running";
}

/** One Why-cell chip: a trigger KIND, how many of that kind fired, and every one of their labels. */
export type TriggerChip = { kind: string; n: number; labels: string[] };

/** The Why cell's chips — one per DISTINCT kind, in first-appearance order (the record's own
 *  order, never re-sorted), carrying the count and every collapsed label.
 *
 *  An arm that fired `technical_breakout` four times said one thing four times; four identical
 *  chips rendered that as four words and spent four chips' width saying nothing new (#7 — a mark
 *  true of every item in a list carries no information). The count says the same thing in one
 *  chip. Nothing is dropped: the number is on the chip and every label rides its title, so the
 *  evidence behind each individual fire is still one hover away (#6).
 *
 *  Deliberately keyed on kind ALONE. Two fires of a kind can differ in grade or date, but neither
 *  was ever on the chip — only the kind was — so collapsing on kind hides nothing that was
 *  visible, and the labels preserve the difference for the hover. */
export function triggerChips(triggers: readonly { kind: string; label: string }[]): TriggerChip[] {
  const out: TriggerChip[] = [];
  const byKind = new Map<string, TriggerChip>();
  for (const t of triggers) {
    const seen = byKind.get(t.kind);
    if (seen) {
      seen.n += 1;
      seen.labels.push(t.label);
      continue;
    }
    const chip: TriggerChip = { kind: t.kind, n: 1, labels: [t.label] };
    byKind.set(t.kind, chip);
    out.push(chip);
  }
  return out;
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
  // The FIELD says the horizon outran this name's tape; the BADGE decides that is worth telling the
  // operator, and those are different questions. On a running episode falling short of the horizon is
  // the definition of running — the row already says so, in this same cell, via the absent MATURED
  // mark. Only on a MATURED episode is it a caveat: a number presented as final, measured on a tape
  // that stopped before the horizon it claims. Ungated it fired on 91% of the ledger, which is the
  // shape of a mark true of nearly every row (#7) — and every matured fire was a weekend or a market
  // holiday, where nothing had been missed at all. The field's own correction removed those; this
  // gate removes the running rows, and what is left is the exception the badge was always for.
  if (e.matured && e.truncated && !e.insufficient_prices)
    out.push({
      label: "TAPE ENDS",
      cls: "b-trunc",
      title: `the horizon elapsed, but this name's price tape stops at ${fmtDate(e.exit_date)}`,
    });
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

/** The LEDGER ROW's short form of the same tokens. The prose above is right for the drawer, the
 *  event ledger and the chart tooltip — places you READ. A ledger row is SCANNED, and there
 *  "de-armed (see de-arm day)" spent a third of the Status column deferring to somewhere else. */
const CLOSE_REASON_BADGE: Record<string, string> = {
  arm_until_lapsed: "WINDOW LAPSED",
  conviction_aged_out: "AGED OUT",
  managing: "MANAGING",
  window_end: "AT RECORD EDGE",
  dearmed_other: "DE-ARMED",
};

/** How the episode left the armed set, as a compact badge — null while it is still open.
 *
 *  It wears the MUTED tone deliberately. Every closed row has a close reason (208 of 252 on the
 *  current record), so this states a fact about the rule rather than flagging an exception; giving
 *  it an alert colour would make two thirds of the ledger shout (#7). It is a badge for SHAPE — one
 *  scannable chip instead of a trailing sentence — not for volume.
 *
 *  The full story rides the title: the backend's composed `dearm_detail` where one exists, then the
 *  raw wire token, so the English never hides what the record actually says (the `closeReasonLabel`
 *  discipline). That detail is why the short label costs nothing — "(see de-arm day)" was written
 *  when the row had no answer to give, and the backend has composed one ever since (MEASURED: all
 *  142 `dearmed_other` episodes on the current record carry a `dearm_detail`). The de-arm DAY it
 *  pointed at is on the row already — the `→ date` in the Armed column. */
export function closeReasonBadge(e: ScoreboardEpisodeOut): Badge | null {
  if (e.status !== "closed") return null;
  return {
    label: CLOSE_REASON_BADGE[e.close_reason] ?? e.close_reason,
    cls: "b-dearm",
    title: `${closeReasonLine(e.close_reason, e.dearm_detail)}\nwire: ${e.close_reason}`,
  };
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
 *  full-width group header spans exactly the rendered columns. A column has to be added in FOUR
 *  places — `LedgerHead`, `EpisodeRow`, `SpanRow` and here — and the whole-table span test is the
 *  net. */
const LEDGER_COLS: Record<LedgerView, number> = {
  summary: 9, // Name · Armed · De-armed · Why · Exit-by · Status · Return · Peak · Operator
  // 11: the excursion pair was promoted OUT of the hovers into four columns (Peak · Peak high ·
  // Worst · Worst low), each close figure adjacent to its own wick so the pair reads together.
  timing: 11, // Name · Armed · De-armed · Path · Return · Peak · Peak high · Worst · Worst low · Past peak · Status
};

export function ledgerColCount(view: LedgerView): number {
  return LEDGER_COLS[view];
}

// -------- the excursion quartet: four figures, four columns, four single-purpose hovers -------------

/** Which of the four excursion cells a hover is for: the side (favorable / adverse) × the basis
 *  (the CLOSE the return is measured on, or the WICK that actually traded). */
export type ExcursionSide = "peak" | "worst";
export type ExcursionBasis = "close" | "wick";

/** One excursion cell's hover. The wick figures used to ride the CLOSE cell's hover, because they
 *  were a ~2pp correction with no column of their own; now all four have columns, so each hover
 *  describes ONLY its own figure. A hover that restated its neighbour would be the repetition the
 *  columns were promoted to remove — and it is the neighbour's cell that answers for the neighbour.
 *
 *  Two degradations, and the distinction between them is the point (#6):
 *
 *  - **No close excursion at all** means the scored window held no bars — for EITHER basis. Say
 *    that, rather than blaming a missing wick for an empty window. (The real instance: the two
 *    episodes whose arm and exit_by both land on the same Sunday.)
 *  - **A missing wick with bars present** says so in as many words. It never falls back to the
 *    close and it never silently omits the line — an absent line would read as "the intraday
 *    extreme equals the close", which is the one thing that is certainly not true. */
export function excursionTitle(
  e: ScoreboardEpisodeOut,
  side: ExcursionSide,
  basis: ExcursionBasis,
): string {
  const isPeak = side === "peak";
  // The close excursion is present whenever the window held a bar, so it is the EMPTY-WINDOW probe
  // for both bases — a wick column asks it first, then its own field.
  const closeRet = isPeak ? e.peak_return : e.trough_return;
  if (closeRet == null) return "no bars in the scored window — nothing to measure";

  if (basis === "close") {
    const lines = [
      isPeak
        ? "maximum favorable excursion (MFE) — the best CLOSE in the scored window"
        : "maximum adverse excursion (MAE) — the worst CLOSE in the scored window",
    ];
    const closeDate = isPeak ? e.peak_date : e.trough_date;
    if (closeDate) lines.push(`on ${fmtDate(closeDate)}`);
    return lines.join("\n");
  }

  const wick = isPeak ? e.intraday_high_return : e.intraday_low_return;
  const wickDate = isPeak ? e.intraday_high_date : e.intraday_low_date;
  const name = isPeak ? "intraday high" : "intraday low";
  if (wick == null) {
    return (
      `${name} unavailable — not every bar in this window carries a wick, and the close is never ` +
      `substituted for one`
    );
  }
  const lines = [
    isPeak
      ? "the BEST price that actually traded — the intraday high"
      : "the WORST price that actually traded — the intraday low",
  ];
  if (wickDate) lines.push(`on ${fmtDate(wickDate)}`);
  return lines.join("\n");
}

// -------- the episode path: the SHAPE behind the endpoint numbers ------------------------------------

/** The Path cell's hover. Three things, in the order they matter:
 *
 *  1. The span, in bars and dates — because the paths are VARIABLE length (each episode covers its own
 *     `[arm_date, exit_date]`), the caveat that a steeper line does not mean a faster move has to be
 *     stated, not assumed. Fixed-slot padding would have implied a shared time axis these rows do not
 *     have; this is the honest cost of the alternative.
 *  2. The de-arm, when there is one. A de-arm that fell AFTER the scored window has no place on the
 *     path — and silently drawing nothing there would read as "never de-armed", which is false for the
 *     8 real episodes whose run outlived its own horizon. The line says which it is.
 *  3. Nothing else. */
export function pathTitle(e: ScoreboardEpisodeOut): string {
  const n = e.path?.length ?? 0;
  const lines: string[] =
    n === 0
      ? ["no bars in the scored window"]
      : n === 1
        ? [`1 bar in the scored window — a point is not a path`]
        : [
            `${n} bars · ${fmtDate(e.arm_date)} → ${fmtDate(e.exit_date)}`,
            "this episode's own span — the x-axis is not comparable between rows",
          ];
  if (e.dearm_date) {
    lines.push(
      e.dearm_index != null
        ? `de-armed ${fmtDate(e.dearm_date)} (marked)`
        : `de-armed ${fmtDate(e.dearm_date)} — after the scored window, so the mark has no place on ` +
          `this path`,
    );
  }
  return lines.join("\n");
}
