import { dashboardPath } from "../../nav";
import { STATE_LABEL, fmtDate, todayISO } from "../../util/format";
import { leftOutWords } from "../FlaggedNote";
import type {
  DailyDigestOut,
  DigestBuyRowOut,
  DigestCardOut,
  DigestFormingOut,
  DigestHoldOut,
  DigestDueItemOut,
  DigestLastSessionOut,
  DigestLineOut,
  DigestNightsOut,
  DigestPositionOut,
  DigestSinceFillOut,
  DigestTapeOut,
  DigestTapeRowOut,
} from "./useDailyDigest";

// The Daily Digest's pure view (docs/dashboards/daily-digest.md): the section order and their links, the words of
// every line, and the tape's cut. EVERY rule is computed server-side (the card grouping and its Armed-first order,
// the tone of a line, the five largest rises and falls, the sessions, the returns); this module only words and
// selects what the payload carries. It re-derives nothing a source page owns.

export type SectionKey = "last_night" | "positions" | "due" | "insider" | "tape" | "forming";

/** The page's sections, in order. `links` = the full pages a section's header opens (each carries ?asof=). */
export const SECTIONS: readonly {
  key: SectionKey;
  title: string;
  links: readonly { slug: string; label: string }[];
}[] = [
  { key: "last_night", title: "Last night", links: [{ slug: "what-changed", label: "What Changed" }] },
  { key: "positions", title: "Positions", links: [] },
  { key: "due", title: "Due this week", links: [{ slug: "catalyst-calendar", label: "Catalyst Calendar" }] },
  { key: "insider", title: "Insider buys", links: [{ slug: "insider-tracker", label: "Insider Buying" }] },
  {
    key: "tape",
    title: "The tape",
    links: [
      { slug: "hot-board", label: "Hot Board" },
      { slug: "entry-windows", label: "Entry Windows" },
    ],
  },
  { key: "forming", title: "Forming", links: [{ slug: "forming-quietly", label: "Forming Quietly" }] },
];

/** What the page says it is, once, above the sections. */
export const FRAMING =
  "Each section is a cut of a page that already exists, read by that page's own rules: the numbers here are the same numbers there.";

export const LIMITS: readonly string[] = [
  "The digest is a cut of other pages, never a judgment: the lines are the record's own labels and computed figures, and nothing here says what to do.",
  "Catalysts and kill criteria are edited in place with no history, so a past morning reads today's catalysts and kill criteria against that day's cards.",
  "A weekend or holiday morning repeats the last recorded night: nothing newer is recorded, and the page says so.",
  "Returns are as-traded closes. A split between a logged fill and the as-of replaces the logged price with the first close on or after the fill date.",
  "The tape reads closes only: the Hot Board's heat, relative volume and relative strength are on the Hot Board.",
  "Context and Bench names never count in the tape or the insider buys.",
];

export const RECONSTRUCTED_TITLE =
  "this card's newest row was written by pipeline.backfill: a reconstruction of a missed night, not the nightly's own record";
export const INGEST_TITLE =
  "the run that wrote this card's row did not ingest every name cleanly, so a figure behind it may be a night old";

export const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

// ---------------------------------------------------------------------------------------------------------
// dates, links, numbers
// ---------------------------------------------------------------------------------------------------------

/** "Thu Sep 17": the weekday and the day, so a Monday morning visibly reads Friday's night. */
export function dayWord(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = Date.parse(`${iso}T00:00:00Z`);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
}

/** The ?asof= a link carries: a day other than today. Today is what an absent param already reads, so a live visit
 *  keeps clean URLs (the app's own rule: `asofParam` is null when the URL names none). */
export const asofParam = (asof: string): string | null => (asof === todayISO() ? null : asof);

/** A section header's link to a full page (a "now" page ignores the param). */
export const sectionHref = (slug: string, asof: string): string => dashboardPath(slug, asofParam(asof));

