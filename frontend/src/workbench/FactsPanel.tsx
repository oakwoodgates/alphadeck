import { useState } from "react";

import { useExplainFlag, useExtract, useRatifyFact, type ExtractedFact } from "../api/hooks";
import { AutoTextarea } from "./AutoTextarea";
import { errText, type OnFileFact, type OnFileMap } from "./format";

const METER_LABEL: Record<string, string> = {
  revenue_mix: "purity",
  shares_outstanding: "market cap · shares",
  cash_burn: "runway · cash + burn",
};

/** Missing-data flags — "can't compute" is a grey AUTHORING state (the value is None by design; the
 *  operator writes it from the located statements), not a warm alarm. The loud ⚠ is reserved for the
 *  judgment exceptions (one-time, stale, raw-YTD, dual-class) — honest loudness: a flag that alarms
 *  on a mere data gap drowns the flags that mark real composition risk. */
const MISSING_FLAGS = new Set(["no-companyfacts", "no-cashflow-column", "no-cash-instant"]);

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
  // per fact-type: the RATIFIED value(s) already on file (recovered by the parent from the meters'
  // provenance detail). The extract endpoint is deliberately DB-free, so its candidates can't know —
  // without this, re-opening a ratified name re-offered the stale candidate as if the save never
  // happened (the gate-3 "no save?" confusion, then the purity value visibly REVERTING to the original
  // LLM rec; the store is append-only, latest wins on read). Presence = on file; the row seeds its
  // inputs from these values.
  onFile?: OnFileMap;
}) {
  // thesisId (optional) turns on the GROUNDED purity ESTIMATE (SURFACE 1b) for the revenue_mix candidate.
  const extract = useExtract(securityId, thesisId);
  const facts = extract.data?.facts ?? [];
  // an annual-filer name (20-F/40-F): shares (Slice 1) and now cash/runway (Slice A) come through, but
  // PURITY is still not covered — say so, or the "—" meter implies the data doesn't exist (spec §5.3)
  const annualOnly =
    facts.length > 0 &&
    facts.every((f) => f.source === "annual-cover" || f.source === "annual-statements");
  // the RUNWAY leg's own honest state for an annual filer with no cash_burn candidate (Slice A):
  // "cash-generative" (a STATE — no runway applies) · "financials-in-exhibit" (deferred — the
  // statements live in a separate exhibit doc) · "statements-not-located" (unread, not empty)
  const runwayReason = extract.data?.runway_empty_reason ?? null;
  const settled = extract.data !== undefined && !extract.isFetching && !extract.error;
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
        Auto-extract the scoring facts from the latest SEC filings — 10-Q/10-K, or a foreign filer's
        20-F/40-F cover (shares only, always FLAG) — you confirm each (AUTO as-is, FLAG the composition,
        purity you author). An explicit call (cache-first); the operator ratifies, the model never does.
      </div>
      {extract.error && (
        <div className="note err">
          Couldn't extract — {errText(extract.error)}. (The security needs a CIK, and the stack needs
          ALPHADECK_USER_AGENT.)
        </div>
      )}
      {facts.map((f) => (
        // Key by securityId TOO (not fact_type alone): RatifyRow seeds its editable inputs from `candidate`
        // via useState (once, on mount). The rail stays mounted when the operator clicks name→name (only
        // `securityId` changes), so a fact_type-only key made React REUSE the row and keep the PRIOR name's
        // values — every name in a section then showed the first-opened name's shares (a wrong-value ratify
        // risk). The composite key remounts the row per member, re-seeding from the new candidate.
        // On-file state is IN the key too: when a confirm lands and the scored read refreshes, the row
        // remounts into the on-file state showing the just-ratified value — the save visibly took.
        <RatifyRow
          key={`${securityId}:${f.fact_type}:${onFile?.[f.fact_type] ? "onfile" : "new"}`}
          candidate={f}
          securityId={securityId}
          onFile={onFile?.[f.fact_type]}
          estimateAttempted={thesisId != null}
        />
      ))}
      {/* the coverage note for an annual-filer name: PURITY — means NOT COVERED, not zero */}
      {annualOnly && (
        <div className="note">
          Annual-filer coverage is <b>shares + cash/runway</b> — purity isn't extracted from a
          20-F/40-F yet, so that meter stays "—" (not covered), never a judged zero.
        </div>
      )}
      {/* the runway leg's own states (Slice A) — three DISTINCT non-fact outcomes, each named
          honestly (interaction #2): a STATE (cash-generative), a DEFERRAL (exhibit), an UNREAD. */}
      {settled && runwayReason === "cash-generative" && (
        <div className="note">
          <b>Cash-generative</b> — this filer's operating cash flow is positive, so no runway applies
          (and none is computed; a finite number here would be bogus). Shown as a state, not a fact:
          its financial statements aren't in the main filing document, so there is no passage to
          ratify a cash/burn fact against.
        </div>
      )}
      {settled && runwayReason === "financials-in-exhibit" && (
        <div className="note">
          This filer <b>burns cash</b>, but its financial statements live in a separate{" "}
          <b>exhibit document</b> (the 40-F/MJDS shape) — runway needs the exhibit doc, which this
          pass doesn't fetch. <b>Deferred, not judged</b>: the RUNWAY meter stays "—" rather than
          carrying a number without its statement passage.
        </div>
      )}
      {settled && runwayReason === "statements-not-located" && (
        <div className="note">
          The financial statements couldn't be located in this filer's annual document this pass —
          the runway is <b>unread, not empty</b>; it stays a candidate for a future extractor pass.
        </div>
      )}
      {/* honest-loudness: an extract that came back EMPTY isn't a silent blank rail — and the TWO empty
          reasons are DISTINCT (interaction #2): "nothing on EDGAR" is true absence; "cover unread" is a
          retrieval gap that must not masquerade as absence (the name stays a candidate). */}
      {settled && facts.length === 0 && extract.data?.empty_reason === "no-annual-filing" && (
        <div className="note">
          Nothing on EDGAR the extractor can read — no 10-K/10-Q and no annual foreign filing
          (20-F/40-F) on file for this issuer. Nothing to extract or ratify here.
        </div>
      )}
      {settled && facts.length === 0 && extract.data?.empty_reason === "cover-not-located" && (
        <div className="note">
          This issuer's annual filing (<b>20-F/40-F</b>) is on file, but its cover share count couldn't
          be read this pass — the name is <b>unread, not empty</b>. A count without its located cover
          passage is deliberately not offered; author the count from the filing if you need it now.
        </div>
      )}
    </div>
  );
}

