import type {
  DisplayHeadline,
  DisplayMetric,
  DisplaySignal,
  MemberDisplaySignalsOut,
} from "../api/hooks";
import { fmtDate } from "../util/format";
import { insiderNaLabel } from "../workbench/format";
import { FlagWrap } from "./DataFlagMark";
import type { CellFlag, DerivedCell } from "./dataFlags";
import type { TapeMark } from "./tapeMarks";
import { AGG_MIN_PRICED, AGG_RETURN_KEY, type GroupMoving } from "./groupAggregate";

/** One metric chip's value, by wire unit. Handles every unit the payload can carry so a new
 *  backend member renders with ZERO frontend change (the framework's whole point). */
export function fmtMetricValue(m: DisplayMetric): string {
  if (m.value == null) return "—";
  switch (m.unit) {
    case "pct":
      return `${m.value > 0 ? "+" : ""}${m.value.toFixed(1)}%`;
    case "price":
      return m.value.toFixed(2);
    case "usd":
      return `$${Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(m.value)}`;
    case "ratio":
      return `${m.value.toFixed(2)}×`;
    case "count":
      return String(Math.round(m.value));
    default:
      return String(m.value);
  }
}

// The posture-glyph tokens the wire can carry -> the arrow the chip shows. Rising-family glyphs
// tint positive, falling-family negative (glyph only — the chip itself stays mono, #7). Exported:
// the sleeve dossier's fund-flow chip (SleeveRail) renders the same tokens.
export const GLYPH: Record<string, string> = {
  up: "↑",
  turn_up: "↗",
  turn_down: "↘",
  down: "↓",
  flat: "→",
};

/** One state-headline row — rendered in the panel's TOP strip (the operator's at-a-glance read),
 *  hoisted out of the Indicators section at the operator's request. The stable machine key rides
 *  the hover title; only the glyph carries a direction tint. */
export function DisplayHeadlineRow({ headline }: { headline: DisplayHeadline }) {
  return (
    <div className="np-ind-headline" title={headline.key}>
      <span className={`g dirg ${headline.glyph ?? ""}`}>
        {GLYPH[headline.glyph ?? ""] ?? "·"}
      </span>
      <span className="t">{headline.label}</span>
      {headline.detail && <span className="d">{headline.detail}</span>}
    </div>
  );
}

/** The basket-table grain of the SMA posture: the quadrant glyph + % vs the slow line, with the
 *  literal statement on hover. "—" when the name has no reading (no bars) — never a blank cell. */
export function PostureCell({ sig }: { sig: DisplaySignal | null }) {
  const h = sig?.headline;
  const pct = (sig?.metrics ?? []).find((m) => m.key === "pct_vs_slow");
  if (!h && pct?.value == null) return <span className="muted">—</span>;
  return (
    <span
      className="sma-cell"
      title={h ? `${h.label}${h.detail ? ` — ${h.detail}` : ""}` : undefined}
    >
      {h && <span className={`g dirg ${h.glyph ?? ""}`}>{GLYPH[h.glyph ?? ""] ?? "·"}</span>}
      {pct?.value != null && <span className="pv">{fmtMetricValue(pct)}</span>}
    </span>
  );
}

/** The trailing-return columns (1d/7d/30d/90d/1Y) for one basket row, from the `trailing_returns`
 *  display member — rendered as a Fragment of `<td>`s so they sit inline as separate columns (and so
 *  they render in BOTH cockpit lenses; they live on the per-name row). Each is the window's % return,
 *  tinted green (up) / red (down) off the metric's OWN `tone` (the same --pos/--neg tokens the panel
 *  chips use); a thin-history / non-positive-base gap is an HONEST "—" with the why on hover (#6/#9),
 *  never a fabricated number, and a flat 0.0% stays neutral. Reuses fmtMetricValue (+2.6% / -12.3%).
 *  1Y (`ret_1y`) is 252 trading bars — a young name (<~1y of tape) honestly blanks that cell. */
export const RETURN_WINDOW_KEYS = ["ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y"] as const;

