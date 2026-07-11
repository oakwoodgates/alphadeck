import type {
  BasketMember,
  CallCardResponse,
  MemberCallOut,
  ScoredMemberOut,
} from "../api/hooks";

/** The per-name buckets (strongest → weakest) — the Board's column idiom applied INSIDE a basket.
 *  Five are the spec'd member states; WARMING and QUIET are the coverage groups the wire forces:
 *  a conviction-only name is in NEITHER `armed_members` NOR `watch_members` (watch is
 *  confirmation-only), yet its trigger is live on the rail — filing it under "no signals" would be
 *  dishonest. And every basket row must land somewhere (pruning hides, it never vanishes), so the
 *  remainder is QUIET, not absent. Display-only: the buckets re-read the call, never re-derive it. */
export type BucketKey =
  | "managing"
  | "armed"
  | "lapsing"
  | "theme_armed"
  | "warming"
  | "watch"
  | "quiet";

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
  { key: "lapsing", label: "Lapsing", hint: "entry window closing", cls: "bkt-lapsing" },
  { key: "theme_armed", label: "Theme-armed", hint: "theme fallback · starter cap", cls: "bkt-theme" },
  { key: "warming", label: "Warming", hint: "conviction in · awaiting confirmation", cls: "bkt-warming" },
  { key: "watch", label: "Watch", hint: "moving · no conviction yet", cls: "bkt-watch" },
  { key: "quiet", label: "Quiet", hint: "no live signals", cls: "bkt-quiet" },
];

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
 *    verdict === "managing"  →  managing        (render-if-present)
 *    in armed_members        →  lapsing if `lapsing` (the clock is the urgent fact — it wins over
 *                               the theme flag, which stays visible as a chip), else theme_armed
 *                               if `theme_armed`, else armed
 *    in watch_members        →  watch           (checked BEFORE the trigger join — a watch member's
 *                               breakout is itself in triggers_fired)
 *    ticker in triggers_fired →  warming        (conviction-only: live trigger, no member call)
 *    otherwise               →  quiet
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
    else bucket = "quiet";
    return {
      member,
      ordinal,
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
