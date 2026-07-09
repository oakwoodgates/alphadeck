import { useState } from "react";

import { useExplainFlag, useExtract, useRatifyFact, type ExtractedFact } from "../api/hooks";
import { AutoTextarea } from "./AutoTextarea";
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
  onFile,
}: {
  securityId: string;
  thesisId?: string;
  // per fact-type: a RATIFIED value already exists on file (derived by the parent from the meters'
  // provenance). The extract endpoint is deliberately DB-free, so its candidates can't know — without
  // this tag, re-opening a ratified name re-offered the same candidate as if the save never happened
  // (the gate-3 "no save?" confusion; the store is append-only, latest wins on read).
  onFile?: Partial<Record<string, boolean>>;
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
        // Key by securityId TOO (not fact_type alone): RatifyRow seeds its editable inputs from `candidate`
        // via useState (once, on mount). The rail stays mounted when the operator clicks name→name (only
        // `securityId` changes), so a fact_type-only key made React REUSE the row and keep the PRIOR name's
        // values — every name in a section then showed the first-opened name's shares (a wrong-value ratify
        // risk). The composite key remounts the row per member, re-seeding from the new candidate.
        <RatifyRow
          key={`${securityId}:${f.fact_type}`}
          candidate={f}
          securityId={securityId}
          onFile={onFile?.[f.fact_type] ?? false}
        />
      ))}
      {/* honest-loudness (the SIMO case): an extract that came back EMPTY isn't a silent blank rail — the
          issuer has no 10-K/10-Q the extractor covers (a foreign 20-F/6-K filer). Name it, don't hide it. */}
      {extract.data !== undefined &&
        extract.data.length === 0 &&
        !extract.isFetching &&
        !extract.error && (
          <div className="note">
            No 10-K/10-Q on file for this name — foreign issuers file <b>20-F / 6-K</b>, which the extractor
            doesn't cover yet. Nothing to extract or ratify here.
          </div>
        )}
    </div>
  );
}

function RatifyRow({
  candidate,
  securityId,
  onFile,
}: {
  candidate: ExtractedFact;
  securityId: string;
  onFile?: boolean;
}) {
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
  // the located passage is EVIDENCE and must be readable — but a segment table arrives as a wall of
  // collapsed text, so it renders CLAMPED (a scannable window + fade) with an explicit expand
  const [expanded, setExpanded] = useState(false);

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

  // purity needs a segment + a %: with an estimate both pre-fill (confirm as-is), else the operator
  // authors them. The OTHER types gate on their fields too — `Number("")` is 0, so an empty field must
  // never confirm (the None-valued candidates — dual-class without a cover sum, no-companyfacts,
  // no-cashflow-column — leave fields blank BY DESIGN; a blank confirm would ratify a fake zero).
  const confirmDisabled =
    ratify.isPending ||
    (candidate.fact_type === "revenue_mix" && (!pct || !segment)) ||
    (candidate.fact_type === "shares_outstanding" && !shares) ||
    (candidate.fact_type === "cash_burn" && (!cash || !burn));

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
        {/* a ratified value already exists — confirming APPENDS a new version (latest wins on read);
            without this tag a re-open read as "the first save never happened" */}
        {onFile && (
          <span
            className="ronfile"
            title="a ratified value for this fact is already on file (see Behind the scores) — confirming appends a NEW version; latest wins on read"
          >
            ✓ on file
          </span>
        )}
        {(candidate.flags ?? []).map((fl) => (
          <span className="rflag" key={fl}>
            ⚠ {fl}
          </span>
        ))}
      </div>

      {/* the located passage — EVIDENCE, readable inline (reading it IS the FLAG decision). Clamped to a
          scannable window (a raw segment table arrives as a wall of collapsed text); the expand is one
          click, the filing link one more. */}
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
          {/* clamp only what's actually long — the fade must never eat a short passage */}
          <div className={`excerpt${!expanded && p.excerpt.length > 420 ? " clamped" : ""}`}>
            {p.excerpt}
          </div>
        </div>
      ))}
      {(candidate.located_passages ?? []).some((p) => p.excerpt.length > 420) && (
        <button type="button" className="wb-mini ghost" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "− collapse the passage" : "+ show the full passage"}
        </button>
      )}

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
          {/* a textarea, not an input — the pre-filled composition/basis notes run long and a
              single truncated line hid what the operator was about to ratify */}
          <AutoTextarea
            ariaLabel="note"
            className="rf-note"
            value={note}
            placeholder="the composition / basis"
            maxRows={6}
            onChange={setNote}
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