export function ReturnCells({
  sig,
  flagFor,
}: {
  sig: DisplaySignal | null;
  /** S3 — the data flag reaching a window's cell (keyed like the metric: `ret_7d`), or null. */
  flagFor?: (key: string) => CellFlag | null;
}) {
  const byKey = new Map((sig?.metrics ?? []).map((m) => [m.key, m]));
  return (
    <>
      {RETURN_WINDOW_KEYS.map((key) => {
        const m = byKey.get(key);
        return (
          <td className="met retc" key={key}>
            <FlagWrap flag={flagFor?.(key)}>
              {!m || m.value == null ? (
                <span className="muted" title={m?.note ?? undefined}>
                  —
                </span>
              ) : (
                <span className={`ret ${m.tone ?? ""}`}>{fmtMetricValue(m)}</span>
              )}
            </FlagWrap>
          </td>
        );
      })}
    </>
  );
}

/** The group header's "is this group moving?" line (every grouped lens): `· 7d median ±X.X%` off the
 *  `groupMoving` aggregate — the MEDIAN 7d return over the group's PRICED rows (the same `ret_7d` the
 *  7d cells show, computed client-side), or a muted "—" below `AGG_MIN_PRICED` priced rows. Renders
 *  in the cells' own pct format (fmtMetricValue: signed, 1dp) and tints MUTED green up / red down —
 *  always-present context, not a badge, so no threshold accent and nothing flashes (#7 / interaction
 *  principle #3); a flat 0.0% stays neutral. The population ("5 priced of 6 names") rides the hover
 *  title (show the work, #6). A display aggregate, never a call input (#4). */
export function GroupMovingLine({ stat }: { stat: GroupMoving }) {
  const { n, priced, median } = stat;
  const leftOut = stat.leftOut ?? 0;
  const names = n === 1 ? "name" : "names";
  // S3: a 7d value a data flag reaches never moves the group read — it is left out, and the header says so
  const outNote =
    leftOut > 0
      ? `; ${leftOut} flagged 7d ${leftOut === 1 ? "value" : "values"} left out (a data flag — hover the row's mark)`
      : "";
  const title =
    (median == null
      ? `no median below ${AGG_MIN_PRICED} priced names (${priced} of ${n} priced)`
      : `median 7d return over the ${priced} priced of ${n} ${names}`) + outNote;
  const tone = median == null ? "na" : median > 0 ? "pos" : median < 0 ? "neg" : "";
  return (
    <span className="agg" title={title}>
      · 7d median{" "}
      <span className={`aggv${tone ? ` ${tone}` : ""}`}>
        {median == null
          ? "—"
          : fmtMetricValue({ key: AGG_RETURN_KEY, label: "7d median", value: median, unit: "pct" })}
      </span>
      {leftOut > 0 && <span className="agg-out"> · {leftOut} left out</span>}
    </span>
  );
}

/** One basket-table RVOL cell from the `rvol` display member: the as-of bar's volume ÷ the mean
 *  volume of the N bars before it. The member emits TWO windows off one fetch and `metricKey` /
 *  `loudKey` select which — `rvol` / `loud_mult` is the **8-bar** (call-matched) read, `rvol20` /
 *  `loud_mult_20` the **20-bar** (trader-convention, call-decoupled) read; each accents from its OWN
 *  threshold on the wire so the FE hardcodes nothing. Renders "N.NN×"; a volume-backed reading —
 *  value at/above that window's threshold — reads a WARM 'hot' accent (the exception, not every row,
 *  #7), never the return-green tone. A gap (a halt / thin OTC as-of bar, or a name short of the
 *  window's base) is an HONEST "—" with the why on hover (#6/#9). */
export function RvolCell({
  sig,
  metricKey = "rvol",
  loudKey = "loud_mult",
}: {
  sig: DisplaySignal | null;
  metricKey?: string;
  loudKey?: string;
}) {
  const m = (sig?.metrics ?? []).find((x) => x.key === metricKey);
  if (!m || m.value == null)
    return (
      <span className="muted" title={m?.note ?? undefined}>
        —
      </span>
    );
  const loud = sig?.basis.params?.[loudKey];
  const hot = typeof loud === "number" && m.value >= loud;
  return (
    <span className={`rvol${hot ? " hot" : ""}`} title={hot ? "volume-backed move" : undefined}>
      {fmtMetricValue(m)}
    </span>
  );
}

