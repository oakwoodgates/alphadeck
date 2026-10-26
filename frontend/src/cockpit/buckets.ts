import type {
  BasketMember,
  CallCardResponse,
  MemberCallOut,
  ScoredMemberOut,
  Segment,
} from "../api/hooks";
import { BENCH, CONTEXT, SILENT_LINKS, isSilentLink, withSilentLinks } from "../workbench/useChainDraft";

/** The per-name buckets (strongest → weakest) — the Board's column idiom applied INSIDE a basket.
 *  Five are the spec'd member states; WARMING and QUIET are the coverage groups the wire forces:
 *  a conviction-only name is in NEITHER `armed_members` NOR `watch_members` (watch is
 *  confirmation-only), yet its trigger is live on the rail — filing it under "no signals" would be
 *  dishonest. And every basket row must land somewhere (pruning hides, it never vanishes), so the
 *  remainder is QUIET, not absent. Display-only: the buckets re-read the call, never re-derive it.
 *
 *  CONTEXT and BENCH are the two SILENT value-chain links (fixed, last): a name the operator placed in
 *  either is on the card and visible but NEVER counts toward the call, so it files under its link's own
 *  bucket — after Quiet, the quietest place on the page — instead of a call-derived one. */
export type BucketKey =
  | "managing"
  | "armed"
  | "lapsing"
  | "theme_armed"
  | "warming"
  | "watch"
  | "quiet"
  | "context"
  | "bench";

export interface BucketDef {
  key: BucketKey;
  label: string;
  hint: string;
  /** CSS class carrying the bucket accent (`--gc`): the row dot, the header swatch, the tint. */
  cls: string;
}

/** Display order, strongest → weakest. `managing` is render-if-present: the assembler emits it on
 *  the HELD member when the open position carries a security_id (a take logged on a name —
 *  CALL_LOGIC §4); an unattributed position (a thesis-level take, the seed-era columns) emits no
 *  member verdict and the group simply stays empty. */
export const BUCKETS: BucketDef[] = [
  { key: "managing", label: "Managing", hint: "in position", cls: "bkt-managing" },
  { key: "armed", label: "Armed", hint: "act now", cls: "bkt-armed" },
  // `lapsing` = the HOLD clock (exit_by) inside the freshness dial — NOT the entry window. It was
  // labeled "entry window closing" until Q14 (2026-10-18), which was wrong for most arm-window
  // expiries (69% never showed it, MEASURED). The key + CSS class keep the wire name; only the
  // words changed. The entry window's own flag is `entry_window_closing`, shown on the Entry-by cell.
  {
    key: "lapsing",
    label: "Hold clock closing",
    hint: "signal-validity horizon near",
    cls: "bkt-lapsing",
  },
  { key: "theme_armed", label: "Theme-armed", hint: "theme fallback · starter cap", cls: "bkt-theme" },
  { key: "warming", label: "Warming", hint: "conviction in · awaiting confirmation", cls: "bkt-warming" },
  { key: "watch", label: "Watch", hint: "moving · no conviction yet", cls: "bkt-watch" },
  { key: "quiet", label: "Quiet", hint: "no live signals", cls: "bkt-quiet" },
  // the two silent links — AFTER Quiet, the same muted accent. A held (`managing`) name never lands here.
  { key: "context", label: CONTEXT, hint: "never counts toward the call", cls: "bkt-context" },
  { key: "bench", label: BENCH, hint: "never counts toward the call", cls: "bkt-bench" },
];

/** The buckets whose members show an ENTRY WINDOW (`arm_until`) — the armed family only.
 *  A WATCH member carries arm_until on the wire too, and deliberately stays dark: watch is
 *  confirmation without conviction, so its clock is a confirmation DECAY, not an entry the system
 *  is endorsing — putting a date under "Entry-by" there would be dishonest loudness (#7). The
 *  NamePanel draws the same line in words ("Entry window" with a verdict, "Confirmation clock ·
 *  decays in Nd" without). Shared by the cell and its sort key so the two can never drift.  */
export const ENTRY_WINDOW_BUCKETS: ReadonlySet<BucketKey> = new Set<BucketKey>([
  "armed",
  "lapsing",
  "theme_armed",
]);

const BUCKET_BY_KEY = new Map(BUCKETS.map((b) => [b.key, b]));
export function bucketDef(key: BucketKey): BucketDef {
  return BUCKET_BY_KEY.get(key) as BucketDef;
}

