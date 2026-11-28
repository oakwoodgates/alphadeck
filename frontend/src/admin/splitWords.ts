// The Splits cards in PLAIN ENGLISH (operator ask, 2026-10-24): what the split is, what each source says, and what
// each button does. Pure words over the GET /admin/splits payload — never a different decision, only how it is
// explained (backend: app/routers/admin_splits.py; the verbs: repositories/split_repo.py).
import type {
  AdminSplitListEntryOut,
  AdminSplitTapeStepOut,
  AdminSplitUnplacedOut,
  AdminSplitUnplacedRunOut,
} from "../api/hooks";
import { safeHttpUrl } from "../components/ExternalLink";

type Split = AdminSplitListEntryOut;
type Option = NonNullable<Split["confirm_options"]>[number];

const md = (d: string) => d.slice(5); // "2026-07-06" -> "07-06"
const n = (x: string | number) => Number(x).toLocaleString("en-US", { maximumFractionDigits: 6 });

/** "every 15 shares became 1" / "every share became 2" — old shares -> new shares. */
export function sharesPhrase(ratioFrom: string, ratioTo: string): string {
  const from = Number(ratioFrom);
  const to = Number(ratioTo);
  const before = from === 1 ? "every share" : `every ${n(from)} shares`;
  return `${before} became ${n(to)}`;
}

/** "1-for-15 reverse split: every 15 shares became 1; first traded 2026-07-06." */
export function splitSentence(s: Split): string {
  return `${s.label}: ${sharesPhrase(s.ratio_from, s.ratio_to)}; first traded on the new basis ${s.effective_date}.`;
}

/** What the SEC filing says, in a sentence. */
export function secSays(s: Split): string {
  const sec = s.sec;
  if (!sec) return "No SEC filing in the window states this split.";
  const form = sec.form ?? "filing";
  const filed = sec.filed ? ` filed ${sec.filed}` : "";
  if (!sec.ratio_label) {
    return `The SEC ${form}${filed} was found, but no single ratio could be read from it (${
      sec.abstain_reason ?? "no ratio read"
    }).`;
  }
  const date = sec.effective_date
    ? sec.date_kind === "trading"
      ? `, first trading on the new basis ${sec.effective_date}`
      : `, legally effective ${sec.effective_date} (the first trading day can differ)`
    : ", with no date stated";
  return `The SEC ${form}${filed} says ${sec.ratio_label}${date}.`;
}

/** What Massive.com says, in a sentence, with its own label when it states one (W6-1): "Massive.com lists 1061-for-1000
 *  forward split, first trading 2025-10-30; it labels it a forward split." */
export function polygonSays(s: Split): string {
  const p = s.polygon;
  if (!p) return "Massive.com lists no split here.";
  const what = p.ratio_label ?? `${p.split_from} → ${p.split_to}`;
  const label = p.adjustment_type ? `; it labels it a ${p.adjustment_type.replace(/_/g, " ")}` : "";
  return `Massive.com lists ${what}${p.execution_date ? `, first trading ${p.execution_date}` : ""}${label}.`;
}

/** What Confirm does with one source's values. */
export function confirmLine(s: Split, o: Option, many: boolean): string {
  const whose = many ? `${o.source === "sec" ? "the SEC filing's" : "Massive.com's"} values (${o.label})` : o.label;
  const moved =
    o.effective_date !== s.effective_date ? ` The split moves to ${o.effective_date}.` : "";
  return `Confirm ${whose}: prices before ${md(o.effective_date)} are put on the new scale, and the split-night hold on this company's price triggers lifts.${moved}`;
}

/** What Reject does. */
export function rejectLine(): string {
  return "Reject: no split; prices stay as stored. The split stays listed, grayed, and Restore brings it back.";
}

/** A split the SYSTEM set aside as a distribution (D2: a forward "split" under x1.5 that only Massive.com lists, with
 *  no SEC filing about it) — rejected, but not by the steward. */
export function autoSetAside(s: Split): boolean {
  return s.status === "rejected" && s.author !== "operator";
}

/** The quiet set-aside row's words: "rejected · rejected by you, 10-20", or the API's own label alone for an
 *  automatic set-aside ("set aside automatically: a distribution"). */