/** One basket-table insider-buys cell from the `insider_flow_90d` display member:
 *  **`{open-market buys}/{distinct buyers}`** over the cell's trailing window (30d or 90d — the
 *  member emits both off ONE fetch; `countKey` / `buyersKey` select which). Renders "8/3"; a MUTED
 *  "—" when there are zero buys OR the metric is absent (the COMMON case — most names have no insider
 *  buying, so the quiet default marks the rule and the accent the exception, #7). A CLUSTER (≥2
 *  distinct buyers) reads the leader-blue 'conviction' accent — breadth is the stronger insider tell
 *  (the platform's insider theses fire on MULTIPLE insiders, not one insider's volume); a lone buyer
 *  still shows, just un-accented. The accent is deliberately NOT the RVOL warm nor the return
 *  green/red — a distinct cool 'conviction' hue. The per-cell tooltip spells the window out. */
export function InsiderCell({
  sig,
  countKey,
  buyersKey,
  window,
}: {
  sig: DisplaySignal | null;
  countKey: string;
  buyersKey: string;
  window: string;
}) {
  const buys = (sig?.metrics ?? []).find((m) => m.key === countKey)?.value;
  const buyers = (sig?.metrics ?? []).find((m) => m.key === buyersKey)?.value;
  // zero buys is the rule, not a gap — a muted "—" (never "0/0"); an absent metric reads the same
  if (buys == null || buys === 0) return <span className="muted">—</span>;
  const buyN = Math.round(buys);
  const buyerN = Math.round(buyers ?? 0);
  const cluster = buyerN >= 2; // breadth, not volume, is the conviction tell (#7)
  return (
    <span
      className={`insider${cluster ? " cluster" : ""}`}
      title={`${buyN} open-market ${buyN === 1 ? "buy" : "buys"} by ${buyerN} ${
        buyerN === 1 ? "insider" : "insiders"
      }, last ${window}`}
    >
      {buyN}/{buyerN}
    </span>
  );
}

/** A last-close price: "$27.71", thousands separated, and a name under a dollar gets four decimals (a $0.0431
 *  stock must not read "$0.04"). */