export interface BucketRow {
  member: BasketMember;
  /** Index in the authored basket — the stable row identity (duplicate tickers stay distinct). */
  ordinal: number;
  /** The member's own call (armed or watch tier) when the wire carries one. */
  call: MemberCallOut | null;
  scored: ScoredMemberOut | null;
  bucket: BucketKey;
}

export interface BucketGroup {
  def: BucketDef;
  rows: BucketRow[];
}

/** Partition the basket into the buckets. Joins ride `security_id` (the `capBySid` precedent);
 *  WARMING is the one ticker join — `TriggerRefOut` carries no security_id on the wire, so
 *  duplicate-ticker rows both light up (visible over-inclusion, never a silent drop).
 *
 *  Bucket precedence (mutually exclusive, evaluated in this order):
 *    verdict === "managing"  →  managing        (render-if-present; a held position never leaves the
 *                               top, even for a name sitting in Context / Bench)
 *    in armed_members        →  lapsing if `lapsing` (the hold clock closing — the clock is the
 *                               urgent fact, so it wins over the theme flag, which stays visible
 *                               as a chip), else theme_armed
 *                               if `theme_armed`, else armed
 *    in watch_members        →  watch           (checked BEFORE the trigger join — a watch member's
 *                               breakout is itself in triggers_fired)
 *    ticker in triggers_fired →  warming        (conviction-only: live trigger, no member call)
 *    segment is Context/Bench →  context / bench (the operator's silent links — never a call bucket; the
 *                               row carries no member call: it does not count toward the call)
 *    otherwise               →  quiet
 *
 *  Why the silent link comes LAST among the call-derived tests: the roster is TODAY's, but the card can be
 *  a history recompute at a PAST as-of. A name that voted last week (armed, watch, or a fired trigger on
 *  that card) and was moved into Context today must still read as the voter the card's headline names — it
 *  files under its call bucket WITH its call. Only a name the card has no hit and no fired trigger for
 *  falls to its silent bucket. On a current card this changes nothing: the backend never puts a silent
 *  name on one. (A ticker shared with a voting name is the existing over-inclusive warming join.)
 *
 *  Within a bucket the wire's own ranking is preserved for the member lists (the call machinery
 *  already ranked armed/watch — the FE never re-ranks the brain's output); warming/quiet keep the
 *  authored basket order. Empty buckets are omitted (a header over nothing is noise). */
export function groupBasket(
  basket: BasketMember[],
  card: CallCardResponse | undefined,
  scored: ScoredMemberOut[] | undefined,
): BucketGroup[] {
  const armedBySid = new Map<string, { call: MemberCallOut; rank: number }>();
  (card?.armed_members ?? []).forEach((m, i) => armedBySid.set(m.security_id, { call: m, rank: i }));
  const watchBySid = new Map<string, { call: MemberCallOut; rank: number }>();
  (card?.watch_members ?? []).forEach((m, i) => watchBySid.set(m.security_id, { call: m, rank: i }));
  const scoredBySid = new Map((scored ?? []).map((s) => [s.security_id, s]));
  const firedTickers = new Set(
    (card?.triggers_fired ?? []).map((t) => t.ticker).filter((t): t is string => Boolean(t)),
  );

  interface Ranked extends BucketRow {
    rank: number;
  }
  const rows: Ranked[] = basket.map((member, ordinal) => {
    const sid = member.security_id;
    const armed = sid ? armedBySid.get(sid) : undefined;
    const watch = sid ? watchBySid.get(sid) : undefined;
    const hit = armed ?? watch;
    let bucket: BucketKey;
    if (hit?.call.verdict === "managing") bucket = "managing";
    else if (armed)
      bucket = armed.call.lapsing ? "lapsing" : armed.call.theme_armed ? "theme_armed" : "armed";
    else if (watch) bucket = "watch";
    else if (member.ticker && firedTickers.has(member.ticker)) bucket = "warming";
    // only a name the card has NOTHING on files under its silent link (see the precedence note above)
    else if (isSilentLink(member.segment)) bucket = member.segment === CONTEXT ? "context" : "bench";
    else bucket = "quiet";
    return {
      member,
      ordinal,
      // a silent row has no hit by construction (a hit would have filed it above), so its call is null; a
      // past voter keeps its call
      call: hit?.call ?? null,
      scored: (sid ? scoredBySid.get(sid) : undefined) ?? null,
      bucket,
      rank: hit?.rank ?? ordinal,
    };
  });

  return BUCKETS.map((def) => ({
    def,
    rows: rows
      .filter((r) => r.bucket === def.key)
      .sort((a, b) => a.rank - b.rank || a.ordinal - b.ordinal)
      .map(({ rank: _rank, ...row }) => row),
  })).filter((g) => g.rows.length > 0);
}

