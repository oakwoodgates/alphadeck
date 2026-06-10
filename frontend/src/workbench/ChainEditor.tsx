import { useState } from "react";

import type { ThesisDetail } from "../api/hooks";
import { usePromoteThesis } from "../api/hooks";
import { AddName } from "./AddName";
import { archLabel, errText } from "./format";
import { memberKey, useChainDraft } from "./useChainDraft";

interface Props {
  thesis: ThesisDetail;
  onDone: () => void; // exit edit mode (the parent unmounts this, re-snapshotting on the next edit)
}

/** The authoring surface (Slice 4b): build & edit the value chain — add / rename / reorder / remove
 *  links, place / move / add / remove names — then SAVE the whole draft through the existing full-replace
 *  `POST /workbench/theses`. On success the parent returns to the view, where the scored read re-derives
 *  (the meters group on the new structure). Authorship is operator-authored here (`operator_set`); the
 *  same surface is where S5's drafter will later show `system_drafted` placements to accept / edit. */
export function ChainEditor({ thesis, onDone }: Props) {
  const d = useChainDraft(thesis);
  const save = usePromoteThesis();
  const [newSeg, setNewSeg] = useState("");

  const segLabels = d.draft.segments.map((s) => s.label);
  const keys = new Set(d.draft.basket.map(memberKey));

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
        <div className="toast show err">Couldn't save — {errText(save.error)}. Nothing changed.</div>
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
        {d.draft.basket.map((m) => (
          <div className="wb-mem-row" key={memberKey(m)}>
            <span className="tk">{m.ticker}</span>
            <span className={`arch ${m.archetype}`}>{archLabel(m.archetype)}</span>
            <select
              className="wb-input"
              value={m.segment ?? ""}
              aria-label={`place ${m.ticker}`}
              onChange={(e) => d.placeMember(memberKey(m), e.target.value || null)}
            >
              <option value="">— unplaced —</option>
              {segLabels.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
            {/* the authorship seam — S5's drafter will later show "drafted · accept / edit" here */}
            <span className="wb-author">{m.authored_by === "operator_set" ? "operator" : m.authored_by}</span>
            <button
              type="button"
              className="wb-mini ghost"
              aria-label={`remove ${m.ticker}`}
              onClick={() => d.removeMember(memberKey(m))}
            >
              ×
            </button>
          </div>
        ))}
        {d.draft.basket.length === 0 && <div className="note">No names yet — add one below.</div>}
      </div>

      <AddName existingKeys={keys} onAdd={d.addMember} />
    </div>
  );
}
