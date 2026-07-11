import { useState } from "react";

import { useRatifyFact } from "../api/hooks";
import { todayISO } from "../util/format";
import { errText } from "./format";

const CATALYST_TYPES = [
  "contract",
  "earnings",
  "regulatory",
  "gov_funding",
  "clinical_readout",
  "personnel",
  "promoter_attention",
] as const;

/** "+ log a catalyst" (slice A2) — the hand-authored per-security CONVICTION fact, the Key-1 arming
 *  path (``fact_catalyst`` via the ratify union's ``catalyst`` variant). No extractor candidate
 *  exists for these, so the CITATION is the provenance (#6): source_ref is required — an uncited
 *  catalyst is a bare claim and the server 422s it. The operator authors the verifiable event; the
 *  platform grades its liveness and times the call. Collapsed by default (quiet — authoring is the
 *  exception, not the rule); distinct from the Cockpit's catalyst CALENDAR (display events). */
export function CatalystFactForm({ securityId }: { securityId: string }) {
  const ratify = useRatifyFact();
  const [open, setOpen] = useState(false);
  const [ctype, setCtype] = useState<string>("contract");
  const [grade, setGrade] = useState<string>("core");
  const [label, setLabel] = useState("");
  const [eventDate, setEventDate] = useState(todayISO());
  const [horizonEnd, setHorizonEnd] = useState("");
  const [sourceRef, setSourceRef] = useState("");

  if (!open) {
    return (
      <button type="button" className="wb-mini ghost" onClick={() => setOpen(true)}>
        + log a catalyst (conviction fact)
      </button>
    );
  }
  if (ratify.isSuccess && ratify.data?.fact_type === "catalyst") {
    return (
      <div className="ratify-row done">
        ✓ catalyst logged — a Key-1 conviction fact; the call re-derives.
      </div>
    );
  }
  return (
    <div className="cff">
      <label className="rf">
        type
        <select aria-label="catalyst type" value={ctype} onChange={(e) => setCtype(e.target.value)}>
          {CATALYST_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </label>
      <label className="rf">
        grade
        <select aria-label="catalyst grade" value={grade} onChange={(e) => setGrade(e.target.value)}>
          <option value="core">core (structural)</option>
          <option value="flip">flip (sentiment)</option>
        </select>
      </label>
      <label className="rf">
        known since
        <input
          type="date"
          aria-label="catalyst event date"
          value={eventDate}
          max={todayISO()}
          onChange={(e) => setEventDate(e.target.value)}
        />
      </label>
      <label className="rf">
        horizon end
        <input
          type="date"
          aria-label="catalyst horizon end"
          value={horizonEnd}
          onChange={(e) => setHorizonEnd(e.target.value)}
        />
      </label>
      <label className="rf wide">
        what happened
        <input
          aria-label="catalyst label"
          placeholder="e.g. 10-year offtake agreement signed with …"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
      </label>
      <label className="rf wide">
        citation (required)
        <input
          aria-label="catalyst citation"
          placeholder="the press release / 8-K / IR page URL — provenance is the point"
          value={sourceRef}
          onChange={(e) => setSourceRef(e.target.value)}
        />
      </label>
      {ratify.isError && <div className="note err">Couldn't log — {errText(ratify.error)}.</div>}
      <div className="sle-actions">
        <button
          type="button"
          className="wb-mini"
          disabled={ratify.isPending || !label.trim() || !sourceRef.trim()}
          onClick={() =>
            ratify.mutate({
              fact_type: "catalyst",
              security_id: securityId,
              catalyst_type: ctype as never,
              grade: grade as never,
              label: label.trim(),
              source: "ratified",
              source_ref: sourceRef.trim(),
              event_date: eventDate,
              horizon_end: horizonEnd || null,
            })
          }
        >
          {ratify.isPending ? "Logging…" : "Log the catalyst"}
        </button>
        <button type="button" className="wb-mini ghost" onClick={() => setOpen(false)}>
          cancel
        </button>
      </div>
    </div>
  );
}