/** Collapse the multi-row artifact of a multi-SEGMENT name for the NON-segment lenses. A name placed
 *  in N value-chain links has N `BasketMember` rows (same `security_id`, differing only in `segment`),
 *  which the call-state and business-type lenses would otherwise render as confusing IDENTICAL
 *  duplicates. This keeps ONE row per `security_id` — the FIRST in render order (strongest call, then
 *  authored order), dropping later same-sid rows. Lossless: same `security_id` ⇒ same call bucket,
 *  scored join, and business-type, so the collapse is purely visual. Rows with NO `security_id` are
 *  NEVER deduped (each kept — the recall-safe direction: two ticker-only rows may be different
 *  companies). The value-chain (segment) lens deliberately does NOT call this — there, each link-row
 *  is the point ("show them all"). */
export function dedupeBySecurityId(groups: BucketGroup[]): BucketGroup[] {
  const seen = new Set<string>();
  return groups
    .map((g) => ({
      def: g.def,
      rows: g.rows.filter((r) => {
        const sid = r.member.security_id;
        if (!sid) return true; // no master bind → never merge (over-include, never a silent drop)
        if (seen.has(sid)) return false;
        seen.add(sid);
        return true;
      }),
    }))
    .filter((g) => g.rows.length > 0);
}

/** The business-type LENS (Business-Type M1): the SAME rows re-partitioned by the scored
 *  super-sector ("are the utilities moving?"). Display-only, built FROM groupBasket's output so
 *  every row keeps its call-state def — the state dot and exit-by survive the lens (the call never
 *  disappears behind a view). Rows inside a group keep the call-strength order (the flatten walks
 *  the state buckets strongest → weakest); groups render in the fixed super order below with the
 *  visible tails LAST — `other` (mapped-to-other) then `unclassified` (no sector on file): pruning
 *  hides, it never vanishes (#9 — an un-enriched name still has a row). Empty groups are omitted. */
export interface TypeGroup {
  /** The supersector value, or "unclassified" (scored missing / un-enriched). */
  key: string;
  rows: { row: BucketRow; def: BucketDef }[];
}

/** Display order for the lens: the 8 supers, then the visible tails. */
export const SUPERSECTOR_ORDER = [
  "energy_utilities",
  "materials",
  "technology",
  "healthcare",
  "financials",
  "industrials",
  "consumer_comms",
  "real_estate",
  "other",
  "unclassified",
] as const;

export function groupByBusinessType(groups: BucketGroup[]): TypeGroup[] {
  const by = new Map<string, { row: BucketRow; def: BucketDef }[]>();
  for (const g of groups) {
    for (const row of g.rows) {
      const key = row.scored?.business_supersector ?? "unclassified";
      const list = by.get(key) ?? [];
      list.push({ row, def: g.def });
      by.set(key, list);
    }
  }
  const known = SUPERSECTOR_ORDER.filter((k) => by.has(k)).map((key) => ({
    key,
    rows: by.get(key) as { row: BucketRow; def: BucketDef }[],
  }));
  // a super this order list doesn't know yet (a future enum value) still renders — after the
  // knowns, before nothing: over-include, never a silent drop
  const unknown = [...by.keys()]
    .filter((k) => !(SUPERSECTOR_ORDER as readonly string[]).includes(k))
    .sort()
    .map((key) => ({ key, rows: by.get(key) as { row: BucketRow; def: BucketDef }[] }));
  return [...known, ...unknown];
}

/** The value-chain (segment) LENS: the SAME rows re-partitioned by each member's `segment` — the
 *  value-chain link the chain drafter placed it in. UNLIKE the other lenses this shows the FULL
 *  groups (never deduped): a name placed in N links has N rows and appears under EACH link header
 *  ("show them all"), which is the whole point. Built FROM groupBasket's output so every row keeps
 *  its call-state `def` (the state dot / exit-by survive the lens). Group ORDER is the thesis's
 *  authored `segments` order; a label present on a row but not in that list still renders (after the
 *  knowns — over-include, never a silent drop); empty links are omitted (a header over nothing is
 *  noise); the keep-visible "Unsegmented" tail collects the null/empty-segment rows (the
 *  business-type "Unclassified" mirror — #9, a name is never dropped). The two SILENT links (Context,
 *  Bench) are always the LAST two groups — after the unknown labels and the Unsegmented tail — whatever
 *  order a payload's segments came in; empty ones are omitted like any other link. */
