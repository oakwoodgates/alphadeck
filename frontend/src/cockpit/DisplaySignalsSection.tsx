import type {
  DisplayHeadline,
  DisplayMetric,
  DisplaySignal,
  MemberDisplaySignalsOut,
} from "../api/hooks";
import { fmtDate } from "../util/format";

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
// tint positive, falling-family negative (glyph only — the chip itself stays mono, #7).
const GLYPH: Record<string, string> = {
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
 *  reads one muted line. Renders every registered member uniformly off the generic payload. */
export function DisplaySignalsSection({ display }: { display: MemberDisplaySignalsOut | null }) {
  const signals = display?.signals ?? [];
  return (
    <>
      <div className="np-h">Indicators · this name</div>
      {signals.length === 0 ? (
        <div className="np-stateline">No indicator data at this as-of.</div>
      ) : (
        signals.map((sig) => (
          <div className="np-ind" key={sig.kind}>
            {/* the headline renders in the panel's TOP strip, not here — this section keeps the
                full detail: the chips, the dated flips, and the basis */}
            <div className="np-ind-label">{sig.label}</div>
            <div className="np-ind-chips">
              {(sig.metrics ?? []).map((m) => (
                <span className="np-ind-chip" key={m.key} title={m.note ?? undefined}>
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
    </>
  );
}
