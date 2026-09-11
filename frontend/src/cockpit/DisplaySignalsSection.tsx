import type {
  DisplayHeadline,
  DisplayMetric,
  DisplaySignal,
  MemberDisplaySignalsOut,
} from "../api/hooks";
import { fmtDate } from "../util/format";
import { insiderNaLabel } from "../workbench/format";
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

export function ReturnCells({ sig }: { sig: DisplaySignal | null }) {
  const byKey = new Map((sig?.metrics ?? []).map((m) => [m.key, m]));
  return (
    <>
      {RETURN_WINDOW_KEYS.map((key) => {
        const m = byKey.get(key);
        return (
          <td className="met retc" key={key}>
            {!m || m.value == null ? (
              <span className="muted" title={m?.note ?? undefined}>
                —
              </span>
            ) : (
              <span className={`ret ${m.tone ?? ""}`}>{fmtMetricValue(m)}</span>
            )}
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
  const names = n === 1 ? "name" : "names";
  const title =
    median == null
      ? `no median below ${AGG_MIN_PRICED} priced names (${priced} of ${n} priced)`
      : `median 7d return over the ${priced} priced of ${n} ${names}`;
  const tone = median == null ? "na" : median > 0 ? "pos" : median < 0 ? "neg" : "";
  return (
    <span className="agg" title={title}>
      · 7d median{" "}
      <span className={`aggv${tone ? ` ${tone}` : ""}`}>
        {median == null
          ? "—"
          : fmtMetricValue({ key: AGG_RETURN_KEY, label: "7d median", value: median, unit: "pct" })}
      </span>
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
export function SparklineCell({ sig }: { sig: DisplaySignal | null }) {
  const series = (sig?.series ?? []).find((s) => s.key === SPARK_SERIES_KEY);
  const geo = series ? sparkGeometry(series.values) : null;
  if (!sig || !geo)
    return (
      <span className="muted" title={sig?.basis.note ?? undefined}>
        —
      </span>
    );
  return (
    <span className="spark" title={basisLine(sig)}>
      <svg
        className="spark-svg"
        width={SPARK_W}
        height={SPARK_H}
        viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
        role="img"
        aria-label={`price path, ${geo.bars} bars`}
      >
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
