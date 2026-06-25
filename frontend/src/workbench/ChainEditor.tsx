import { useState } from "react";

import type {
  BasketMember,
  ResolvedPlacement,
  SecurityCandidate,
  ThesisDetail,
} from "../api/hooks";
import { useDraftChain, useProduceTerms, usePromoteThesis } from "../api/hooks";
import { ErrorToast } from "../components/ErrorToast";
import { AddName } from "./AddName";
import { ARCHETYPES, archLabel, errText } from "./format";
import { memberKey, useChainDraft } from "./useChainDraft";

interface Props {
  thesis: ThesisDetail;
  onDone: () => void; // exit edit mode (the parent unmounts this, re-snapshotting on the next edit)
}

// The authorship seam, in words: who placed each name. Quiet provenance (inverse loudness), never loud.
const authorLabel = (a: string): string =>
  a === "operator_set" ? "operator" : a === "system_drafted" ? "drafted" : "edited";

// A term's provenance: an operator seed vs an LLM-proposed (guard-tiered) term. The data already carries it.
const termAuthor = (a: string): string =>
  a === "operator_set" ? "seed" : a === "operator_edited" ? "edited" : "auto";

/** The authoring surface (Slice 4b + the S5 draft/ratify, 5c): build & edit the value chain by hand — or
 *  DRAFT it from the narrative (the narrative→chain drafter) and ratify per name. A drafted placement loads
 *  as `system_drafted` (badged, prunable); accepting it → `operator_set`, editing any field → `operator_edited`.
 *  A name the drafter couldn't resolve uniquely (AMBIGUOUS) enters the basket ONLY by an explicit operator
 *  pick (ticker + CIK disambiguate); one with no master row (ABSENT) is shown, never placed. A drafted name
 *  is UNSCORED until the operator extract→ratifies it. Nothing persists until SAVE (the full-replace promote,
 *  which honors each member's authorship and stores the thesis-fit prose). */