export function setAsideLine(s: Split, statusWord: string): string {
  return autoSetAside(s) ? s.decided_by : `${statusWord} · ${s.decided_by}`;
}

// --- D3: "Confirm with other values" — the steward's own ratio, first session and cited document ---

type TapeStep = AdminSplitTapeStepOut;

/** The API's note limit (backend CITED_NOTE_MAX; the 2,000-character `prose` limit is not this field's). */
export const NOTE_MAX = 500;

// "x4.53", "x80", "x0.22" — three significant digits, the way the server words a step in its own sentences
const x = (v: number) => `x${Number(v.toPrecision(3))}`;
const px = (v: number | null | undefined) => (v == null ? "—" : String(Number(v.toPrecision(4))));

/** What "Confirm with other values" does (the line beside its button). */
export function confirmValuesLine(): string {
  return (
    "Confirm with other values: you type the ratio and first session from a document you cite, and the stored " +
    "tape is checked against them first. Then, as with Confirm, prices before that session are put on the new " +
    "scale and the split-night hold on this company's price triggers lifts. Revoke undoes it."
  );
}

// the server's rule for a ratio (backend RATIO_PATTERN, RATIO_MAX): plain decimal text, above 0, at most 100,000
const RATIO = /^\d{1,6}(\.\d{1,10})?$/;
const ratioNum = (t: string): number | null => (RATIO.test(t) && Number(t) > 0 && Number(t) <= 100000 ? Number(t) : null);

/** A real calendar date typed as 2026-09-24 (what a date input holds once it is complete). */
export function isDate(t: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(t)) return false;
  const d = new Date(`${t}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === t;
}

/** Why the two share counts are not a ratio yet, in a sentence; null when they are one. */
export function ratioProblem(ratioFrom: string, ratioTo: string): string | null {
  if (!ratioFrom || !ratioTo) return "Enter the old and the new share counts.";
  const a = ratioNum(ratioFrom);
  const b = ratioNum(ratioTo);
  if (a === null || b === null) {
    return "A share count is a plain number above 0 and at most 100,000, such as 10 or 6.25.";
  }
  if (a === b) return "The old and the new counts are equal: that is not a split.";
  return null;
}

/** Why Confirm these values is off, in a sentence (the first thing still missing); null when the form is complete. */
export function valuesProblem(v: {
  ratioFrom: string;
  ratioTo: string;
  firstSession: string;
  citedUrl: string;
}): string | null {
  const ratio = ratioProblem(v.ratioFrom, v.ratioTo);
  if (ratio) return ratio;
  if (!isDate(v.firstSession)) return "Enter the first session at the new price (a date).";
  if (!v.citedUrl) return "Cite the document that states these values (an https:// address).";
  if (!v.citedUrl.toLowerCase().startsWith("https://") || safeHttpUrl(v.citedUrl) === null) {
    return "The cited document must be an https:// address with a host.";
  }
  return null;
}

/** The stored tape at the first session: "The stored tape stepped x4.53 on 09-24: close 1.2 on 09-23 → 5.43, volume
 *  x0.22." — or that it has no bar to read there. */
export function tapeStepSentence(t: TapeStep): string {
  if (t.step == null) {
    return `The stored tape has no bar to read on ${t.first_session} (it needs that day's bar and an earlier one).`;
  }
  const from = t.prior_date ? ` on ${md(t.prior_date)}` : "";
  const vol = t.volume_ratio == null ? "" : `, volume ${x(t.volume_ratio)}`;
  return `The stored tape stepped ${x(t.step)} on ${md(t.first_session)}: close ${px(t.prior_close)}${from} → ${px(t.close)}${vol}.`;
}

/** The entered ratio's price factor beside the band the tape accepts, with the verdict; null before a ratio is
 *  entered. "Your values: every 10 shares became 3, a price factor of x3.33; the tape accepts x2.02 to x5.5. The
 *  tape supports your values." */
export function tapeFactorSentence(t: TapeStep, ratioFrom: string, ratioTo: string): string | null {
  if (t.price_factor == null) return null;
  const mine = `Your values: ${sharesPhrase(ratioFrom, ratioTo)}, a price factor of ${x(t.price_factor)}`;
  const band = t.step_low != null && t.step_high != null ? `; the tape accepts ${x(t.step_low)} to ${x(t.step_high)}` : "";
  const verdict =
    t.within_tolerance === true
      ? " The tape supports your values."
      : t.within_tolerance === false
        ? t.step == null
          ? " The tape cannot check your values."
          : " The tape does not support your values."
        : "";
  return `${mine}${band}.${verdict}`;
}

