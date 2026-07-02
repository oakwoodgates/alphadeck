import { useState } from "react";

import { useExplainFlag, useExtract, useRatifyFact, type ExtractedFact } from "../api/hooks";
import { errText } from "./format";

const METER_LABEL: Record<string, string> = {
  revenue_mix: "purity",
  shares_outstanding: "market cap · shares",
  cash_burn: "runway · cash + burn",
};

/** The facts panel (hybrid-2b) — extract the scoring facts from the latest filings and RATIFY them, closing
 *  the extract → ratify → re-score loop in the UI. The operator confirms each candidate (AUTO as-is, FLAG
 *  the composition, HUMAN purity authored); on confirm the fact is written and the meter re-derives. The
 *  extract is an EXPLICIT click (cache-first), never on a render. The LOCATED PASSAGE is the evidence —
 *  shown readable inline (not a tooltip), because reading it IS the FLAG ratification decision. */
export function FactsPanel({
  securityId,
  thesisId,
}: {
  securityId: string;
  thesisId?: string;
}) {
  // thesisId (optional) turns on the GROUNDED purity ESTIMATE (SURFACE 1b) for the revenue_mix candidate.
  const extract = useExtract(securityId, thesisId);
  return (
    <div className="facts-panel">
      <button
        type="button"
        className="wb-edit-btn"
        onClick={() => extract.refetch()}
        disabled={extract.isFetching}
      >
        {extract.isFetching ? "Extracting…" : "↻ Extract from filings"}
      </button>
      <div className="note">
        Auto-extract the scoring facts from the latest 10-Q/10-K — you confirm each (AUTO as-is, FLAG the
        composition, purity you author). An explicit call (cache-first); the operator ratifies, the model
        never does.
      </div>
      {extract.error && (
        <div className="note err">
          Couldn't extract — {errText(extract.error)}. (The security needs a CIK, and the stack needs
          ALPHADECK_USER_AGENT.)
        </div>
      )}
      {(extract.data ?? []).map((f) => (
        <RatifyRow key={f.fact_type} candidate={f} securityId={securityId} />
      ))}
    </div>
  );
}

