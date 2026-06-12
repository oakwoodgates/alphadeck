import { useState } from "react";

import { useExtract, useRatifyFact, type ExtractedFact } from "../api/hooks";
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
export function FactsPanel({ securityId }: { securityId: string }) {
  const extract = useExtract(securityId);
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
  const auto = candidate.tier === "auto";
  const [shares, setShares] = useState(candidate.value != null ? String(candidate.value) : "");
  const [cash, setCash] = useState(candidate.cash_usd != null ? String(candidate.cash_usd) : "");
  const [burn, setBurn] = useState(
    candidate.quarterly_burn_usd != null ? String(candidate.quarterly_burn_usd) : "",
  );
  const [segment, setSegment] = useState("");
  const [pct, setPct] = useState("");
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

  // purity (HUMAN) is operator-authored — never pre-filled, never confirmable until entered
  const confirmDisabled =
    ratify.isPending || (candidate.fact_type === "revenue_mix" && (!pct || !segment));

  if (ratify.isSuccess) {
    return <div className="ratify-row done">✓ {label} ratified — the meter re-derives.</div>;
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
              placeholder="you author it"
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
