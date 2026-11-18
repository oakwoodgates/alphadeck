import { useState } from "react";

import { useExtract, useRatifyFact, type ExtractedFact } from "../api/hooks";
import { refusalText } from "../api/refusals";
import { AutoTextarea } from "./AutoTextarea";
import { errText, type OnFileFact, type OnFileMap } from "./format";
import { LocatedPassages, MISSING_FLAGS } from "../components/LocatedPassages";
import { LIMITS } from "../api/limits";

/** The purity panel (V2 W5e) — "Purity (yours to set)". Purity is the user's own judgment of how much of a company's
 *  business is on the thesis: they extract the located filing passage, read the model's proposal (an unverified
 *  estimate, never the value), and CONFIRM or override it; on confirm the fact is written and the meter re-derives.
 *  The extract is an EXPLICIT click (cache-first), never on a render. The LOCATED PASSAGE is the evidence, shown
 *  readable inline (not a tooltip), because reading it IS the decision.
 *
 *  A share count and cash/burn are NOT here: they are the platform's, shared by every user, and read in the Shared
 *  values block (read-only, with a Flag) — no field, no Confirm, for anyone. Since W7a the extract returns only the
 *  purity candidate to a plain user (the steward's still carries all three); this panel shows only `revenue_mix`. */
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
  // happened (then the purity value visibly REVERTING to the original LLM rec; the store is append-only,
  // latest wins on read). Presence = on file; the row seeds its inputs from these values.
  onFile?: OnFileMap;
}) {
  // thesisId (optional) turns on the GROUNDED purity ESTIMATE (SURFACE 1b) for the revenue_mix candidate.
  const extract = useExtract(securityId, thesisId);
  // purity only: the other candidates the endpoint returns are the platform's values (shared), not this panel's
  const facts = (extract.data?.facts ?? []).filter((f) => f.fact_type === "revenue_mix");
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
        Read the passage of the latest SEC filing that shows how much of this company is on the thesis. A model may
        propose a percent; it is never the value. Nothing is saved until you confirm. The share count and cash are
        shared values, shown above.
      </div>
      {extract.error &&
        // the API's fixed refusal (security hardening PR 1b: not on your cards, or today's allowance spent) is an
        // answer, so it reads on its own line as worded; anything else keeps the couldn't-extract note
        (refusalText(extract.error) ? (
          <div className="note err">{refusalText(extract.error)}</div>
        ) : (
          <div className="note err">
            Couldn't extract — {errText(extract.error)}. (The security needs a CIK, and the stack needs
            ALPHADECK_USER_AGENT.)
          </div>
        ))}
      {facts.map((f) => (
        // Key by securityId TOO (not fact_type alone): RatifyRow seeds its editable inputs from `candidate`
        // via useState (once, on mount). The rail stays mounted when the operator clicks name→name (only
        // `securityId` changes), so a fact_type-only key made React REUSE the row and keep the PRIOR name's
        // values. The composite key remounts the row per member, re-seeding from the new candidate.
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
      {/* honest-loudness: an extract that came back EMPTY isn't a silent blank rail. "Nothing on EDGAR" is true
          absence: no filing to read a purity from. */}
      {settled && facts.length === 0 && extract.data?.empty_reason === "no-annual-filing" && (
        <div className="note">
          Nothing on EDGAR the extractor can read — no 10-K/10-Q and no annual foreign filing
          (20-F/40-F) on file for this issuer. Nothing to extract here.
        </div>
      )}
      {/* W7a: a plain user's extract is purity only, and the annual (20-F/40-F) path never reads purity */}
      {settled && facts.length === 0 && extract.data?.empty_reason === "purity-not-covered" && (
        <div className="note">
          Purity is not read automatically for a company that files annual reports on Form 20-F or 40-F. Set it by
          hand from its annual report.
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
  // SURFACE 1b — the GROUNDED purity estimate (llm-proposed, UNVERIFIED): pre-fill the % + the proposed
  // segment (parsed from the note the endpoint wrote) so "confirm as-is" is one action; the operator can
  // override either. Sending the estimate on ratify lets the server stamp `vouched` (confirmed vs overridden).
  // ON FILE SUPPRESSES IT: once the operator ratified, the stale rec must not displace THEIR value (the
  // re-entry reversion bug) — and a re-confirm from file is not a vouch on the old estimate.
  const purityEstimate =
    candidate.estimate_source === "llm_proposed" && !onFile ? candidate.value : null;
  const proposedSegment = (candidate.note ?? "").match(/on-thesis segment:\s*(.+?)\s*\]/)?.[1] ?? "";
  // seed each input from what's ON FILE first (the operator's ratified value — the panel must show their
  // decision, not the stale candidate), then the candidate, then blank
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
  const passages = candidate.located_passages ?? [];

  const onConfirm = () =>
    ratify.mutate({
      security_id: securityId,
      source: candidate.source, // the candidate's BASIS, carried through (not retyped)
      source_ref: candidate.source_ref,
      event_date: candidate.event_date,
      note,
      fact_type: "revenue_mix",
      segment_label: segment,
      mix_pct: Number(pct),
      // the shown estimate (if any) → the server stamps vouched confirmed (unchanged) / overridden (changed)
      estimate: purityEstimate ?? undefined,
    });

  // purity needs a segment + a %: with an estimate both pre-fill (confirm as-is), else the operator authors them
  const confirmDisabled = ratify.isPending || !pct || !segment;

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
        ✓ purity ratified{vouched} — the meter re-derives.
      </div>
    );
  }

  return (
    <div className={`ratify-row ${candidate.tier}`}>
      <div className="ratify-rh">
        <span className="rk">purity</span>
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
          <span className={MISSING_FLAGS.has(fl) ? "rflag missing" : "rflag"} key={fl}>
            {MISSING_FLAGS.has(fl) ? "∅" : "⚠"} {fl}
          </span>
        ))}
      </div>

      {/* the located passage — EVIDENCE, rendered inline (reading it IS the decision). Clamped to a scannable
          window (a raw segment table arrives as a wall of collapsed text); the expand is one click, the filing
          link one more. */}
      <LocatedPassages passages={passages} />

      {purityEstimate != null && (
        <div className="est-tag">
          ✦ estimate {purityEstimate}% · llm-proposed · unverified — confirm as-is, or override
        </div>
      )}
      {/* missing ≠ blank: an empty purity says WHY it's empty. Pre-revenue names have no segment revenue to read;
          a thesis-scoped extract that stayed empty means the grounded estimate declined (fail-open) — different
          authoring starts, named honestly. Quiet on file (the ratified value is showing). */}
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
          maxLength={LIMITS.line}
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
      <label className="rf wide">
        note
        {/* a textarea, not an input — the pre-filled basis notes run long and a single truncated line hid what
            the operator was about to ratify */}
        <AutoTextarea
          ariaLabel="note"
          maxLength={LIMITS.prose}
          className="rf-note"
          value={note}
          placeholder="the composition / basis"
          maxRows={6}
          onChange={setNote}
        />
      </label>

      {ratify.isError && <div className="note err">Couldn't ratify — {errText(ratify.error)}.</div>}
      <button type="button" className="wb-mini" onClick={onConfirm} disabled={confirmDisabled}>
        {ratify.isPending ? "Saving…" : "Confirm"}
      </button>
    </div>
  );
}
