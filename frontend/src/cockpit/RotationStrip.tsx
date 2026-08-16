import type { DisplayMetric, DisplaySignal } from "../api/hooks";
import { DisplayHeadlineRow, fmtMetricValue } from "./DisplaySignalsSection";

/** The thesis-level rotation readings and the ONE headline key that is "loud" for each (honest
 *  loudness, #7): a BREADTH reading is loud only when the theme is THRUSTING (key `thrust`); a
 *  SECTOR-RS reading only when a supersector is LEADING (key `leading`). Every other categorical the
 *  wire can carry — `quiet` / `unknown` / `inline` — renders neutral, so the strip marks the
 *  exception, never every row. Kept in lock-step with the backend producers
 *  (`signals/display/theme_breadth.py`, `signals/display/relative_strength.py`). */
const LOUD_KEY: Record<"breadth" | "sector_rs", string> = {
  breadth: "thrust",
  sector_rs: "leading",
};

/** One rotation reading (Breadth or Sector RS): a labeled block reusing the panel's headline row +
 *  metric-chip markup. Loud ONLY when the reading's headline sits at its loud key. Read-only tape
 *  context beside the call — it states the rotation, it never fires / arms / vetoes / grades (#4). */
function RotationReading({
  slot,
  label,
  sig,
}: {
  slot: "breadth" | "sector_rs";
  label: string;
  sig: DisplaySignal;
}) {
  const loud = sig.headline?.key === LOUD_KEY[slot];
  const metrics = sig.metrics ?? [];
  return (
    <div className={`rot-item${loud ? " loud" : ""}`}>
      <div className="rot-k">{label}</div>
      {sig.headline && <DisplayHeadlineRow headline={sig.headline} />}
      {/* the chips reuse the panel's np-ind-chip markup + fmtMetricValue (breadth %, sector leader
          counts) — quiet by design; an honest gap reads "—" with the why on hover, never a fake number */}
      {metrics.length > 0 && (
        <div className="np-ind-chips">
          {metrics.map((m: DisplayMetric) => (
            <span
              className={`np-ind-chip${m.tone ? ` ${m.tone}` : ""}`}
              key={m.key}
              title={m.note ?? undefined}
            >
              <span className="k">{m.label}</span>
              <span className={`v${m.value == null ? " na" : ""}`}>{fmtMetricValue(m)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** The Cockpit's THESIS-LEVEL rotation strip: the theme-breadth thrust + sector-RS leadership readings,
 *  off the SAME `/theses/{id}/display-signals` payload the basket cells already read — they ride the
 *  TOP-LEVEL `breadth` / `sector_rs` fields (not the per-member list). Rendered above the basket table.
 *
 *  Read-only display context (#4/#6): it shows the tape's rotation and its work, it is structurally not a
 *  SignalEvent and never turns a key. Honest loudness (#7): a field renders NOTHING when it is null (no
 *  benchmark tape / thin history / a thesis with no resolved members), and the strip itself renders
 *  nothing when BOTH are absent — no empty shell, no empty chip. */
export function RotationStrip({
  breadth,
  sectorRs,
}: {
  breadth: DisplaySignal | null;
  sectorRs: DisplaySignal | null;
}) {
  if (!breadth && !sectorRs) return null;
  return (
    <section className="sect rot-strip">
      <div className="sect-h">Rotation · theme tape</div>
      <div className="rot-row">
        {breadth && <RotationReading slot="breadth" label="Breadth" sig={breadth} />}
        {sectorRs && <RotationReading slot="sector_rs" label="Sector RS" sig={sectorRs} />}
      </div>
    </section>
  );
}
