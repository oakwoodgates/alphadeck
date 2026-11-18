import { useState } from "react";

import {
  useCatalystCounting,
  type BasketMember,
  type CatalystCountingIn,
  type CatalystCountingOut,
} from "../api/hooks";
import { fmtDate } from "../util/format";
import { errText } from "../workbench/format";
import { isSilentLink } from "../workbench/useChainDraft";

/** V2 W6d — make a SAVED calendar row COUNT toward the card's call (docs/CALL_LOGIC.md §3c).
 *
 *  A quiet "Counts toward the call…" opens a small form (grade, the card's voting names, a citation, an optional
 *  "relevant until"); once it counts the row shows ONE line of state with Edit and Stop counting (the inverse: one
 *  click, the row is an ordinary calendar item again and its form re-opens filled with the last statement). The
 *  client checks only mirror the obvious (a grade, a citation); the server is the authority and every refusal is
 *  shown as it came, on the row that made the call. Everything a person typed renders as a text node. */

type Grade = "core" | "flip";

const CORE_LABEL = "Core: binding (a signed contract, a license, a loan guarantee)";
const FLIP_LABEL = "Flip: provisional (an MOU, a selection, attention)";

const sentence = (e: unknown) => errText(e);

function CountingForm({
  thesisId,
  entryId,
  label,
  counting,
  members,
  onClose,
}: {
  thesisId: string;
  entryId: string;
  label: string;
  counting: CatalystCountingOut | undefined;
  members: BasketMember[];
  onClose: () => void;
}) {
  const save = useCatalystCounting(thesisId);
  const [picked, setPicked] = useState<string[]>(() => counting?.members.map((m) => m.security_id) ?? []);
  const [grade, setGrade] = useState<Grade | "">(counting?.grade ?? "");
  const [citation, setCitation] = useState(counting?.citation ?? "");
  const [until, setUntil] = useState(counting?.relevant_until ?? "");
  const [on, setOn] = useState(true);
  const [reset, setReset] = useState(false);
  const [clientError, setClientError] = useState<string | null>(null);

  // the card's names: voting ones are pickable; a name in a silent link has no vote (shown, disabled, with the note).
  const roster = members.filter((m) => m.ticker);
  const known = new Set(roster.map((m) => m.security_id));
  const extra = (counting?.members ?? []).filter((m) => !known.has(m.security_id)); // a stored name no longer on the card
  const voting = (m: BasketMember) => Boolean(m.security_id) && !isSilentLink(m.segment);

  const toggle = (sid: string) => {
    const next = picked.includes(sid) ? picked.filter((x) => x !== sid) : [...picked, sid];
    setPicked(next);
    setClientError(null);
    if (next.length === 0 && grade === "core") {
      setGrade("flip");
      setReset(true);
    } else {
      setReset(false);
    }
  };

  const submit = () => {
    if (on) {
      if (!grade) return setClientError("Pick a grade.");
      if (!citation.trim())
        return setClientError("Add a citation: a link (https://…) or an SEC accession number.");
    }
    setClientError(null);
    const body: CatalystCountingIn = {
      counts: on,
      grade: grade || null,
      security_ids: picked,
      citation: citation.trim() || null,
      relevant_until: until || null,
    };
    save.mutate({ entryId, body }, { onSuccess: onClose });
  };

  return (
    <div className="cc-form" role="group" aria-label="Count this toward the call">
      <div className="cc-title">Count this toward the call</div>
      <div className="cc-for">{label}</div>

      <fieldset className="cc-field">
        <legend>Names</legend>
        <div className="cc-hint">Leave empty to count it for the whole card (the theme conviction).</div>
        <div className="cc-names">
          {roster.map((m) => (
            <span className="cc-name" key={`${m.ticker}-${m.security_id}`}>
              <label>
                <input
                  type="checkbox"
                  disabled={!voting(m)}
                  checked={voting(m) && picked.includes(m.security_id as string)}
                  onChange={() => toggle(m.security_id as string)}
                />
                {m.ticker}
              </label>
              {m.security_id && isSilentLink(m.segment) && (
                <span className="cc-hint">{`${m.ticker} is in ${m.segment}, so it has no vote; this entry does nothing for it.`}</span>
              )}
            </span>
          ))}
          {extra.map((m) => (
            <span className="cc-name" key={m.security_id}>
              <label>
                <input type="checkbox" checked={picked.includes(m.security_id)} onChange={() => toggle(m.security_id)} />
                {m.ticker ?? m.security_id}
              </label>
            </span>
          ))}
        </div>
      </fieldset>

      <fieldset className="cc-field">
        <legend>Grade</legend>
        {picked.length > 0 && (
          <label>
            <input type="radio" name={`cc-grade-${entryId}`} checked={grade === "core"} onChange={() => setGrade("core")} />
            {CORE_LABEL}
          </label>
        )}
        <label>
          <input type="radio" name={`cc-grade-${entryId}`} checked={grade === "flip"} onChange={() => setGrade("flip")} />
          {FLIP_LABEL}
        </label>
        {picked.length === 0 && <div className="cc-hint">A whole-card entry counts as Flip.</div>}
        {reset && (
          <div className="cc-hint">No company is picked, so this is a whole-card entry: it counts as Flip.</div>
        )}
      </fieldset>

      <label className="cc-field">
        <span>Citation</span>
        <input
          value={citation}
          placeholder="A link (https://…) or an SEC accession number"
          onChange={(e) => setCitation(e.target.value)}
        />
      </label>

      <label className="cc-field">
        <span>Relevant until (optional)</span>
        <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
      </label>
      <div className="cc-hint">Leave empty and it stays live for 365 days.</div>

      <label className="cc-field">
        <span>
          <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />
          Counts toward the call
        </span>
      </label>
      <div className="cc-hint">
        It counts from tonight, not from the date on the calendar. Earlier nights stay as they were.
      </div>

      <div className="sle-actions">
        <button type="button" className="wb-mini" disabled={save.isPending} onClick={submit}>
          Save
        </button>
        <button type="button" className="wb-mini ghost" onClick={onClose}>
          Cancel
        </button>
      </div>
      {clientError && <div className="note err">{clientError}</div>}
      {save.isError && <div className="note err">{sentence(save.error)}</div>}
    </div>
  );
}

