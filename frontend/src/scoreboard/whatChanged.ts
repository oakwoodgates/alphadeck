import type {
  EditionCompanyDiffOut,
  EditionCutNoticeOut,
  EditionDiffCountsOut,
  EditionEpisodeDiffOut,
  EditionEpisodeSideOut,
  EditionNightReadOut,
  EditionThesisDiffOut,
  ScoreboardEditionOut,
} from "../api/hooks";
import { fmtDate, verdictLabel } from "../util/format";

// "WHAT CHANGED" (editions S6) — pure copy for the comparison view: one edition against the one before it,
// said in plain English, thesis -> company, with the counts on top. Everything here reads the server's
// structured diff (`GET /scoreboard/editions/diff`) and computes nothing about the calls themselves. Quiet by
// design (#7): a comparison is context, never an alarm, so no line here carries a color or an urgency word.
// Copy lives here so it is testable on its own.

type EditionRef = Pick<ScoreboardEditionOut, "number" | "label">;

/** Who is who in a sentence: "Edition 0" / "Edition 1". */
export type Names = { before: string; after: string };

export function editionName(e: Pick<ScoreboardEditionOut, "number">): string {
  return `Edition ${e.number}`;
}

export function namesFor(before: EditionRef, after: EditionRef): Names {
  return { before: editionName(before), after: editionName(after) };
}

/** The edition the viewed one is compared with: the newest edition BEFORE it. null on Edition 0 (nothing
 *  comes before the record) and whenever fewer than two editions exist — the link does not render then. */