/** "+1.23%", "-0.50%", "0.00%"; "—" when there is none. */
export function signed(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return "—";
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

/** The sign tone's class: only on a signed return (invariant #7: the one tone on a return, never on a state). */
export const signClass = (pct: number | null | undefined): string =>
  pct === null || pct === undefined || pct === 0 ? "" : pct > 0 ? "dd-pos" : "dd-neg";

const usd = (n: number, digits = 2) =>
  `$${n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
const shares = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });

// ---------------------------------------------------------------------------------------------------------
// the night pair
// ---------------------------------------------------------------------------------------------------------

export function nightsWords(n: DigestNightsOut): string {
  if (!n.latest) return "No night is recorded by this date.";
  if (!n.previous) return `The night of ${dayWord(n.latest)} · no earlier night to compare.`;
  const between = n.sessions_between === null ? "" : ` · ${plural(n.sessions_between, "session", "sessions")} between`;
  return `The night of ${dayWord(n.latest)}, against ${dayWord(n.previous)}${between}.`;
}

/** Said only when the as-of is past the newest recorded night (a weekend, a holiday, the morning before the pass). */
export function afterNightNote(n: DigestNightsOut): string | null {
  if (!n.latest || !n.days_after_night || n.days_after_night < 1) return null;
  return `${dayWord(n.asof)} is ${plural(n.days_after_night, "day", "days")} past that night. Nothing newer is recorded, so the page repeats it.`;
}

// ---------------------------------------------------------------------------------------------------------
// 1. last night
// ---------------------------------------------------------------------------------------------------------

const stateWord = (s: string | null | undefined) => (s ? (STATE_LABEL[s] ?? s) : "—");

/** The card's headline: its state move ("Warming → Armed"), "new card · Armed", else the state it stands in. */
export function headlineWords(card: DigestCardOut): string {
  const h = card.headline;
  if (h?.change === "state") return `${stateWord(h.from_state)} → ${stateWord(h.to_state)}`;
  if (h?.change === "new_card") return `new card · ${stateWord(h.to_state)}`;
  return stateWord(card.state);
}

/** label · kind · event date: what a trigger line names after its verb. */
function signal(l: DigestLineOut): string {
  return [l.label, l.signal_kind?.replace(/_/g, " "), l.on ? fmtDate(l.on) : null].filter(Boolean).join(" · ");
}

/** The line's own words, neutral throughout (What Changed's wording: a trigger that stopped is "no longer firing",
 *  one still firing on a newer event date is "re-dated"). */
export function lineWords(l: DigestLineOut): string {
  switch (l.change) {
    case "key_on":
      return `${l.label} key turned on`;
    case "key_off":
      return `${l.label} key turned off`;
    case "armed":
      return "newly armed";
    case "no_longer_armed":
      return "no longer armed";
    case "new_watch":
      return "new on watch";
    case "trigger_new":
      return `fired · ${signal(l)}`;
    case "trigger_gone":
      return `no longer firing · ${signal(l)}`;
    case "risk_new":
      return `new risk signal · ${signal(l)}`;
    case "trigger_redated":
      return `re-dated · ${[l.label, l.signal_kind?.replace(/_/g, " ")].filter(Boolean).join(" · ")} · ${fmtDate(l.previous_on)} → ${fmtDate(l.on)}`;
    case "entry_window": {
      const days = l.days ?? 0;
      return days === 0
        ? `entry window closes on the night · ${fmtDate(l.on)}`
        : `entry window closes in ${plural(days, "day", "days")} · ${fmtDate(l.on)}`;
    }
    case "exit_by":
      return `exit-by reached · ${fmtDate(l.on)}`;
    case "state":
      return `${stateWord(l.from_state)} → ${stateWord(l.to_state)}`;
    case "new_card":
      return `new card · ${stateWord(l.to_state)}`;
  }
}

/** The ONE loud rule, as a class: the server decides which lines carry a tone (accent: entered Armed, newly armed,
 *  an entry window closing within 3 days; warm: left Armed, an exit-by reached). */
export const toneClass = (tone: DigestLineOut["tone"]): string =>
  tone === "accent" ? "dd-tone-accent" : tone === "warm" ? "dd-tone-warm" : "";

/** The Cockpit key of a line's name: its ticker, else its security id. */
export const nameKey = (l: DigestLineOut): string | null => l.ticker ?? l.security_id ?? null;

// The fold (honest loudness, Workbench principle 3: the common noise collapses, the exception stays a line). MEASURED on
// a dev copy of prod, two consecutive nights differ by about 400 What Changed lines, and nearly all of them are the nightly
// churn on nearly every Armed card: a trigger re-dated, a trigger new or gone, a new risk signal, a name new on watch. Per
// card those lines fold into ONE quiet count line that opens in place and closes again (nothing is removed). The server's
// tone rule never marks one of these kinds (a test reads `tone_of` and pins it), so no accent or warm line is ever folded.

/** The kinds of line a card folds, in the fixed order its count line words them. */
export const FOLDED_CHANGES = ["trigger_redated", "trigger_new", "trigger_gone", "risk_new", "new_watch"] as const;
export type FoldedChange = (typeof FOLDED_CHANGES)[number];

export const isFolded = (l: DigestLineOut): boolean => (FOLDED_CHANGES as readonly string[]).includes(l.change);

/** The count line's words for one kind (a count of 1 reads in the singular). */
const FOLD_WORDS: Record<FoldedChange, (n: number) => string> = {
  trigger_redated: (n) => `${plural(n, "trigger", "triggers")} re-dated`,
  trigger_new: (n) => `${plural(n, "trigger", "triggers")} new`,
  trigger_gone: (n) => `${n.toLocaleString("en-US")} gone`,
  risk_new: (n) => `${plural(n, "risk signal", "risk signals")} new`,
  new_watch: (n) => `${n.toLocaleString("en-US")} new on watch`,
};

export type CardFold = {
  /** The lines that stay lines, in the server's order (the state moves, keys, names armed, the clocks). */
  shown: DigestLineOut[];
  /** The folded lines, in the server's order (opened in place by the count line). */
  folded: DigestLineOut[];
  /** The count line ("12 triggers re-dated · 4 triggers new · 1 gone · 9 risk signals new · 2 new on watch": only the
   *  non-zero parts, in that order), `null` when the card folds nothing. */
  words: string | null;
};

/** One card's lines split into the lines that stay and the lines that fold into its count line. */
export function foldCard(card: DigestCardOut): CardFold {
  const shown = card.lines.filter((l) => !isFolded(l));
  const folded = card.lines.filter(isFolded);
  const parts = FOLDED_CHANGES.flatMap((c) => {
    const n = folded.filter((l) => l.change === c).length;
    return n > 0 ? [FOLD_WORDS[c](n)] : [];
  });
  return { shown, folded, words: parts.length > 0 ? parts.join(" · ") : null };
}

/** How many lines of section 1 fold into counts (every card's, with its headline never one of them). */
export const foldedLines = (cards: readonly DigestCardOut[]): number =>
  cards.reduce((n, c) => n + c.lines.filter(isFolded).length, 0);

/** The section header's count: the cards, every line the server carries and how many of them folded into counts. */
export function lastNightCount(l: DailyDigestOut["last_night"]): string | null {
  if (l.no_baseline || l.cards.length === 0) return null;
  const folded = foldedLines(l.cards);
  const base = ` · ${plural(l.cards_with_change, "card", "cards")} · ${plural(l.lines, "line", "lines")}`;
  return folded > 0 ? `${base} · ${folded.toLocaleString("en-US")} folded into counts` : base;
}

// ---------------------------------------------------------------------------------------------------------
// 2. positions
// ---------------------------------------------------------------------------------------------------------

export function fillWords(p: DigestPositionOut): string {
  if (!p.has_position) return "no position logged as of this date";
  const day = p.opened_on ? `filled ${fmtDate(p.opened_on)}` : "fill date not logged";
  return p.logged_price !== null ? `${day} at ${usd(p.logged_price)}` : `${day} · no price logged`;
}

/** Shares times price, labeled as the logged fill; only when both were logged. */
export function loggedFillWords(p: DigestPositionOut): string | null {
  if (p.fill_usd === null || p.shares === null || p.logged_price === null) return null;
  return `logged fill ${shares(p.shares)} sh × ${usd(p.logged_price)} = ${usd(p.fill_usd, 0)}`;
}

export type Fig = { text: string; cls: string; title?: string; sub?: string };

/** The return since the fill, with the start it used said out loud ("from the logged price", "from the first close
 *  Sep 11"); a name left out for a flagged price history reads the flag's reason. */
export function sinceFillFig(f: DigestSinceFillOut): Fig {
  if (f.status === "flagged") return { text: "—", cls: "dd-gray", sub: leftOutWords(f.reason ?? "flagged price history") };
  if (f.status !== "priced") return { text: "—", cls: "dd-gray", sub: f.reason ?? undefined };
  const basis =
    f.basis === "logged_price"
      ? "from the logged price"
      : `from the first close ${fmtDate(f.basis_date)}`;
  const from =
    f.basis === "logged_price"
      ? `${usd(f.basis_price ?? 0)} (the logged fill price)`
      : `${usd(f.basis_price ?? 0)} (the close on ${fmtDate(f.basis_date)})`;
  const iwm = f.bench_pct === null ? "no IWM bar on one of those dates" : `IWM ${signed(f.bench_pct)} over the same dates`;
  return {
    text: signed(f.ret_pct),
    cls: signClass(f.ret_pct),
    sub: f.note ? `${basis} · ${f.note}` : basis,
    title: `${from} to ${usd(f.last_close ?? 0)} (the close on ${fmtDate(f.last_date)}); ${iwm}`,
  };
}

/** The last session's return against IWM's. */
export function lastSessionFig(l: DigestLastSessionOut): Fig {
  if (l.status === "flagged") return { text: "—", cls: "dd-gray", sub: leftOutWords(l.reason ?? "flagged price history") };
  if (l.status !== "priced") return { text: "—", cls: "dd-gray", sub: l.reason ?? undefined };
  return {
    text: signed(l.ret_pct),
    cls: signClass(l.ret_pct),
    sub: l.bench_pct === null ? undefined : `IWM ${signed(l.bench_pct)}`,
    title: `${dayWord(l.previous_session)} to ${dayWord(l.session)}`,
  };
}

export function holdWords(h: DigestHoldOut): string {
  if (!h.exit_by) return "no exit-by";
  if (h.sessions_left !== null) {
    return h.sessions_left === 0
      ? `exit-by ${fmtDate(h.exit_by)} · its last day`
      : `exit-by ${fmtDate(h.exit_by)} · ${plural(h.sessions_left, "session", "sessions")} left`;
  }
  const ago = h.passed_sessions ?? 0;
  return ago === 0
    ? `exit-by ${fmtDate(h.exit_by)} · passed`
    : `exit-by ${fmtDate(h.exit_by)} · passed ${plural(ago, "session", "sessions")} ago`;
}

export function riskWords(p: DigestPositionOut): string {
  if (p.risk_signals === 0) return "no risk signals";
  const base = plural(p.risk_signals, "risk signal", "risk signals");
  return p.risk_new > 0 ? `${base} · ${p.risk_new} new last night` : base;
}

export const killWords = (p: DigestPositionOut): string =>
  p.kill_criteria.length === 0 ? "no kill criteria" : plural(p.kill_criteria.length, "kill criterion", "kill criteria");

/** The kill criteria in the operator's own words, verbatim, one per line: the hover. */
export const killTitle = (p: DigestPositionOut): string | undefined =>
  p.kill_criteria.length === 0 ? undefined : p.kill_criteria.join("\n");

// ---------------------------------------------------------------------------------------------------------
// 3. due this week
// ---------------------------------------------------------------------------------------------------------

export const dueWhen = (i: DigestDueItemOut): string =>
  i.today ? "today" : `${dayWord(i.on)} · in ${plural(i.days, "day", "days")}`;

export function dueWhat(i: DigestDueItemOut): string {
  if (i.item === "entry_window") return "entry window ends";
  if (i.item === "exit_by") return "exit-by";
  return [i.label, i.kind].filter(Boolean).join(" · ");
}

/** The quiet count under the bucket: catalysts that passed after P and are not yet pruned. */
export function passedWords(data: DailyDigestOut): string | null {
  const since = data.due.passed_since;
  if (since === null || !data.nights.previous) return null;
  return `${plural(since, "catalyst", "catalysts")} passed since ${dayWord(data.nights.previous)} and ${since === 1 ? "is" : "are"} not yet pruned (${data.due.passed_total.toLocaleString("en-US")} passed in all).`;
}

// ---------------------------------------------------------------------------------------------------------
// 4. insider buys
// ---------------------------------------------------------------------------------------------------------

export function buyerWords(r: DigestBuyRowOut): string {
  if (!r.filer) return "filer not named";
  const who = r.role ? `${r.filer} (${r.role})` : r.filer;
  return r.co_filers > 0 ? `${who} and ${r.co_filers} more` : who;
}

/** "1,000 sh @ $4.00 = $4,000": the reader's own code-P summary; a part the filing does not state is left out. */
export function sizeWords(r: DigestBuyRowOut): string {
  const parts: string[] = [];
  if (r.shares !== null) parts.push(r.price !== null ? `${shares(r.shares)} sh @ ${usd(r.price)}` : `${shares(r.shares)} sh`);
  if (r.usd !== null) parts.push(`${r.shares !== null ? "= " : ""}${usd(r.usd, 0)}`);
  return parts.join(" ") || "size not stated";
}

export function filedWords(r: DigestBuyRowOut): string {
  const filed = `filed ${fmtDate(r.filed)}`;
  if (!r.trade_date || r.filed_days_after === null) return filed;
  return `${filed} · traded ${fmtDate(r.trade_date)}, filed ${plural(r.filed_days_after, "day", "days")} after`;
}

export const clusterWord = (r: DigestBuyRowOut): string | null => (r.cluster ? `cluster · ${r.cluster.buyers} buyers` : null);

export const clusterTitle = (r: DigestBuyRowOut): string | undefined =>
  r.cluster ? r.cluster.others.map((o) => `${o.name}, filed ${fmtDate(o.filed)}`).join("\n") : undefined;

export function triggerWords(r: DigestBuyRowOut): string {
  if (r.triggers.length === 0) return "not a trigger";
  return `became a trigger on ${r.triggers.map((t) => t.thesis_name).join(", ")}`;
}

export function insiderEmpty(data: DailyDigestOut): string {
  const i = data.insider;
  if (i.no_baseline) return "No earlier night to compare.";
  return `No insider buys filed since ${dayWord(i.span_start)}.`;
}

// ---------------------------------------------------------------------------------------------------------
// 5. the tape
// ---------------------------------------------------------------------------------------------------------

/** The rows on show: the largest rises and falls the server flagged, in its order; every armed name when asked. The
 *  toggle is only worth rendering when `hidden` > 0 (honest loudness: a control that cannot tell rows apart does not
 *  render). */
export function tapeRows(tape: DigestTapeOut, showAll: boolean): { shown: DigestTapeRowOut[]; extra: number } {
  const cut = tape.rows.filter((r) => r.cut !== null);
  // `extra` = the rows the cut leaves behind the toggle, whichever way the toggle is set (it names what it adds)
  return { shown: showAll ? [...tape.rows] : cut, extra: tape.rows.length - cut.length };
}

export function tapeNote(tape: DigestTapeOut): string {
  const c = tape.counts;
  const span = tape.session ? `${dayWord(tape.previous_session)} to ${dayWord(tape.session)}` : "no session";
  const iwm = tape.iwm_pct === null ? "" : ` · IWM ${signed(tape.iwm_pct)}`;
  return `Largest last-session rises and falls among ${plural(c.names_priced, "priced name", "priced names")} · ${span}${iwm}`;
}

/** The two clocks the Hot Board shows: the sessions since the arm night, then the entry window's sessions left. */
export function clockWords(r: DigestTapeRowOut): string {
  const since = r.sessions_since === null ? "—" : `${r.sessions_since} in`;
  if (r.sessions_left !== null) return `${since} · ${r.sessions_left} left`;
  if (r.closed_ago !== null) return `${since} · window closed ${plural(r.closed_ago, "session", "sessions")} ago`;
  return since;
}

export function sinceArmFig(r: DigestTapeRowOut): Fig {
  const s = r.since_arm;
  if (s.ret_pct !== null) {
    return {
      text: signed(s.ret_pct),
      cls: signClass(s.ret_pct),
      sub: s.iwm_pct === null ? undefined : `IWM ${signed(s.iwm_pct)}`,
      title: `${usd(s.entry_close ?? 0)} on ${fmtDate(s.entry_date)} to ${usd(s.last_close ?? 0)} on ${fmtDate(s.last_date)}`,
    };
  }
  const why: Record<string, string> = {
    no_arm_night: "no arm night",
    before_window: "arm night before the price window",
    no_bar: "no close since the arm night",
  };
  if (s.gap === "flagged") return { text: "—", cls: "dd-gray", sub: leftOutWords("flagged price history") };
  return { text: "—", cls: "dd-gray", sub: s.gap ? why[s.gap] : undefined };
}

// ---------------------------------------------------------------------------------------------------------
// 6. forming
// ---------------------------------------------------------------------------------------------------------

export function formingWords(f: DigestFormingOut): { warming: string; incubating: string; turned: string | null } {
  return {
    warming: plural(f.warming, "Warming card", "Warming cards"),
    incubating: plural(f.incubating, "Incubating card", "Incubating cards"),
    turned: f.key_turned.length === 0 ? null : "key turned last night:",
  };
}

// ---------------------------------------------------------------------------------------------------------
// the work lines
// ---------------------------------------------------------------------------------------------------------

/** The quiet "show the work" lines: what the page rests on, every count off the payload. */
export function workLines(data: DailyDigestOut): string[] {
  const c = data.counts;
  const t = data.tape.counts;
  const b = data.insider.counts;
  const lines: string[] = [];
  lines.push(
    `As of ${dayWord(data.asof)} · ${data.clock === "public" ? "the public clock: what was public by the end of that date" : "the live record"} · ${nightsWords(data.nights)}`,
  );
  const scope = [
    `${plural(c.cards_counted, "card", "cards")} counted of ${c.live_cards.toLocaleString("en-US")} live`,
    c.not_yet_created > 0 ? `${c.not_yet_created} created after the as-of` : null,
    c.roster_fallback > 0 ? `${c.roster_fallback} on today's roster (no snapshot by then)` : null,
  ].filter(Boolean);
  lines.push(scope.join(" · "));
  lines.push(
    [
      `${c.cards_recorded_on_night} recorded on the night`,
      `${c.cards_carried} carried from an earlier night`,
      `${c.cards_no_call} with no call yet`,
      `${c.cards_armed} Armed · ${c.cards_warming} Warming · ${c.cards_managing} Managing · ${c.cards_incubating} Incubating`,
      `${c.cards_ingest_not_fresh} ingest not fresh`,
      `${c.cards_reconstructed} reconstructed`,
    ].join(" · "),
  );
  lines.push(
    [
      `tape: ${plural(t.rows, "armed row", "armed rows")} on ${plural(t.names, "name", "names")}`,
      `${t.names_priced} priced · ${t.names_unpriced} unpriced · ${t.names_flagged} flagged`,
      `${t.since_arm_blank} with no move since the arm night`,
    ].join(" · "),
  );
  lines.push(
    data.insider.no_baseline
      ? "insider buys: no earlier night, so no span"
      : [
          `insider buys: ${plural(b.filings_read, "Form 4 filing", "Form 4 filings")} filed in the span (${dayWord(data.insider.span_start)} to ${dayWord(data.insider.span_end)}) on ${plural(b.names_read, "name", "names")}`,
          `${b.purchases} purchases · ${b.routine_folded} with no purchase · ${b.amendments_no_purchase} amendments with none`,
          `${b.silent_rows_left_out} Context and Bench roster rows left out`,
        ].join(" · "),
  );
  lines.push(`${data.cache === "hit" ? "from the cache" : "computed"} · ${data.compute_ms.toLocaleString("en-US")} ms`);
  return lines;
}