export function fmtLastPrice(v: number): string {
  const digits = v >= 1 ? 2 : 4;
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** One basket-table `Last close` cell from the `sma_position` display member's `close` metric: the newest
 *  close at or before the as-of — an END-OF-DAY print, never a live quote (this platform has no intraday). It
 *  stays on screen even when the tape has stopped (that is what "last known" means), and then says WHEN: a
 *  muted " · Oct 14" appears when the bar is older than the newest bar anywhere in the basket (`newestBar`),
 *  when the member itself calls it stale, or when the feed is flagged stopped (`stopped` = the flag's reason,
 *  which rides the hover). The date marks the exception — most rows print today's bar and carry none (#7). A
 *  name with no bars is an honest "—". */
export function LastCell({
  sig,
  newestBar,
  stopped = null,
}: {
  sig: DisplaySignal | null;
  /** the newest last-bar date across the basket's rows (ISO) — a row older than it is lagging */
  newestBar?: string | null;
  stopped?: string | null;
}) {
  const m = (sig?.metrics ?? []).find((x) => x.key === "close");
  if (!sig || !m || m.value == null)
    return (
      <span className="muted" title={m?.note ?? sig?.basis.note ?? undefined}>
        —
      </span>
    );
  const end = sig.basis.window_end ?? null;
  const memberStale = /^stale/.test(sig.basis.note ?? "");
  const old = memberStale || stopped != null || (end != null && newestBar != null && end < newestBar);
  const price = fmtLastPrice(m.value);
  return (
    <span
      className={`last${old ? " old" : ""}`}
      title={[
        `last close ${price}${end ? ` on ${fmtDate(end)}` : ""} (end of day, not a live quote)`,
        sig.basis.note,
        stopped,
      ]
        .filter(Boolean)
        .join(" · ")}
    >
      {price}
      {old && end && <span className="last-date"> · {fmtDate(end)}</span>}
    </span>
  );
}

/** One basket-table `52w` cell from the `range_52w` display member: how far the last close sits BELOW the
 *  52-week closing high ("-23.4%"; 0.0% = at the high). Muted mono, no accent — it is always-present context,
 *  and "at the high" is already the exception the number itself states (#7). The hover shows the work: the high
 *  and the bar it printed on, plus the window's own note when it is shorter than a year (#6). A gap is an
 *  honest "—" with the why. */
export function Off52wCell({ sig }: { sig: DisplaySignal | null }) {
  const m = (sig?.metrics ?? []).find((x) => x.key === "pct_off_52w_high");
  if (!sig || !m || m.value == null)
    return (
      <span className="muted" title={m?.note ?? sig?.basis.note ?? undefined}>
        —
      </span>
    );
  const high = (sig.metrics ?? []).find((x) => x.key === "high_52w");
  const title = [
    high?.value != null
      ? `52-week closing high ${high.value.toFixed(2)}${high.note ? ` ${high.note}` : ""}`
      : null,
    basisLine(sig),
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className="off52w" title={title}>
      {fmtMetricValue(m)}
    </span>
  );
}

/** One basket-table `ADV 20d` cell from the `volume_regime` display member: the 20-bar average DOLLAR volume
 *  (close × volume) — the liquidity read, compact ("$1.2M"). Muted mono, no accent and no threshold: where
 *  "thin" begins is the operator's call, not the table's. A name short of 20 volume bars, or none, is an
 *  honest "—" with the why on hover; the basis note (bars without a volume, excluded) rides the title. */
export function AdvCell({ sig }: { sig: DisplaySignal | null }) {
  const m = (sig?.metrics ?? []).find((x) => x.key === "adv_usd_20d");
  if (!sig || !m || m.value == null)
    return (
      <span className="muted" title={m?.note ?? sig?.basis.note ?? undefined}>
        —
      </span>
    );
  return (
    <span
      className="adv"
      title={[`average dollar volume (close × volume) over the last 20 bars`, sig.basis.note]
        .filter(Boolean)
        .join(" · ")}
    >
      {fmtMetricValue(m)}
    </span>
  );
}

/** The basket-table `Tape` cell: one small chip per EXCEPTIONAL tape reading (`tapeMarks`) — a coiling base,
 *  relative strength at a 13-week high, a recent golden / death cross. The default is a muted "—" (most names
 *  print none), so a chip marks the exception, never the rule (#7). Each chip stays mono and neutral with only
 *  its direction glyph tinted (the shared `.dirg` rule); the member's own statement rides the hover (#6). Each
 *  chip is flagged on its own reach (`flagFor`): in doubt grays it, known wrong replaces it with "—". Not
 *  sortable — a set of marks is not a number to rank on. */
export function TapeCell({
  marks,
  flagFor,
}: {
  marks: readonly TapeMark[];
  /** the data flag reaching the bars a mark reads, by its derived-cell proxy (or null) */
  flagFor?: (cell: DerivedCell) => CellFlag | null;
}) {
  if (marks.length === 0) return <span className="muted">—</span>;
  return (
    <span className="tape">
      {marks.map((m) => (
        <FlagWrap key={m.key} flag={flagFor?.(m.reach)}>
          <span className={`tape-chip ${m.key}`} title={m.title}>
            <span className={`g dirg ${m.glyph}`}>{GLYPH[m.glyph]}</span>
            {m.label}
          </span>
        </FlagWrap>
      ))}
    </span>
  );
}

/** The sparkline cell's box (px). The `price_path` member's 90 slots over ~72px is a hairline per
 *  bar; the geometry scales to whatever slot count a series carries — the FE hardcodes no window. */
export const SPARK_W = 72;
export const SPARK_H = 16;
const SPARK_PAD = 1; // keeps the 1px stroke inside the box at the min/max slots
/** The one series the basket cell reads off the `price_path` member. */
export const SPARK_SERIES_KEY = "close";

export interface SparkGeometry {
  /** One SVG path `d` per run of ≥2 consecutive real slots. A null slot BREAKS the line — the runs
   *  either side of a gap are separate paths, never joined (no interpolation, #6/#9). */
  paths: string[];
  /** Isolated real slots (a run of one between gaps): drawn as a dot, so a bar the tape printed
   *  is still shown, never silently dropped — and never stretched into a line it isn't. */
  dots: { x: number; y: number }[];
  /** The count of real (non-null) slots — the bars actually drawn. */
  bars: number;
}

/** The pure geometry of a fixed-slot series: slot i sits at x = i/(n−1)·w across the WHOLE window,
 *  so a left-padded (young) tape draws a genuinely shorter, right-aligned path; y maps the real
 *  values' min → bottom / max → top (a flat series is a midline, never a zero-range blow-up).
 *  Returns null below two real values — a point is not a path, and the cell reads "—". */
export function sparkGeometry(
  values: readonly (number | null | undefined)[],
  w = SPARK_W,
  h = SPARK_H,
): SparkGeometry | null {
  const isReal = (v: number | null | undefined): v is number =>
    typeof v === "number" && Number.isFinite(v);
  const real = values.filter(isReal);
  if (real.length < 2) return null;
  const n = values.length;
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of real) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const r1 = (v: number) => Math.round(v * 10) / 10; // 0.1px — a light DOM, not a precise one
  const xAt = (i: number) => r1(n > 1 ? (i / (n - 1)) * w : w / 2);
  const yAt = (v: number) =>
    r1(hi === lo ? h / 2 : SPARK_PAD + (1 - (v - lo) / (hi - lo)) * (h - 2 * SPARK_PAD));
  const paths: string[] = [];
  const dots: { x: number; y: number }[] = [];
  let run: { x: number; y: number }[] = [];
  const flush = () => {
    if (run.length >= 2) {
      paths.push(run.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.y}`).join(" "));
    } else if (run.length === 1) {
      dots.push(run[0]);
    }
    run = [];
  };
  values.forEach((v, i) => {
    if (isReal(v)) run.push({ x: xAt(i), y: yAt(v) });
    else flush(); // the gap: close the run here, never bridge to the next real slot
  });
  flush();
  return { paths, dots, bars: real.length };
}

/** One basket-table sparkline cell from the `price_path` display member's fixed-slot `close`
 *  series — the SHAPE behind the return ladder's endpoint numbers. A NEUTRAL hairline in the muted
 *  text gray, no accent: it must never conflate with the return green/red, the RVOL warm, or the
 *  insider blue (#7). A null slot is an honest gap the line BREAKS on — never interpolated — so a
 *  young name draws a shorter, right-aligned path; fewer than two real closes (or no series at all)
 *  reads a muted "—" with the why on hover (a point is not a path, #6/#9). Not sortable: a shape,
 *  never a number the sort or the call could read (#4). The exact tape rides the hover title. */
/** S3 — a split's tick on the sparkline: `barsAgo` bars before the path's newest slot (the split row's
 *  `bars_ago` — the first bar at the new price), and its label for the hover. */
export interface SparkTick {
  barsAgo: number;
  label: string;
}

/** The x of each tick that falls inside an `n`-slot window (slot n−1 = the newest bar); a split older than
 *  the window draws nothing. Pure — the same x mapping as `sparkGeometry`. */
export function sparkTicks(
  ticks: readonly SparkTick[],
  n: number,
  w = SPARK_W,
): { x: number; label: string }[] {
  const r1 = (v: number) => Math.round(v * 10) / 10;
  return ticks
    .filter((t) => t.barsAgo >= 0 && t.barsAgo < n)
    .map((t) => {
      const i = n - 1 - t.barsAgo;
      return { x: r1(n > 1 ? (i / (n - 1)) * w : w / 2), label: t.label };
    });
}

export function SparklineCell({
  sig,
  ticks = [],
}: {
  sig: DisplaySignal | null;
  /** S3 — the company's splits inside the window: a tick mark at most (plan section 11). */
  ticks?: readonly SparkTick[];
}) {
  const series = (sig?.series ?? []).find((s) => s.key === SPARK_SERIES_KEY);
  const geo = series ? sparkGeometry(series.values) : null;
  if (!sig || !geo)
    return (
      <span className="muted" title={sig?.basis.note ?? undefined}>
        —
      </span>
    );
  const marks = sparkTicks(ticks, series!.values.length);
  const title = [basisLine(sig), ...marks.map((t) => t.label)].join(" · ");
  return (
    <span className="spark" title={title}>
      <svg
        className="spark-svg"
        width={SPARK_W}
        height={SPARK_H}
        viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
        role="img"
        aria-label={`price path, ${geo.bars} bars${marks.length ? `, ${marks.length} split(s)` : ""}`}
      >
        {marks.map((t, i) => (
          <line key={`t${i}`} className="spark-tick" x1={t.x} x2={t.x} y1={0} y2={SPARK_H} />
        ))}
        {geo.paths.map((d, i) => (
          <path key={i} d={d} />
        ))}
        {geo.dots.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={1} />
        ))}
      </svg>
    </span>
  );
}

function basisLine(sig: DisplaySignal): string {
  const b = sig.basis;
  const parts: string[] = [];
  if (b.bars_used != null) parts.push(`${b.bars_used} bars`);
  if (b.window_end) parts.push(`through ${fmtDate(b.window_end)}`);
  if (b.note) parts.push(b.note);
  return parts.join(" · ") || b.source;
}

/** "Indicators · this name" — the read-only display signals (docs/DISPLAY_SIGNALS.md): quiet
 *  metric chips, muted dated flip lines, and a fine-print basis (show-the-work, #6). Ambient tape
 *  context, never a trigger and never loud (#7): honest gaps read "—" with the why; no data at all
 *  reads one muted line. Renders every registered member uniformly off the generic payload.
 *
 *  `foreignFilerForm` (the wire `foreign_filer_form`): when set (a §16-exempt 20-F/40-F filer) AND the
 *  payload carries no insider signal, an ambient MUTED N/A row explains the STRUCTURAL absence of the
 *  insider read — "unavailable, not quiet" (#7). Belt-and-suspenders on the insider check: a foreign filer
 *  files no Form 4, so it never carries one anyway. */
export function DisplaySignalsSection({
  display,
  foreignFilerForm,
}: {
  display: MemberDisplaySignalsOut | null;
  foreignFilerForm?: string | null;
}) {
  const signals = display?.signals ?? [];
  const showInsiderNa =
    !!foreignFilerForm && !signals.some((s) => s.kind === "insider_flow_90d");
  return (
    <>
      <div className="np-h">Indicators · this name</div>
      {signals.length === 0 && !showInsiderNa ? (
        <div className="np-stateline">No indicator data at this as-of.</div>
      ) : (
        signals.map((sig) => (
          <div className="np-ind" key={sig.kind}>
            {/* the headline renders in the panel's TOP strip, not here — this section keeps the
                full detail: the chips, the dated flips, and the basis */}
            <div className="np-ind-label">{sig.label}</div>
            <div className="np-ind-chips">
              {(sig.metrics ?? []).map((m) => (
                <span
                  className={`np-ind-chip${m.tone ? ` ${m.tone}` : ""}`}
                  key={m.key}
                  title={m.note ?? undefined}
                >
                  <span className="k">{m.label}</span>
                  <span className={`v${m.value == null ? " na" : ""}`}>{fmtMetricValue(m)}</span>
                  {m.value == null && m.note && <span className="note">{m.note}</span>}
                </span>
              ))}
            </div>
            {(sig.events ?? []).map((e) => (
              <div className="np-ind-event" key={e.key}>
                <span className={`dir ${e.direction ?? ""}`}>
                  {e.direction === "down" ? "↓" : e.direction === "up" ? "↑" : "·"}
                </span>
                <span className="lbl">{e.label}</span>
                <span className="dt">{fmtDate(e.date)}</span>
              </div>
            ))}
            {/* the show-the-work fine print (#6); full params ride the hover title */}
            <div
              className="np-ind-basis"
              title={`${sig.basis.source} · ${JSON.stringify(sig.basis.params)}`}
            >
              {basisLine(sig)}
            </div>
          </div>
        ))
      )}
      {/* the §16-exempt foreign-filer insider N/A — ambient, never loud (#7): reuses the np-ind markup +
          .na muted styling; the structural "why" rides the fine-print basis line */}
      {showInsiderNa && (
        <div className="np-ind">
          <div className="np-ind-label">Insider flow</div>
          <div className="np-ind-chips">
            <span className="np-ind-chip">
              <span className="k">insider flow</span>
              <span className="v na">{insiderNaLabel(foreignFilerForm)}</span>
            </span>
          </div>
          <div className="np-ind-basis">
            §16 exempts foreign private issuers / MJDS filers from Form 4 — the insider signal is
            structurally unavailable here, not quiet.
          </div>
        </div>
      )}
    </>
  );
}