export function ChainEditor({ thesis, onDone }: Props) {
  const d = useChainDraft(thesis);
  const save = usePromoteThesis();
  const draftQ = useDraftChain(thesis.id);
  const produceTerms = useProduceTerms(thesis.id);
  // The stored term set the draft reads — the freshly-produced one if just regenerated, else what loaded.
  const termSet = produceTerms.data?.term_set ?? thesis.term_set;
  const signalTerms = termSet.filter((e) => e.tier === "signal");
  const broadTerms = termSet.filter((e) => e.tier === "broad");
  const [newSeg, setNewSeg] = useState("");
  const [ambiguous, setAmbiguous] = useState<ResolvedPlacement[]>([]);
  const [verify, setVerify] = useState<ResolvedPlacement[]>([]);
  const [absent, setAbsent] = useState<ResolvedPlacement[]>([]);
  const [draftEmpty, setDraftEmpty] = useState(false);

  const segLabels = d.draft.segments.map((s) => s.label);
  const keys = new Set(d.draft.basket.map(memberKey));

  // Draft the chain from the narrative — an EXPLICIT operator action (never on render). Fail-open: an empty
  // draft (no key / the model declined) loads nothing and the editor is unchanged. MERGE, not replace.
  const onDraft = async () => {
    const { data } = await draftQ.refetch();
    if (!data) return;
    d.loadDraft(data);
    setAmbiguous(data.placements.filter((p) => p.status === "ambiguous"));
    setVerify(data.placements.filter((p) => p.status === "verify"));
    setAbsent(data.placements.filter((p) => p.status === "absent"));
    setDraftEmpty(data.placements.length === 0 && data.segments.length === 0);
  };

  // An AMBIGUOUS name enters the basket ONLY here, by an explicit pick — the operator commits the exact
  // security_id (the membership decision, INVARIANT #2). It lands `system_drafted` (the prose is still
  // drafted) for the operator to accept / edit, like any drafted placement.
  const pickAmbiguous = (p: ResolvedPlacement, c: SecurityCandidate) => {
    d.addMember({
      ticker: c.ticker,
      role: "—",
      archetype: "high_beta",
      security_id: c.security_id,
      segment: p.segment,
      thesis_fit: p.prose || null,
      authored_by: "system_drafted",
    });
    setAmbiguous((prev) => prev.filter((x) => x !== p));
  };

  // A VERIFY name is already RESOLVED (in your universe by exact CIK) but matched on a single broad keyword,
  // so the deterministic discovery surfaces it LOWER-confidence and never auto-places it (the same discipline
  // as AMBIGUOUS — a single match is never auto-membership, INVARIANT #2). One explicit "add" commits its known
  // security_id; it lands `system_drafted` (still unscored) for the operator to accept / edit / drop.
  const addVerify = (p: ResolvedPlacement) => {
    if (!p.security_id) return;
    d.addMember({
      ticker: p.ticker || p.name,
      role: "—",
      archetype: "high_beta",
      security_id: p.security_id,
      segment: p.segment,
      thesis_fit: p.prose || null,
      authored_by: "system_drafted",
    });
    setVerify((prev) => prev.filter((x) => x !== p));
  };

  const onSave = () =>
    save.mutate(
      {
        id: thesis.id,
        name: thesis.name,
        narrative: thesis.narrative,
        ticker: thesis.ticker ?? null,
        basket: d.draft.basket,
        segments: d.draft.segments,
      },
      { onSuccess: () => onDone() },
    );

  return (
    <div className="wb-editor">
      <div className="wb-editor-head">
        <div className="sect-h">
          Build the value chain <em>— decompose the basket into links</em>
        </div>
        <div className="wb-editor-actions">
          {d.dirty && <span className="wb-dirty">unsaved</span>}
          <button type="button" className="promote" disabled={save.isPending} onClick={onSave}>
            {save.isPending ? "Saving…" : "Save chain"}
          </button>
          <button type="button" className="wb-mini ghost" onClick={onDone}>
            {d.dirty ? "Discard" : "Done"}
          </button>
        </div>
      </div>
      {save.isError && (
        <ErrorToast>Couldn't save — {errText(save.error)}. Nothing changed.</ErrorToast>
      )}

      <div className="wb-terms">
        <div className="wb-draft-gap">
          <button
            type="button"
            className="wb-edit-btn"
            onClick={() => produceTerms.mutate()}
            disabled={produceTerms.isPending}
          >
            {produceTerms.isPending
              ? "Producing…"
              : termSet.length > 0
                ? "↻ Regenerate term set"
                : "⚙ Produce term set"}
          </button>
          <span className="note">
            Produce the discovery term set the draft reads — keyword-gen proposes, a deterministic guard
            tiers, and your seeds are the only <b>SIGNAL</b>. Regenerate preserves your seeds and re-rolls the
            proposed <b>BROAD</b> terms. Inspect the split, then Draft.
          </span>
        </div>
        {produceTerms.isError && (
          <ErrorToast>Couldn't produce terms — {errText(produceTerms.error)}.</ErrorToast>
        )}
        {termSet.length > 0 ? (
          <div className="wb-terms-split">
            <div className="wb-terms-tier">
              <div className="wb-terms-tier-h">
                SIGNAL <small>· seeds — a hit PLACES</small>
              </div>
              <ul>
                {signalTerms.map((e, i) => (
                  <li key={i}>
                    <b>{e.term}</b>
                    <span className="wb-author">{termAuthor(e.authored_by)}</span>
                  </li>
                ))}
                {signalTerms.length === 0 && (
                  <li className="muted">none — seed canonical compounds to place names</li>
                )}
              </ul>
            </div>
            <div className="wb-terms-tier">
              <div className="wb-terms-tier-h">
                BROAD <small>· corroboration — VERIFY only</small>
              </div>
              <ul>
                {broadTerms.map((e, i) => (
                  <li key={i}>
                    <b>{e.term}</b>
                    <span className="wb-author">{termAuthor(e.authored_by)}</span>
                  </li>
                ))}
                {broadTerms.length === 0 && <li className="muted">none</li>}
              </ul>
            </div>
          </div>
        ) : (
          !produceTerms.isPending && (
            <div className="note">
              No term set yet — produce one before drafting (a draft without it returns “produce terms
              first”).
            </div>
          )
        )}
      </div>

      <div className="wb-draft-gap">
        <button
          type="button"
          className="wb-edit-btn"
          onClick={onDraft}
          disabled={draftQ.isFetching}
        >
          {draftQ.isFetching ? "Drafting…" : "✦ Draft from narrative"}
        </button>
        <span className="note">
          Pre-fill the chain from your narrative — the drafter proposes the links, the names in each, and
          thesis-fit prose; you accept / edit / drop each. Names resolve against the master (exact membership
          decides); a placed name is <b>unscored</b> until you extract → ratify it. Nothing is sent until Save.
        </span>
      </div>
      {draftQ.isError && (
        <ErrorToast>Couldn't draft — {errText(draftQ.error)}.</ErrorToast>
      )}
      {draftEmpty && (
        <div className="note">
          The drafter returned nothing — no <code>ANTHROPIC_API_KEY</code> in the stack, or the model
          declined. Hand-authoring below is unaffected.
        </div>
      )}

      <div className="wb-seg-edit">
        {d.draft.segments.map((s, i) => (
          <div className="wb-seg-chip" key={i}>
            <input
              className="wb-input"
              value={s.label}
              aria-label={`link ${i + 1} label`}
              onChange={(e) => d.renameSegment(s.label, e.target.value)}
            />
            <button
              type="button"
              className="wb-mini"
              disabled={i === 0}
              aria-label={`move ${s.label} earlier`}
              onClick={() => d.moveSegment(s.label, -1)}
            >
              ←
            </button>
            <button
              type="button"
              className="wb-mini"
              disabled={i === d.draft.segments.length - 1}
              aria-label={`move ${s.label} later`}
              onClick={() => d.moveSegment(s.label, 1)}
            >
              →
            </button>
            <button
              type="button"
              className="wb-mini ghost"
              aria-label={`remove ${s.label}`}
              onClick={() => d.removeSegment(s.label)}
            >
              ×
            </button>
          </div>
        ))}
        <div className="wb-seg-add">
          <input
            className="wb-input"
            placeholder="add a link…"
            aria-label="new link label"
            value={newSeg}
            onChange={(e) => setNewSeg(e.target.value)}
          />
          <button
            type="button"
            className="wb-mini"
            onClick={() => {
              d.addSegment(newSeg);
              setNewSeg("");
            }}
          >
            + link
          </button>
        </div>
      </div>

      <div className="wb-mem-edit">
        {d.draft.basket.map((m) => {
          const k = memberKey(m);
          const drafted = m.authored_by === "system_drafted";
          return (
            <div className={`wb-mem${drafted ? " is-drafted" : ""}`} key={k}>
              <div className="wb-mem-row">
                <span className="tk">{m.ticker}</span>
                <select
                  className="wb-input wb-arch"
                  value={m.archetype}
                  aria-label={`archetype for ${m.ticker}`}
                  onChange={(e) =>
                    d.editArchetype(k, e.target.value as BasketMember["archetype"])
                  }
                >
                  {ARCHETYPES.map((a) => (
                    <option key={a} value={a}>
                      {archLabel(a)}
                    </option>
                  ))}
                </select>
                <select
                  className="wb-input"
                  value={m.segment ?? ""}
                  aria-label={`place ${m.ticker}`}
                  onChange={(e) => d.placeMember(k, e.target.value || null)}
                >
                  <option value="">— unplaced —</option>
                  {segLabels.map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
                <span className="wb-author">{authorLabel(m.authored_by)}</span>
                {drafted && (
                  <button
                    type="button"
                    className="wb-mini"
                    aria-label={`accept ${m.ticker}`}
                    onClick={() => d.acceptMember(k)}
                  >
                    ✓ accept
                  </button>
                )}
                <button
                  type="button"
                  className="wb-mini ghost"
                  aria-label={`remove ${m.ticker}`}
                  onClick={() => d.removeMember(k)}
                >
                  ×
                </button>
              </div>
              <textarea
                className="wb-prose"
                rows={2}
                aria-label={`thesis-fit for ${m.ticker}`}
                placeholder="why this name sits in its link — thesis-fit reasoning (drafted, or yours)…"
                value={m.thesis_fit ?? ""}
                onChange={(e) => d.editProse(k, e.target.value)}
              />
            </div>
          );
        })}
        {d.draft.basket.length === 0 && (
          <div className="note">No names yet — draft from the narrative, or add one below.</div>
        )}
      </div>

      {ambiguous.length > 0 && (
        <div className="wb-suggest">
          <div className="note">
            Ambiguous — the drafter found several matches; <b>pick the exact security</b> (ticker + CIK
            disambiguate a homonym). Nothing is placed until you pick.
          </div>
          {ambiguous.map((p, i) => (
            <div className="wb-suggest-row" key={i}>
              <div className="wb-suggest-h">
                <b>{p.name}</b>
                {p.segment ? <small>{p.segment}</small> : null}
              </div>
              {p.prose ? <div className="drafted muted">{p.prose}</div> : null}
              <ul className="wb-matches">
                {p.candidates.map((c) => {
                  const inBasket = keys.has(c.security_id);
                  return (
                    <li key={c.security_id}>
                      <button
                        type="button"
                        disabled={inBasket}
                        onClick={() => pickAmbiguous(p, c)}
                      >
                        <b>{c.ticker}</b>
                        {c.cik ? <span className="cik">CIK {c.cik}</span> : null}
                        {c.name ? <span className="co">{c.name}</span> : null}
                        {inBasket ? <span className="muted"> · in basket</span> : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}

      {verify.length > 0 && (
        <div className="wb-verify">
          <div className="note">
            Verify — in your universe, but matched on a single broad keyword (lower confidence). Each is
            resolved by CIK; <b>add</b> the ones that fit. An added name is drafted (still unscored).
          </div>
          {verify.map((p, i) => {
            const inBasket = p.security_id ? keys.has(p.security_id) : false;
            return (
              <div className="wb-verify-row" key={i}>
                <div className="wb-suggest-h">
                  <b>{p.name}</b>
                  {p.ticker ? <span className="co">{p.ticker}</span> : null}
                  {p.segment ? <small>{p.segment}</small> : null}
                </div>
                {p.prose ? <div className="drafted muted">{p.prose}</div> : null}
                <button
                  type="button"
                  className="wb-mini"
                  disabled={inBasket || !p.security_id}
                  aria-label={`add ${p.ticker || p.name}`}
                  onClick={() => addVerify(p)}
                >
                  {inBasket ? "· in basket" : "+ add"}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {absent.length > 0 && (
        <div className="wb-absent">
          <div className="note">
            Suggested, not in your universe — shown, never placed (ingest the name to make it pickable).
          </div>
          {absent.map((p, i) => (
            <div className="wb-absent-row" key={i}>
              <b>{p.name}</b>
              {p.ticker ? <span className="co">{p.ticker}?</span> : null}
              {p.prose ? <span className="drafted muted">{p.prose}</span> : null}
            </div>
          ))}
        </div>
      )}

      <AddName existingKeys={keys} onAdd={d.addMember} />
    </div>
  );
}