export function previousEdition(
  editions: readonly ScoreboardEditionOut[],
  served: number,
): ScoreboardEditionOut | null {
  if (editions.length < 2) return null;
  const earlier = editions.filter((e) => e.number < served);
  if (!earlier.length) return null;
  return earlier.reduce((a, b) => (b.number > a.number ? b : a));
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "Jul 9" or "Jul 9 – Jul 22" — a span of nights, with its count when it is more than one. */
export function nightSpan(first: string, last: string, n: number): string {
  return first === last ? fmtDate(first) : `${fmtDate(first)} – ${fmtDate(last)} · ${plural(n, "night")}`;
}

const FIELD_LABEL: Record<string, string> = {
  verdict: "verdict",
  entry_grade: "entry grade",
  conviction_grade: "conviction grade",
  exit_by: "exit-by",
  dearm_date: "de-armed",
  forward_return: "score",
};

function fieldValue(field: string, v: string | number | null | undefined): string {
  if (v == null || v === "") return field === "dearm_date" ? "still armed" : "—";
  if (field === "verdict") return verdictLabel(String(v));
  if (field === "exit_by" || field === "dearm_date") return fmtDate(String(v));
  return String(v);
}

/** One folded run of changed company-nights: "armed in Edition 1 only" or "entry grade core → flip". The
 *  arrow always reads earlier edition → later edition. */
export function nightChange(
  changed: readonly string[],
  before: EditionNightReadOut,
  after: EditionNightReadOut,
  names: Names,
): string {
  if (changed.includes("armed")) {
    return after.armed ? `armed in ${names.after} only` : `armed in ${names.before} only`;
  }
  return changed
    .map((f) => {
      const k = f as keyof EditionNightReadOut;
      return `${FIELD_LABEL[f] ?? f} ${fieldValue(f, before[k] as string | null)} → ${fieldValue(f, after[k] as string | null)}`;
    })
    .join(" · ");
}

/** A score as plain text — signed percent, no color (a comparison is never an alarm). */
export function scoreText(x: number | null | undefined): string {
  if (x == null) return "not scored yet";
  const pct = (x * 100).toFixed(1);
  return x > 0 ? `+${pct}%` : `${pct}%`;
}

/** A difference of two scores, in percentage points. */
export function deltaText(x: number | null | undefined): string | null {
  if (x == null) return null;
  const pts = (x * 100).toFixed(1);
  return `${x > 0 ? "+" : ""}${pts} pts`;
}

function runEnd(s: EditionEpisodeSideOut): string {
  return s.dearm_date ? `de-armed ${fmtDate(s.dearm_date)}` : "still armed";
}

function shiftText(days: number): string {
  if (days === 0) return "the same night";
  const n = Math.abs(days);
  return `${plural(n, "day")} ${days < 0 ? "earlier" : "later"}`;
}

/** One episode change, in a sentence. Each side's score is arm → exit-by on realized closes through the
 *  comparison's last night; a pair adds the difference. ``windowStart`` = the thesis's first compared night,
 *  named when an unpaired run armed before it (on a night the other edition does not cover). */
export function episodeLine(
  ep: EditionEpisodeDiffOut,
  names: Names,
  windowStart?: string | null,
): string {
  const b = ep.before;
  const a = ep.after;
  const early =
    ep.armed_before_window && windowStart
      ? ` · armed before the nights both editions cover (from ${fmtDate(windowStart)})`
      : "";
  if (ep.change === "appeared" && a) {
    return (
      `${names.after} arms it ${fmtDate(a.arm_date)} (${runEnd(a)}); ${names.before} did not${early} · ` +
      `score ${scoreText(a.forward_return)}`
    );
  }
  if (ep.change === "vanished" && b) {
    return (
      `${names.before} armed it ${fmtDate(b.arm_date)} (${runEnd(b)}); ${names.after} does not${early} · ` +
      `score ${scoreText(b.forward_return)}`
    );
  }
  if (!a || !b) return ep.change;
  const delta = deltaText(ep.return_delta);
  const score =
    a.forward_return != null && a.forward_return === b.forward_return
      ? `score unchanged at ${scoreText(a.forward_return)}`
      : `score ${scoreText(b.forward_return)} → ${scoreText(a.forward_return)}` + (delta ? ` (${delta})` : "");
  if (ep.change === "moved") {
    const midArm = b.began_mid_arm
      ? ` · ${names.before} began mid-arm on ${fmtDate(b.arm_date)}, its first night`
      : a.began_mid_arm
        ? ` · ${names.after} began mid-arm on ${fmtDate(a.arm_date)}, its first night`
        : "";
    return (
      `armed ${fmtDate(b.arm_date)} in ${names.before}, ${fmtDate(a.arm_date)} in ${names.after} ` +
      `(${shiftText(ep.arm_shift_days ?? 0)})${midArm} · ${score}`
    );
  }
  // matched: the same arm night, a different run
  const parts = (ep.differs ?? [])
    .filter((f) => f !== "forward_return")
    .map((f) => {
      const k = f as keyof EditionEpisodeSideOut;
      return `${FIELD_LABEL[f] ?? f} ${fieldValue(f, b[k] as string | null)} → ${fieldValue(f, a[k] as string | null)}`;
    });
  return [`same arm ${fmtDate(a.arm_date)}`, ...parts, score].join(" · ");
}

/** The headline, one short line each: theses and companies, company-nights, episodes. */
export function countsLines(c: EditionDiffCountsOut, names: Names): string[] {
  const lines = [
    `${c.n_theses_changed} of ${plural(c.n_theses, "thesis", "theses")} changed · ` +
      `${plural(c.n_companies_changed, "company", "companies")}`,
    `${plural(c.n_company_nights_changed, "company-night")} differ: ${c.n_armed_after_only} armed in ` +
      `${names.after} only · ${c.n_armed_before_only} armed in ${names.before} only · ${c.n_fields_changed} ` +
      `armed in both with a different verdict, grade or exit-by`,
    `episodes: ${c.n_episodes_appeared} appeared · ${c.n_episodes_vanished} vanished · ${c.n_episodes_moved} ` +
      `moved · ${c.n_episodes_matched} on the same arm night (${c.n_episodes_matched_changed} of them changed)`,
  ];
  if (c.n_episodes_outside > 0) {
    lines.push(
      `${plural(c.n_episodes_outside, "episode")} armed on nights only one edition covers · counted, not compared`,
    );
  }
  return lines;
}

/** The thesis's compared window and its quiet receipts. */
export function thesisLine(t: EditionThesisDiffOut): string {
  if (!t.first_night || !t.last_night) return "no night both editions cover";
  const parts = [`compared ${nightSpan(t.first_night, t.last_night, t.n_nights)}`];
  if (t.n_state_nights > 0) parts.push(`thesis state differs on ${plural(t.n_state_nights, "night")}`);
  if (t.n_matched > 0) parts.push(`${plural(t.n_matched, "episode")} on the same arm night`);
  if (t.n_companies_unchanged > 0) {
    parts.push(`${plural(t.n_companies_unchanged, "other armed company", "other armed companies")} unchanged`);
  }
  return parts.join(" · ");
}

/** The nights only one edition holds for this thesis (before the other began, or after its last page) — said
 *  once, quietly, with the arms there that are counted but never compared. null when there are none. */
export function outsideLine(t: EditionThesisDiffOut, names: Names): string | null {
  const spans = [
    ...(t.before_only ?? []).map((s) => `${names.before} alone covers ${nightSpan(s.first, s.last, s.n_nights)}`),
    ...(t.after_only ?? []).map((s) => `${names.after} alone covers ${nightSpan(s.first, s.last, s.n_nights)}`),
  ];
  if (!spans.length) return null;
  const arms = t.n_outside > 0 ? ` · ${plural(t.n_outside, "episode")} armed there, not compared` : "";
  return spans.join(" · ") + arms;
}

/** WHY one change happened (editions S7), in plain English — only when the later edition was cut by the
 *  automatic cut from the earlier one, whose trial attributed it; null otherwise (a cause is never guessed).
 *  The codes are the server's (`scoreboard.diff`): what the trial proved, or what its scope implies. */
export function causeText(cause: string | null | undefined, names: Names): string | null {
  switch (cause) {
    case "rule_change":
      return "cause: the declared rule change";
    case "unexplained":
      return "cause: a change in the rules no declared rule explains";
    case "late_data":
      return `cause: data that arrived after ${names.before} was cut`;
    case "data_repair":
      return "cause: the data repair or data that arrived late (the two cannot be separated)";
    case "not_separated":
      return "cause: not separated (a rule change, a repair or data that arrived late; this night was not re-tested)";
    default:
      return null;
  }
}

/** The quiet note under the counts: whether the changes below carry their causes. */
export function causeNote(causes: boolean | undefined, names: Names): string {
  return causes
    ? `each change names its cause, from the trial that cut ${names.after}`
    : `no causes are shown: ${names.after} was not cut from ${names.before}, so no single trial speaks for the pair`;
}

/** The pair the cut notice's "see what changed" link compares (the new edition and the one it replaced),
 *  or null when either is missing from the list. */
export function noticePair(
  editions: readonly ScoreboardEditionOut[],
  notice: EditionCutNoticeOut | null | undefined,
): [ScoreboardEditionOut, ScoreboardEditionOut] | null {
  if (!notice) return null;
  const before = editions.find((e) => e.number === notice.previous);
  const after = editions.find((e) => e.number === notice.number);
  return before && after ? [before, after] : null;
}

/** Companies in a stable, neutral order (ticker, then name) — never ranked by how much they moved (#4). */
export function sortCompanies(cs: readonly EditionCompanyDiffOut[]): EditionCompanyDiffOut[] {
  const key = (c: EditionCompanyDiffOut) => (c.ticker ?? c.company_name ?? c.security_id).toUpperCase();
  return [...cs].sort((x, y) => key(x).localeCompare(key(y)));
}