function StopCounting({
  thesisId,
  counting,
}: {
  thesisId: string;
  counting: CatalystCountingOut;
}) {
  const stop = useCatalystCounting(thesisId);
  return (
    <>
      <button
        type="button"
        className="wb-mini ghost"
        title="It stops counting from tonight. Earlier nights stay as they were."
        disabled={stop.isPending}
        onClick={() =>
          stop.mutate({
            entryId: counting.entry_id,
            body: {
              counts: false,
              grade: counting.grade,
              security_ids: counting.members.map((m) => m.security_id),
              citation: counting.citation,
              relevant_until: counting.relevant_until ?? null,
            },
          })
        }
      >
        Stop counting
      </button>
      {stop.isError && <div className="note err">{sentence(stop.error)}</div>}
    </>
  );
}

export function CatalystCountingRow({
  thesisId,
  entryId,
  label,
  counting,
  members,
  metaIndex,
}: {
  thesisId: string;
  entryId: string;
  label: string;
  /** this row's entry from `ThesisDetail.catalyst_counting` (matched by entry id); undefined = never counted */
  counting: CatalystCountingOut | undefined;
  members: BasketMember[];
  metaIndex: boolean;
}) {
  const [open, setOpen] = useState(false);
  const counts = Boolean(counting?.counts);

  if (open) {
    return (
      <CountingForm
        thesisId={thesisId}
        entryId={entryId}
        label={label}
        counting={counting}
        members={members}
        onClose={() => setOpen(false)}
      />
    );
  }
  if (counts && counting) {
    const who = counting.members.length
      ? counting.members.map((m) => m.ticker ?? m.security_id).join(", ")
      : "whole card";
    return (
      <div className="cc-line">
        <span className="cc-state">{`Counts · ${counting.grade === "core" ? "Core" : "Flip"} · ${who} · since ${fmtDate(counting.counts_from)}`}</span>
        <button type="button" className="wb-mini ghost" onClick={() => setOpen(true)}>
          Edit
        </button>
        <StopCounting thesisId={thesisId} counting={counting} />
      </div>
    );
  }
  if (metaIndex) {
    return <div className="cc-line cc-hint">This card is a meta index: nothing on it counts toward the call.</div>;
  }
  return (
    <div className="cc-line">
      <button type="button" className="wb-mini ghost" onClick={() => setOpen(true)}>
        Counts toward the call…
      </button>
    </div>
  );
}