/** What an override form does: confirm a split already on the list (D3), or add one no source lists (F2-2). */
export type FormAct = "confirm" | "add";

/** The override box beside a tape that does not support the values (or has no bar to check them against). The add form
 *  leaves the verdict to the tape line above it, which already says which of the two it is. */
export function overrideLabel(t: TapeStep, act: FormAct = "confirm"): string {
  if (act === "add") return "Add anyway: the split is then marked “past the tape”.";
  const why = t.step == null ? "The tape has no bar to check these values against" : "The tape does not support these values";
  return `${why} — confirm anyway. The split is then marked “confirmed past the tape”.`;
}

/** Why the form's button is off while the override box stands unticked. */
export function overrideHint(act: FormAct = "confirm"): string {
  return `Tick the box above to ${act} past the tape.`;
}

// --- W2-8: what a confirm would touch, from the API's first_bar and shares_rescaled (needs-review / announced) ---

/** "by 4" for a 1-for-4 reverse split, "by 2" for a 2-for-1, "by 10/3" for a 3-for-10: the factor a share count is
 *  divided (reverse) or multiplied (forward) by. */
function countFactor(s: Split): string {
  const reverse = Number(s.ratio_from) > Number(s.ratio_to);
  const [a, b] = reverse ? [s.ratio_from, s.ratio_to] : [s.ratio_to, s.ratio_from];
  return Number(b) === 1 ? n(a) : `${n(a)}/${n(b)}`;
}

/** One line on an open split, or null when there is nothing to say: a first session on or before the first stored bar
 *  adjusts nothing on the tape, and a share count dated before the first session is rescaled by a confirm. "TREO's
 *  first session is its first bar: nothing on the tape to adjust; confirming divides 13,524,682 shares dated
 *  2026-07-31 by 4 — check that count's basis." */
export function hintLine(s: Split): string | null {
  const parts: string[] = [];
  const whose = s.ticker ? `${s.ticker}'s` : "Its";
  if (s.first_bar && s.effective_date <= s.first_bar) {
    parts.push(
      s.effective_date === s.first_bar
        ? `${whose} first session is its first bar: nothing on the tape to adjust`
        : `${whose} first session is before its first bar (${s.first_bar}): nothing on the tape to adjust`,
    );
  }
  const sh = s.shares_rescaled;
  if (sh) {
    const verb = s.status === "announced" ? "once in force it" : "confirming";
    const how = Number(s.ratio_from) > Number(s.ratio_to) ? "divides" : "multiplies";
    const count = Math.round(sh.count).toLocaleString("en-US");
    parts.push(`${verb} ${how} ${count} shares dated ${sh.date} by ${countFactor(s)} — check that count's basis`);
  }
  if (!parts.length) return null;
  const line = parts.join("; ");
  return `${line[0].toUpperCase()}${line.slice(1)}.`;
}

// --- F1-5: SEC filings about a split the list could not place (the "Split filings not placed" section) ---

type Unplaced = AdminSplitUnplacedOut;

/** The API's limit on a dismissal's reason (backend `AdminSplitJumpReasonIn.reason`). */
export const REASON_MAX = 500;

/** A split's `status` in words. */
export const STATUS_WORD: Record<string, string> = {
  needs_review: "needs review",
  confirmed: "confirmed",
  announced: "announced",
  rejected: "rejected",
  revoked: "revoked",
};

const SOURCE_WORD = { nightly: "nightly", check: "long-window check" } as const;

const upper = (s: string) => `${s.charAt(0).toUpperCase()}${s.slice(1)}`;

/** Does the list already answer this filing: a listed split cites it, or has the ratio it states? Such a filing
 *  needs the steward least, so the section lists it after the ones nothing on the list answers. */
export function unplacedCovered(u: Unplaced): boolean {
  const near = u.listed_near;
  return near != null && (near.cites_filing || near.same_ratio === true);
}

/** The filings no listed split covers first, each group in the order the server sent (newest filing first). Drops
 *  none (WB#2). */
