import { useState } from "react";

import {
  usePutCatalysts,
  usePutKillCriteria,
  type CatalystOut,
  type KillCriterionOut,
} from "../api/hooks";
import { errText } from "../workbench/format";

/** Spine-list authoring (slice A2) — the Cockpit's catalyst calendar + kill criteria become editable.
 *
 *  These are the thesis-level SURFACE events (display objects the card's calendar + counter-case
 *  consume) and the documented "what would kill this" — operator-owned lists behind their sole-writer
 *  endpoints (a promote can never wipe them; the structural guard is server-side). The editors are
 *  QUIET (a ghost "✎ edit" on a section the Cockpit already renders) and exist even at zero — an
 *  unauthored thesis needs the entry point, since empty sections used to not render at all. The
 *  per-name conviction FACTS (the arming path) are authored on the Workbench rail, not here.
 */

type CatDraft = { label: string; kind: string; when_date: string; when_label: string };
const blankCat: CatDraft = { label: "", kind: "", when_date: "", when_label: "" };

export function CatalystEditor({
  thesisId,
  catalysts,
}: {
  thesisId: string;
  catalysts: CatalystOut[];
}) {
  const put = usePutCatalysts(thesisId);
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<CatDraft[]>([]);

  const open = () => {
    setRows(
      catalysts.length
        ? catalysts.map((c) => ({
            label: c.label,
            kind: c.kind ?? "",
            when_date: c.when_date ?? "",
            when_label: c.when_label ?? "",
          }))
        : [{ ...blankCat }],
    );
    setEditing(true);
  };

  const save = () =>
    put.mutate(
      rows
        .filter((r) => r.label.trim())
        .map((r) => ({
          label: r.label.trim(),
          kind: r.kind.trim() || null,
          when_date: r.when_date || null,
          when_label: r.when_label.trim() || null,
        })),
      { onSuccess: () => setEditing(false) },
    );

  if (!editing) {
    return (
      <button type="button" className="wb-mini ghost" onClick={open}>
        {catalysts.length ? "✎ edit the calendar" : "+ add catalysts (the events you're watching)"}
      </button>
    );
  }
  return (
    <div className="sle">
      {rows.map((r, i) => (
        <div className="sle-row" key={i}>
          <input
            aria-label={`catalyst label ${i + 1}`}
            placeholder="e.g. MU FQ4 earnings"
            value={r.label}
            onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))}
          />
          <input
            aria-label={`catalyst kind ${i + 1}`}
            className="sle-kind"
            placeholder="kind"
            value={r.kind}
            onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, kind: e.target.value } : x)))}
          />
          <input
            type="date"
            aria-label={`catalyst date ${i + 1}`}
            value={r.when_date}
            onChange={(e) =>
              setRows(rows.map((x, j) => (j === i ? { ...x, when_date: e.target.value } : x)))
            }
          />
          <input
            aria-label={`catalyst when label ${i + 1}`}
            className="sle-kind"
            placeholder='or "~Q3"'
            value={r.when_label}
            onChange={(e) =>
              setRows(rows.map((x, j) => (j === i ? { ...x, when_label: e.target.value } : x)))
            }
          />
          <button
            type="button"
            className="wb-mini ghost"
            aria-label={`remove catalyst ${i + 1}`}
            onClick={() => setRows(rows.filter((_, j) => j !== i))}
          >
            ✕
          </button>
        </div>
      ))}
      <div className="sle-actions">
        <button type="button" className="wb-mini ghost" onClick={() => setRows([...rows, { ...blankCat }])}>
          + row
        </button>
        <button type="button" className="wb-mini" disabled={put.isPending} onClick={save}>
          {put.isPending ? "Saving…" : "Save calendar"}
        </button>
        <button type="button" className="wb-mini ghost" onClick={() => setEditing(false)}>
          cancel
        </button>
      </div>
      {put.isError && <div className="note err">Couldn't save — {errText(put.error)}.</div>}
    </div>
  );
}

export function KillCriteriaEditor({
  thesisId,
  kills,
}: {
  thesisId: string;
  kills: KillCriterionOut[];
}) {
  const put = usePutKillCriteria(thesisId);
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<string[]>([]);

  const open = () => {
    setRows(kills.length ? kills.map((k) => k.text) : [""]);
    setEditing(true);
  };
  const save = () =>
    put.mutate(
      rows.filter((t) => t.trim()).map((t) => ({ text: t.trim() })),
      { onSuccess: () => setEditing(false) },
    );

  if (!editing) {
    return (
      <button type="button" className="wb-mini ghost" onClick={open}>
        {kills.length ? "✎ edit" : "+ add kill criteria (what would kill this thesis)"}
      </button>
    );
  }
  return (
    <div className="sle">
      {rows.map((t, i) => (
        <div className="sle-row" key={i}>
          <input
            aria-label={`kill criterion ${i + 1}`}
            placeholder="e.g. DRAM contract prices roll over two consecutive quarters"
            value={t}
            onChange={(e) => setRows(rows.map((x, j) => (j === i ? e.target.value : x)))}
          />
          <button
            type="button"
            className="wb-mini ghost"
            aria-label={`remove kill criterion ${i + 1}`}
            onClick={() => setRows(rows.filter((_, j) => j !== i))}
          >
            ✕
          </button>
        </div>
      ))}
      <div className="sle-actions">
        <button type="button" className="wb-mini ghost" onClick={() => setRows([...rows, ""])}>
          + row
        </button>
        <button type="button" className="wb-mini" disabled={put.isPending} onClick={save}>
          {put.isPending ? "Saving…" : "Save kill criteria"}
        </button>
        <button type="button" className="wb-mini ghost" onClick={() => setEditing(false)}>
          cancel
        </button>
      </div>
      {put.isError && <div className="note err">Couldn't save — {errText(put.error)}.</div>}
    </div>
  );
}