export const UNSEGMENTED_KEY = "__unsegmented__";

export interface SegmentGroup {
  /** The value-chain link label, or ``UNSEGMENTED_KEY`` for null/empty-segment rows. */
  key: string;
  /** The header text: the link label, or "Unsegmented". */
  label: string;
  /** The operator's per-segment tag (``Segment.descriptor``), surfaced as the header hint. */
  descriptor: string | null;
  rows: { row: BucketRow; def: BucketDef }[];
}

export function groupBySegment(groups: BucketGroup[], segments: Segment[]): SegmentGroup[] {
  const by = new Map<string, { row: BucketRow; def: BucketDef }[]>();
  for (const g of groups) {
    for (const row of g.rows) {
      // `|| UNSEGMENTED_KEY` folds a null AND an empty-string segment into the same honest tail
      const key = row.member.segment || UNSEGMENTED_KEY;
      const list = by.get(key) ?? [];
      list.push({ row, def: g.def });
      by.set(key, list);
    }
  }
  // Context / Bench are pinned last with their fixed descriptors (`withSilentLinks`), so they are taken out
  // of the authored order here and appended after everything else below.
  const chain = withSilentLinks(segments);
  const descriptorByLabel = new Map(chain.map((s) => [s.label, s.descriptor ?? null]));
  const ordered = chain.map((s) => s.label).filter((l) => !isSilentLink(l));
  const known = new Set(chain.map((s) => s.label));
  const extras = [...by.keys()].filter((k) => k !== UNSEGMENTED_KEY && !known.has(k)).sort();

  const out: SegmentGroup[] = [];
  const rendered = new Set<string>();
  for (const label of [...ordered, ...extras]) {
    const rows = by.get(label);
    if (rendered.has(label) || !rows || rows.length === 0) continue; // dedupe labels; omit empty
    rendered.add(label);
    out.push({ key: label, label, descriptor: descriptorByLabel.get(label) ?? null, rows });
  }
  const unseg = by.get(UNSEGMENTED_KEY);
  if (unseg && unseg.length > 0) {
    out.push({ key: UNSEGMENTED_KEY, label: "Unsegmented", descriptor: null, rows: unseg });
  }
  for (const label of SILENT_LINKS) {
    const rows = by.get(label);
    if (rows && rows.length > 0) {
      out.push({ key: label, label, descriptor: descriptorByLabel.get(label) ?? null, rows });
    }
  }
  return out;
}

/** The URL key (?name=) for a cockpit row: the ticker when it's unique in the basket (readable —
 *  the overwhelmingly common case), the security_id when the ticker duplicates (precise across the
 *  duplicates). A duplicate-ticker row with NO security_id degrades to the ticker, which
 *  resolveNameKey lands on the first match by basket order — documented, acceptable. */
export function nameKeyFor(row: BucketRow, basket: BasketMember[]): string {
  const t = row.member.ticker.toLowerCase();
  const dup = basket.filter((m) => m.ticker.toLowerCase() === t).length > 1;
  return dup && row.member.security_id ? row.member.security_id : row.member.ticker;
}

/** Resolve a ?name= key to its display row: security_id exact match first (precise), else
 *  case-insensitive ticker, ties broken by the authored basket order (lowest ordinal). No match →
 *  null: the panel simply doesn't render — the existing vanished-row behavior — so a stale or
 *  mistyped URL key is harmless. */
export function resolveNameKey(
  groups: BucketGroup[],
  key: string | null | undefined,
): { row: BucketRow; def: BucketDef } | null {
  if (!key) return null;
  const all = groups.flatMap((g) => g.rows.map((row) => ({ row, def: g.def })));
  const kt = key.toLowerCase();
  const hits = all.some((x) => x.row.member.security_id === key)
    ? all.filter((x) => x.row.member.security_id === key)
    : all.filter((x) => x.row.member.ticker.toLowerCase() === kt);
  return hits.reduce<{ row: BucketRow; def: BucketDef } | null>(
    (best, x) => (best === null || x.row.ordinal < best.row.ordinal ? x : best),
    null,
  );
}