export function orderUnplaced(rows: Unplaced[]): Unplaced[] {
  return [...rows.filter((u) => !unplacedCovered(u)), ...rows.filter((u) => unplacedCovered(u))];
}

/** Why the filing was not placed, said once: the detector's reason, which names the ratio the filing states when it
 *  states one ("1-for-20 reverse split stated, but no first session stated"). The ratio is added only when the
 *  reason does not already name it. */
export function unplacedWhy(u: Unplaced): string {
  const reason = u.reason.trim();
  const label = u.ratio_label;
  if (!label) return reason ? upper(reason) : "No reason recorded";
  if (!reason) return `The filing states ${label}`;
  return reason.includes(label) ? upper(reason) : `${upper(reason)} — the filing states ${label}`;
}

/** Which run read the filing, and the day it read: "nightly 11-25" / "long-window check 11-27". */
export function unplacedSource(u: Pick<Unplaced, "source" | "seen_on">): string {
  return `${SOURCE_WORD[u.source]} ${md(u.seen_on)}`;
}

/** The days from the filing to a listed split's first session, in words (signed: after is positive). */
function distance(days: number): string {
  if (days === 0) return "the same day as this filing";
  const n = Math.abs(days);
  return `${n} day${n === 1 ? "" : "s"} ${days > 0 ? "after" : "before"} this filing`;
}

/** The split on the list the filing most likely talks about (the API's `listed_near`), one line; null when none is
 *  near. The ratio is left out where the reason already names it. "The list holds this ratio: needs review, first
 *  session 11-30, 13 days after this filing." */
export function listedNearLine(u: Unplaced): string | null {
  const near = u.listed_near;
  if (!near) return null;
  const what = [
    near.label === u.ratio_label ? null : near.label,
    STATUS_WORD[near.status] ?? near.status,
    `first session ${md(near.effective_date)}`,
  ]
    .filter(Boolean)
    .join(", ");
  if (near.cites_filing) return `A listed split cites this filing: ${what}.`;
  if (near.same_ratio === true) return `The list holds this ratio: ${what}, ${distance(near.days_from)}.`;
  return `Nearest listed split: ${what}, ${distance(near.days_from)}.`;
}

/** The runs the section was read from: "Read from nightly 11-25: 65 filings; long-window check 11-27, 800 days: 363
 *  filings." Null when the API named none. */
export function unplacedRunsLine(runs: AdminSplitUnplacedRunOut[]): string | null {
  if (runs.length === 0) return null;
  const parts = runs.map((r) => {
    const days = r.sec_days != null ? `, ${r.sec_days} days` : "";
    return `${SOURCE_WORD[r.source]} ${md(r.asof)}${days}: ${r.rows} ${r.rows === 1 ? "filing" : "filings"}`;
  });
  return `Read from ${parts.join("; ")}.`;
}

/** What the section is and what Dismiss does, once for the whole list. */
export function unplacedHelp(): string {
  return (
    "These are SEC filings about a split that the nightly or the long-window check read but could not place on the " +
    "list (no ratio, no first session, or an extractor abstain). Covered means a listed split cites the filing or " +
    "has the ratio it states: the rest come first. Dismiss means this is not a split to list: the filing leaves " +
    "this list and stays under “dismissed”, with an Undo. A dismissal changes no split, hold or call."
  );
}

/** A dismissed filing's line: "dismissed by you, 11-27 — “a share-capital notice, not a split”". */
export function unplacedDismissedLine(u: Unplaced): string {
  const day = u.dismissed_at ? `, ${md(u.dismissed_at.slice(0, 10))}` : "";
  const why = u.dismissed_reason ? ` — “${u.dismissed_reason}”` : "";
  return `dismissed by you${day}${why}`;
}

// --- F2-2: "Add this split...": a split no source lists, from an unplaced filing or a split-shaped jump ---

// what an add does, said once in the form's intro: the tape check, the listing, the rescale, the undo
const ADD_EFFECT =
  "The stored tape is checked against the values you enter first. Then the split joins the list (confirmed, or " +
  "announced while its first session is ahead): prices before that session are put on the new scale and the " +
  "split-night hold on this company's price triggers lifts. Revoke undoes it (Reject, while it is announced).";