function RatifyRow({
  candidate,
  securityId,
  onFile,
  estimateAttempted,
}: {
  candidate: ExtractedFact;
  securityId: string;
  onFile?: OnFileFact;
  // whether the extract was thesis-scoped (the grounded purity seam only RUNS with a thesis) — it decides
  // the honest reason shown on an empty purity: "couldn't ground" vs simply "author from the passage"
  estimateAttempted?: boolean;
}) {
  const ratify = useRatifyFact();
  const explain = useExplainFlag(candidate); // the LLM seam — FLAG only, an aid to the ratify (below)
  const auto = candidate.tier === "auto";
  const isFlag = candidate.tier === "flag";
  // The value was APPLIED by the machine (get-data auto-confirm) and no human vouched for it. This flips the
  // AUTO field from read-only to EDITABLE — the label promises "confirm or override", and an override the
  // operator can't type is not an override (#1 reversibility). Confirming as-is is still meaningful here: it
  // appends an `operator` fact, upgrading a machine-applied count to one a human vouched for.
  const autoApplied = onFile?.ratified_by === "auto";
  // SURFACE 1b — the GROUNDED purity estimate (llm-proposed, UNVERIFIED): pre-fill the % + the proposed
  // segment (parsed from the note the endpoint wrote) so "confirm as-is" is one action; the operator can
  // override either. Sending the estimate on ratify lets the server stamp `vouched` (confirmed vs overridden).
  // ON FILE SUPPRESSES IT: once the operator ratified, the stale rec must not displace THEIR value (the
  // re-entry reversion bug) — and a re-confirm from file is not a vouch on the old estimate.
  const purityEstimate =
    candidate.fact_type === "revenue_mix" && candidate.estimate_source === "llm_proposed" && !onFile
      ? candidate.value
      : null;
  const proposedSegment = (candidate.note ?? "").match(/on-thesis segment:\s*(.+?)\s*\]/)?.[1] ?? "";
  // seed each input from what's ON FILE first (the operator's ratified value — the panel must show their
  // decision, not the stale candidate), then the candidate, then blank
  const [shares, setShares] = useState(
    onFile?.shares != null
      ? String(onFile.shares)
      : candidate.value != null
        ? String(candidate.value)
        : "",
  );
  const [cash, setCash] = useState(
    onFile?.cash_usd != null
      ? String(onFile.cash_usd)
      : candidate.cash_usd != null
        ? String(candidate.cash_usd)
        : "",
  );
  const [burn, setBurn] = useState(
    onFile?.quarterly_burn_usd != null
      ? String(onFile.quarterly_burn_usd)
      : candidate.quarterly_burn_usd != null
        ? String(candidate.quarterly_burn_usd)
        : "",
  );
  const [segment, setSegment] = useState(
    onFile?.segment_label ?? (purityEstimate != null ? proposedSegment : ""),
  );
  const [pct, setPct] = useState(
    onFile?.mix_pct != null
      ? String(onFile.mix_pct)
      : purityEstimate != null
        ? String(purityEstimate)
        : "",
  );
  // on file, the note shows what was RATIFIED (empty if none was) — never the candidate's note over it
  const [note, setNote] = useState(onFile ? (onFile.note ?? "") : (candidate.note ?? ""));
  // the located passage is EVIDENCE and must be readable — but a segment table arrives as a wall of
  // collapsed text, so it renders CLAMPED (a scannable window + fade) with an explicit expand
  const [expanded, setExpanded] = useState(false);
  // AUTO's source is on-demand (feel-of-control): collapsed by default — AUTO doesn't demand reading —
  // one click to see the located passage behind the pre-filled value. FLAG/HUMAN stay inline (reading
  // the passage IS that decision).
  const [srcOpen, setSrcOpen] = useState(false);
  const passages = candidate.located_passages ?? [];
  const showPassages = passages.length > 0 && (!auto || srcOpen);

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
      ratify.mutate({
        ...common,
        fact_type: "shares_outstanding",
        shares: Number(shares),
        // the ADS-ratio derivation metadata rides through from the candidate (spec §10) — like
        // `source`, carried, never retyped: "known" divides the cap, "unread" withholds it, absent
        // (every 10-Q name) computes 1:1. The note above states which; the operator ratifies it all.
        // (The write accepts only the two meaningful stamps — anything else stays off the wire.)
        ads_ratio: candidate.ads_ratio ?? undefined,
        ads_ratio_status:
          candidate.ads_ratio_status === "known" || candidate.ads_ratio_status === "unread"
            ? candidate.ads_ratio_status
            : undefined,
      });
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
        {/* AUTO-APPLIED vs merely ON FILE. We assert "auto-applied" ONLY for `ratified_by === "auto"` — a
            count the machine applied that no human vouched for, so the operator knows to sanity-check it
            against the market cap. Every other fact keeps the neutral "✓ on file", which is true of all of
            them. Deliberately NOT "operator confirmed": ~108 legacy rows carry ratified_by="operator" from
            the OLD ceremonial AUTO confirm, so that label would claim a check that never happened. */}
        {onFile &&
          (onFile.ratified_by === "auto" ? (
            <span
              className="ronfile auto"
              title="applied automatically from the filing's cover count (single-class, current) — nobody verified this number. It scores like any ratified fact; sanity-check it against the market cap and override here if it looks wrong."
            >
              ✦ auto-applied — confirm or override
            </span>
          ) : (
            <span
              className="ronfile"
              title="a ratified value for this fact is already on file (see Behind the scores) — confirming appends a NEW version; latest wins on read"
            >
              ✓ on file
            </span>
          ))}
        {(candidate.flags ?? []).map((fl) => (
          <span className={MISSING_FLAGS.has(fl) ? "rflag missing" : "rflag"} key={fl}>
            {MISSING_FLAGS.has(fl) ? "∅" : "⚠"} {fl}
          </span>
        ))}
      </div>

      {/* the located passage — EVIDENCE. FLAG/HUMAN render it inline (reading it IS the decision);
          AUTO renders it BEHIND the toggle above (collapsed by default — the pre-filled value doesn't
          demand reading, but its source is one click away, so approving it reads as the operator's
          decision, never a black box). Clamped to a scannable window (a raw segment table arrives as
          a wall of collapsed text); the expand is one click, the filing link one more. */}
      {auto && passages.length > 0 && (
        <button type="button" className="wb-mini ghost" onClick={() => setSrcOpen((v) => !v)}>
          {srcOpen
            ? "− hide the source"
            : `▸ show the source (${[...new Set(passages.map((p) => p.kind))].join(" · ")})`}
        </button>
      )}
      {showPassages &&
        passages.map((p, i) => (
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
      {showPassages && passages.some((p) => p.excerpt.length > 420) && (
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
            readOnly={auto && !autoApplied}
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
          {/* missing ≠ blank: an empty purity says WHY it's empty (the market-cap "needs price" rule,
              applied here). Pre-revenue names have no segment revenue to read; a thesis-scoped extract
              that stayed empty means the grounded estimate declined (fail-open) — different authoring
              starts, named honestly. Quiet on file (the ratified value is showing). */}
          {purityEstimate == null && !onFile && (
            <div className="rf-reason">
              {candidate.source === "10-k-business-description"
                ? "no revenue data on file — pre-revenue: author the % from the business description"
                : estimateAttempted
                  ? "couldn't ground a purity estimate in the passage — author the % from it"
                  : "author the % from the segment passage"}
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