function RatifyRow({ candidate, securityId }: { candidate: ExtractedFact; securityId: string }) {
  const ratify = useRatifyFact();
  const explain = useExplainFlag(candidate); // the LLM seam — FLAG only, an aid to the ratify (below)
  const auto = candidate.tier === "auto";
  const isFlag = candidate.tier === "flag";
  // SURFACE 1b — the GROUNDED purity estimate (llm-proposed, UNVERIFIED): pre-fill the % + the proposed
  // segment (parsed from the note the endpoint wrote) so "confirm as-is" is one action; the operator can
  // override either. Sending the estimate on ratify lets the server stamp `vouched` (confirmed vs overridden).
  const purityEstimate =
    candidate.fact_type === "revenue_mix" && candidate.estimate_source === "llm_proposed"
      ? candidate.value
      : null;
  const proposedSegment = (candidate.note ?? "").match(/on-thesis segment:\s*(.+?)\s*\]/)?.[1] ?? "";
  const [shares, setShares] = useState(candidate.value != null ? String(candidate.value) : "");
  const [cash, setCash] = useState(candidate.cash_usd != null ? String(candidate.cash_usd) : "");
  const [burn, setBurn] = useState(
    candidate.quarterly_burn_usd != null ? String(candidate.quarterly_burn_usd) : "",
  );
  const [segment, setSegment] = useState(purityEstimate != null ? proposedSegment : "");
  const [pct, setPct] = useState(purityEstimate != null ? String(purityEstimate) : "");
  const [note, setNote] = useState(candidate.note ?? "");

  const label = METER_LABEL[candidate.fact_type] ?? candidate.fact_type;
  const common = {
    security_id: securityId,
    source: candidate.source, // the candidate's BASIS, carried through (not retyped)
    source_ref: candidate.source_ref,
    event_date: candidate.event_date,
    note,
  };

  const onConfirm = () => {
    if (candidate.fact_type === "revenue_mix")
      ratify.mutate({
        ...common,
        fact_type: "revenue_mix",
        segment_label: segment,
        mix_pct: Number(pct),
        // the shown estimate (if any) → the server stamps vouched confirmed (unchanged) / overridden (changed)
        estimate: purityEstimate ?? undefined,
      });
    else if (candidate.fact_type === "shares_outstanding")
      ratify.mutate({ ...common, fact_type: "shares_outstanding", shares: Number(shares) });
    else
      ratify.mutate({
        ...common,
        fact_type: "cash_burn",
        cash_usd: Number(cash),
        quarterly_burn_usd: Number(burn),
      });
  };

  // purity needs a segment + a %: with an estimate both pre-fill (confirm as-is), else the operator authors them.
  const confirmDisabled =
    ratify.isPending || (candidate.fact_type === "revenue_mix" && (!pct || !segment));

  if (ratify.isSuccess) {
    // show the vouched outcome for a purity estimate: confirmed-as-is vs overridden (client-derived from the %)
    const vouched =
      purityEstimate == null
        ? ""
        : Number(pct) === purityEstimate
          ? " (confirmed as estimated)"
          : ` (overridden from ${purityEstimate}%)`;
    return (
      <div className="ratify-row done">
        ✓ {label} ratified{vouched} — the meter re-derives.
      </div>
    );
  }

  return (
    <div className={`ratify-row ${candidate.tier}`}>
      <div className="ratify-rh">
        <span className="rk">{label}</span>
        <span className={`rtier ${candidate.tier}`}>{candidate.tier}</span>
        {(candidate.flags ?? []).map((fl) => (
          <span className="rflag" key={fl}>
            ⚠ {fl}
          </span>
        ))}
      </div>

      {/* the located passage — EVIDENCE, readable inline (reading it IS the FLAG decision) */}
      {(candidate.located_passages ?? []).map((p, i) => (
        <div className="located" key={i}>
          <a
            className="chip"
            href={p.source_ref}
            target="_blank"
            rel="noreferrer"
            title={p.source_ref}
          >
            {p.kind} · {p.anchor} ↗
          </a>
          <div className="excerpt">{p.excerpt}</div>
        </div>
      ))}

      {/* the LLM seam (M4b) — a plain-English read GROUNDED in the passage above; an aid to the ratify, never
          the value (it never touches the inputs). FLAG only; explicit (a click); fail-open (no block when the
          model is unavailable or declines — the raw passage + manual ratify stay exactly as today). */}
      {isFlag && (
        <div className="drafted-wrap">
          <button
            type="button"
            className="wb-mini ghost"
            aria-label="Explain in plain English"
            onClick={() => explain.refetch()}
            disabled={explain.isFetching}
          >
            {explain.isFetching ? "Explaining…" : "✦ Explain in plain English"}
          </button>
          {explain.data?.grounded && (
            <div className="drafted">
              <span className="wb-author">drafted</span>
              <span className="drafted-text">{explain.data.explanation}</span>
            </div>
          )}
          {explain.data && !explain.data.grounded && !explain.isFetching && (
            <div className="drafted muted">
              No plain-English read grounded in the passage — read the excerpt above.
            </div>
          )}
        </div>
      )}

      {candidate.fact_type === "shares_outstanding" && (
        <label className="rf">
          shares
          <input
            type="number"
            aria-label="shares"
            value={shares}
            readOnly={auto}
            onChange={(e) => setShares(e.target.value)}
          />
        </label>
      )}
      {candidate.fact_type === "cash_burn" && (
        <>
          <label className="rf">
            cash $
            <input
              type="number"
              aria-label="cash"
              value={cash}
              readOnly={auto}
              onChange={(e) => setCash(e.target.value)}
            />
          </label>
          <label className="rf">
            burn $/qtr
            <input
              type="number"
              aria-label="quarterly burn"
              value={burn}
              readOnly={auto}
              onChange={(e) => setBurn(e.target.value)}
            />
          </label>
        </>
      )}
      {candidate.fact_type === "revenue_mix" && (
        <>
          {purityEstimate != null && (
            <div className="est-tag">
              ✦ estimate {purityEstimate}% · llm-proposed · unverified — confirm as-is, or override
            </div>
          )}
          <label className="rf">
            segment
            <input
              aria-label="segment"
              value={segment}
              placeholder="e.g. nuclear"
              onChange={(e) => setSegment(e.target.value)}
            />
          </label>
          <label className="rf">
            theme %
            <input
              type="number"
              aria-label="purity percent"
              value={pct}
              placeholder={purityEstimate != null ? "" : "you author it"}
              onChange={(e) => setPct(e.target.value)}
            />
          </label>
        </>
      )}
      {!auto && (
        <label className="rf wide">
          note
          <input
            aria-label="note"
            value={note}
            placeholder="the composition / basis"
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
      )}

      {ratify.isError && <div className="note err">Couldn't ratify — {errText(ratify.error)}.</div>}
      <button type="button" className="wb-mini" onClick={onConfirm} disabled={confirmDisabled}>
        {ratify.isPending ? "Saving…" : "Confirm"}
      </button>
    </div>
  );
}