/** The form's lead from a filing: the ratio it states is filled in (nothing is cited for the steward), what is left, and
 *  what an add does. A filing that states no ratio fills in nothing. */
export function addFilingIntro(u: Pick<Unplaced, "ratio_from" | "ratio_to">): string {
  const ratio = u.ratio_from != null && u.ratio_to != null;
  const lead = ratio
    ? "The ratio it states is filled in from the filing: check it against the document you cite, and enter the first session."
    : "The filing states no single ratio: enter the values the document you cite states.";
  return `No source lists this split. ${lead} ${ADD_EFFECT}`;
}

/** The filing an add starts from, as the form names it beside the cited-document box (a reference, never the citation):
 *  "6-K filed 2026-11-17". */
export function sourceFilingLabel(u: Pick<Unplaced, "form" | "filed">): string {
  return `${u.form || "filing"} filed ${u.filed}`;
}

/** The form's lead from a jump: its date is the first session, no ratio is guessed, and what an add does. */
export function addJumpIntro(): string {
  return (
    "No source lists this split. The jump's date is filled in as the first session. No ratio is filled in: enter " +
    `the one the document you cite states. ${ADD_EFFECT}`
  );
}

/** The whole-number ratio a jump's price step is nearest to, beside the empty ratio boxes (never put in them):
 *  "Nearest whole-number ratio to that step: 1-for-8 reverse split (x8)." Null for a step within x1.5 of flat, where
 *  no whole ratio is near. */
export function jumpScaleLine(step: number): string | null {
  if (!(step > 0)) return null;
  const up = step >= 1;
  const k = Math.round(up ? step : 1 / step);
  if (k < 2) return null;
  const what = up ? `1-for-${k} reverse split (x${k})` : `${k}-for-1 forward split (${x(1 / k)})`;
  return `Nearest whole-number ratio to that step: ${what}.`;
}

/** After a confirmed add (its first session is past): the nightly rescales a stored tape only on a first night, so the
 *  operator runs the repair. Split in three so the command can be set apart; null for an announced add, which the
 *  nightly handles when it comes into force. */
export function addedLine(
  s: Pick<Split, "ticker" | "security_id" | "effective_date" | "status">,
): { before: string; command: string; after: string } | null {
  if (s.status !== "confirmed") return null;
  const who = s.ticker ?? s.security_id;
  return {
    before:
      `${who}'s split is added and confirmed (first session ${md(s.effective_date)}). The nightly rescales a ` +
      "stored tape only on a first night: the operator runs ",
    command: `python -m pipeline.repair_split_basis --ticker ${who}`,
    after: " (dry run, then --apply).",
  };
}

// --- F8-3: an open split's other listed splits of its exact ratio nearby (the API's same_ratio_listed) ---

type SameRatio = NonNullable<Split["same_ratio_listed"]>[number];

// a status as the word before the ratio: "a confirmed 1-for-10", "a needs-review 1-for-10"
const SAME_STATUS: Record<string, string> = { needs_review: "needs-review" };

/** "a confirmed 1-for-10 reverse split" / "an announced one". */
function sameWhat(n: SameRatio, what: string): string {
  const adj = SAME_STATUS[n.status] ?? STATUS_WORD[n.status] ?? n.status;
  return `${/^[aeiou]/i.test(adj) ? "an" : "a"} ${adj} ${what}`;
}

/** The days from this split's first session to the other's, in words (signed: later is positive). */
function away(days: number): string {
  if (days === 0) return "the same day";
  const k = Math.abs(days);
  return `${k} day${k === 1 ? "" : "s"} ${days > 0 ? "later" : "earlier"}`;
}

/** One line on an open split naming the other splits on the list of its exact ratio near it, in the server's order
 *  (nearest first, at most two); null when the API names none. Display only: nothing merges two splits, and which one
 *  went ahead is the steward's. "The list also holds a confirmed 1-for-10 reverse split on 07-24, 120 days later:
 *  check this one went ahead." */
export function sameRatioLine(s: Split): string | null {
  const near = s.same_ratio_listed ?? [];
  if (!near.length) return null;
  const parts = near.map((n, i) => {
    const cites = n.cites_filing ? ", citing the same SEC filing" : "";
    return `${sameWhat(n, i === 0 ? n.label : "one")} on ${md(n.effective_date)}, ${away(n.days_from)}${cites}`;
  });
  return `The list also holds ${parts.join(", and ")}: check this one went ahead.`;
}

