import type { BacktestManifest } from "../api/hooks";

// WHAT PRODUCED THESE NUMBERS. A result without its manifest is an anecdote, so this card is not an
// "info" panel to be collapsed — it is the half of the page that makes the other half readable.
//
// The fields answer the questions a reader has to settle before quoting anything: which dials moved
// (and how many runs have moved them — the multiple-comparisons context), which clock, which window,
// which code, and what the run could NOT see (excluded fact tables, the detectors they blind, and the
// rows dropped for want of a disclosure date).

type Props = {
  manifest: BacktestManifest;
  /** How many runs in the registry have moved each dial — "count your trials", per dial. */
  dialTrials: Record<string, number>;
};

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function ManifestCard({ manifest: m, dialTrials }: Props) {
  const diff = (m.overlay_diff ?? {}) as Record<string, { default?: unknown; run?: unknown }>;
  const moved = Object.keys(diff).sort();
  const mirror = m.mirror;
  const excluded = mirror?.excluded_tables ?? [];
  const blind = mirror?.blind_detectors ?? [];
  const tables = Object.values(mirror?.tables ?? {});
  const droppedRows = tables.reduce((a, t) => a + (t.rows_dropped_null_clock ?? 0), 0);
  const identitiesLost = tables.reduce((a, t) => a + (t.identities_lost ?? 0), 0);
  const theses = m.theses ?? [];
  const onSnapshot = theses.filter((t) => t.roster_source === "snapshot").length;

  return (
    <section className="bt-card" aria-label="What produced this run">
      <dl className="bt-facts">
        <div>
          <dt>Window</dt>
          <dd>
            {m.window_start} → {m.window_end}
          </dd>
        </div>
        <div>
          <dt>Clock</dt>
          <dd
            title={
              m.clock === "public"
                ? "facts enter when they became public (the disclosure instant), not when this system ingested them"
                : "facts enter when this system recorded them — the honest system clock, which can lag the disclosure"
            }
          >
            {m.clock} · known_at {m.known_at_mode}
          </dd>
        </div>
        <div>
          <dt>Policy</dt>
          <dd title={m.config_hash}>{m.config_short}</dd>
        </div>
        <div>
          <dt>Code</dt>
          {/* unknown when the image carries no sha — never fabricated, the same rule a calls row holds */}
          <dd title={m.code_sha ?? undefined}>{m.code_sha ? m.code_sha.slice(0, 8) : "unknown"}</dd>
        </div>
        <div>
          <dt>Pin</dt>
          <dd title="the known_at ceiling — every point-in-time read in this run was capped here">
            {m.pin}
          </dd>
        </div>
        <div>
          <dt>Nulls</dt>
          <dd title="draws per episode for each null model, and the seed that reproduces them">
            K={m.null_draws} · seed {m.null_seed}
          </dd>
        </div>
      </dl>

      <h4>Dials moved</h4>
      {moved.length === 0 ? (
        <p className="bt-quiet">None — this run used the production dials.</p>
      ) : (
        <table className="bt-dials">
          <thead>
            <tr>
              <th>Dial</th>
              <th>Default</th>
              <th>This run</th>
              <th title="how many runs in the registry have moved this dial — your trial count">
                Runs touching it
              </th>
            </tr>
          </thead>
          <tbody>
            {moved.map((d) => (
              <tr key={d}>
                <td>{d}</td>
                <td>{fmt(diff[d]?.default)}</td>
                <td>{fmt(diff[d]?.run)}</td>
                <td>{dialTrials[d] ?? 1}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Pre-registration, shown only when there is one. The CLI refuses an overlay without both, so a
          run that moved a dial always has them; a bare exploratory run legitimately does not, and an
          empty "Hypothesis: —" would invite reading one in after the fact. */}
      {m.hypothesis && (
        <>
          <h4>Pre-registered — written before the run</h4>
          <p className="bt-pre">
            <strong>Hypothesis.</strong> {m.hypothesis}
          </p>
          {m.decision_rule && (
            <p className="bt-pre">
              <strong>Decision rule.</strong> {m.decision_rule}
            </p>
          )}
          {m.regime && (
            <p className="bt-pre">
              <strong>Regime.</strong> {m.regime}
            </p>
          )}
        </>
      )}

      <h4>What this run could not see</h4>
      <p className="bt-quiet">
        {excluded.length === 0 ? "No fact table was excluded." : `Excluded: ${excluded.join(", ")}.`}{" "}
        {blind.length === 0 ? "No detector was blinded." : `Blind detectors: ${blind.join(", ")}.`}{" "}
        {droppedRows > 0 || identitiesLost > 0
          ? `${droppedRows.toLocaleString()} row(s) carried no usable disclosure date and were excluded; ${identitiesLost.toLocaleString()} fact identity/identities were lost entirely.`
          : "No row was dropped for want of a disclosure date."}
      </p>

      {/* Roster provenance, quantitative rather than binary: a thesis that fell back for 2 sessions of
          300 is a very different artifact from one that fell back for all 300. */}
      {theses.length > 0 && (
        <p className="bt-quiet">
          Rosters: {onSnapshot} of {theses.length} thesis/theses replayed on a point-in-time roster;
          the rest recomputed on today&apos;s basket.
        </p>
      )}
    </section>
  );
}