// --- F10: a filing that may call a listed split off (the API's called_off, any status) ---

type CalledOff = NonNullable<Split["called_off"]>[number];

/** The longest a quoted passage runs, in characters, before it is cut. */
export const PASSAGE_MAX = 280;

/** A passage for one line: runs of whitespace as one space; whole when it fits, else cut where a clause ends (a comma,
 *  semicolon, colon or closing parenthesis past the halfway), else at the last whole word, else at the limit, with the
 *  mark or dash the cut leaves dropped and an ellipsis. The start is kept: the reader hands over the one sentence that
 *  holds the cue, and the cut cannot aim at the cue (its patterns are the backend's). */
export function cutPassage(text: string, max = PASSAGE_MAX): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const clause = Math.max(...[",", ";", ":", ")"].map((c) => t.lastIndexOf(c, max - 1)));
  const space = t.lastIndexOf(" ", max);
  const end = clause >= max / 2 ? (t[clause] === ")" ? clause + 1 : clause) : space > 0 ? space : max;
  return `${t.slice(0, end).replace(/[\s,;:\u2013\u2014-]+$/, "")}\u2026`;
}

/** "1 day before the first session" / "the day of the first session" / "30 days after the first session". */
function sessionDistance(days: number): string {
  if (days === 0) return "the day of the first session";
  const k = Math.abs(days);
  return `${k} day${k === 1 ? "" : "s"} ${days < 0 ? "before" : "after"} the first session`;
}

/** Which ratio the filing states, against this split's: the server's `same_ratio` (null: it states none). */
function statesRatio(c: CalledOff): string {
  if (c.same_ratio === true) return "states this split's ratio";
  if (c.same_ratio === false) return `states a different ratio${c.ratio_label ? `, ${c.ratio_label}` : ""}`;
  return "states no ratio";
}

// what the filing says, by its kind, for a call-off the reader kept no passage of
const CALLED_OFF_SAYS: Record<string, string> = {
  decision: "the filing says the split will not go ahead",
  failed_vote: "the filing says the vote on the split failed",
};

/** The warning line on a listed split a filing may call off, in the parts the panel sets (the filing is the link):
 *  "Called off? 8-K filed 2026-11-19 (1 day before the first session; states this split's ratio): “On November 19,
 *  2026, Air Industries Group, announced that it would not be effecting the reverse stock split …”". Each fact once:
 *  the ratio is named only when it is not this split's (the entry's own label says that one), the sentence is the
 *  filing's own (cut sensibly), and a call-off with no passage says what its kind means instead. */
export function calledOffWords(c: CalledOff): { head: string; filing: string; when: string; says: string | null } {
  const passage = c.passage ? cutPassage(c.passage) : "";
  const kind = CALLED_OFF_SAYS[c.kind];
  return {
    head: "Called off?",
    filing: `${c.form} filed ${c.filed}`,
    when: `(${sessionDistance(c.days_from)}; ${statesRatio(c)})`,
    says: passage ? `\u201c${passage}\u201d` : kind ? `${kind}.` : null,
  };
}

const COUNT_NAMES = 6;

/** The count at the top of the panel, over the splits still standing (needs review, announced, confirmed): "1 listed
 *  split has a filing that may call it off: AIRI." It counts splits, not filings, and names the first six: the
 *  confirmed and announced lists are folded. Null when no split has one. A rejected or revoked split is not counted
 *  (its line stays on its row), so acting on the warning clears it. */
export function calledOffCountLine(splits: Split[]): string | null {
  const hit = splits.filter((s) => (s.called_off?.length ?? 0) > 0);
  if (!hit.length) return null;
  const one = hit.length === 1;
  const names = [...new Set(hit.map((s) => s.ticker ?? s.name ?? s.security_id))];
  const more = names.length > COUNT_NAMES ? ` and ${names.length - COUNT_NAMES} more` : "";
  return `${hit.length} listed split${one ? " has" : "s have"} a filing that may call ${one ? "it" : "them"} off: ${names.slice(0, COUNT_NAMES).join(", ")}${more}.`;
}
