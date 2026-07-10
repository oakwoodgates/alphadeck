import { useState } from "react";

import {
  useDecisions,
  usePostDecision,
  type CallCardResponse,
  type DecisionOut,
} from "../api/hooks";
import { errText } from "../workbench/format";
import { fmtDate } from "../util/format";

/** Decision capture — the CallCard's action row, wired to the operator-decisions log.
 *
 *  Advisory only (#5): every button LOGS a decision the operator made elsewhere — nothing routes,
 *  nothing blocks. State-appropriate (inverse loudness): Armed offers the loud "log the fill";
 *  Managing offers "log exit"; a not-yet state shows the GATE — friction copy + a logged override,
 *  never a wall. "Pass" is quiet and available at any state. A mistake is corrected by void (an
 *  append that greys the row — visible, never deleted). The take → Managing flip is visible: the
 *  mutation invalidates the call, and the card re-derives with the logged position.
 */
export function DecisionActions({
  thesisId,
  card,
}: {
  thesisId: string;
  card: CallCardResponse;
}) {
  const decisions = useDecisions(thesisId);
  const post = usePostDecision(thesisId);
  const [form, setForm] = useState<"take" | "close" | "pass" | null>(null);
  const today = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(today);
  const [shares, setShares] = useState("");
  const [price, setPrice] = useState("");
  const [reason, setReason] = useState("");
  // the name being taken — defaults to the platform's headline (the armed pick); the operator can
  // pick any member the card knows, or log at thesis level (the empty option)
  const members = [...(card.armed_members ?? []), ...(card.watch_members ?? [])];
  const [secId, setSecId] = useState(card.armed_security_id ?? "");

  const armed = card.state === "armed";
  const managing = card.state === "managing";
  // the v1 gate: taking against a withheld go-signal is fine — it's logged as an override
  const against = !armed && !managing;

  const open = (f: "take" | "close" | "pass") => {
    setForm(form === f ? null : f);
    setDate(today);
    setShares("");
    setPrice("");
    setReason("");
  };

  const submit = (action: "take" | "close" | "pass") =>
    post.mutate(
      {
        action,
        decision_date: date,
        security_id: action === "take" && secId ? secId : null,
        shares: action !== "pass" && shares ? Number(shares) : null,
        price: action !== "pass" && price ? Number(price) : null,
        reason: reason || null,
      },
      { onSuccess: () => setForm(null) },
    );

  const voidRow = (id: string) =>
    post.mutate({ action: "void", decision_date: today, voids: id });

  return (
    <div className="dc">
      {/* the state-appropriate primary + the always-quiet pass */}
      {armed && (
        <div className="actions">
          <button className="btn primary" type="button" onClick={() => open("take")}>
            Act — log the fill
          </button>
          <button className="btn" type="button" onClick={() => open("pass")}>
            Pass (logged)
          </button>
        </div>
      )}
      {managing && (
        <div className="actions two">
          <button className="btn" type="button" onClick={() => open("close")}>
            Log exit
          </button>
          <button className="btn" type="button" onClick={() => open("pass")}>
            Note a pass
          </button>
        </div>
      )}
      {against && (
        <div className="gate">
          <div className="gate-note">
            The gate is withholding the go-signal — friction, not a block. Enter early if you
            disagree; the override is logged with the platform's stance.
          </div>
          <div className="actions two">
            <button className="btn wide" type="button" onClick={() => open("take")}>
              Override — log an early entry
            </button>
            <button className="btn" type="button" onClick={() => open("pass")}>
              Pass (logged)
            </button>
          </div>
        </div>
      )}

      {form && (
        <div className="dc-form">
          {form === "take" && against && (
            <div className="dc-friction">
              the platform's verdict is {card.verdict.replace(/_/g, "-")} — logging this take as an
              override
            </div>
          )}
          {form === "take" && members.length > 0 && (
            <label className="rf">
              name
              <select
                aria-label="decision name"
                value={secId}
                onChange={(e) => setSecId(e.target.value)}
              >
                <option value="">— thesis level —</option>
                {members.map((m) => (
                  <option key={m.security_id} value={m.security_id}>
                    {m.ticker ?? m.security_id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="rf">
            date
            <input
              type="date"
              aria-label="decision date"
              value={date}
              max={today}
              onChange={(e) => setDate(e.target.value)}
            />
          </label>
          {form !== "pass" && (
            <>
              <label className="rf">
                shares
                <input
                  type="number"
                  aria-label="decision shares"
                  value={shares}
                  placeholder="optional"
                  onChange={(e) => setShares(e.target.value)}
                />
              </label>
              <label className="rf">
                price $
                <input
                  type="number"
                  aria-label="decision price"
                  value={price}
                  placeholder="optional"
                  onChange={(e) => setPrice(e.target.value)}
                />
              </label>
            </>
          )}
          <label className="rf wide">
            {form === "pass" ? "why (optional, encouraged)" : "note"}
            <input
              aria-label="decision reason"
              value={reason}
              placeholder={form === "pass" ? "e.g. verdict is not-yet; agreed" : "optional"}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {post.isError && <div className="note err">Couldn't log — {errText(post.error)}.</div>}
          <button
            type="button"
            className="wb-mini"
            disabled={post.isPending}
            onClick={() => submit(form)}
          >
            {post.isPending ? "Logging…" : `Log ${form}`}
          </button>
        </div>
      )}

      {/* the decision history — the record itself, quiet; voided rows grey, never vanish */}
      {(decisions.data ?? []).length > 0 && (
        <div className="dc-log">
          <div className="dc-log-h">Decision log</div>
          {(decisions.data ?? []).slice(0, 6).map((d) => (
            <DecisionRow key={d.id} d={d} onVoid={voidRow} busy={post.isPending} />
          ))}
        </div>
      )}
    </div>
  );
}

function DecisionRow({
  d,
  onVoid,
  busy,
}: {
  d: DecisionOut;
  onVoid: (id: string) => void;
  busy: boolean;
}) {
  const detail = [
    d.shares != null ? `${d.shares} sh` : null,
    d.price != null ? `@ $${d.price}` : null,
    d.reason || null,
    // the gate's record, readable in place: what the platform said when this was logged
    d.call_verdict ? `platform: ${d.call_verdict.replace(/_/g, "-")}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className={`dc-row${d.voided ? " voided" : ""}`}>
      <span className="dc-act">{d.action}</span>
      <span className="dc-when">{fmtDate(d.decision_date)}</span>
      {detail && <span className="dc-detail">{detail}</span>}
      {d.voided && <span className="dc-voided-tag">voided</span>}
      {!d.voided && d.action !== "void" && (
        <button
          type="button"
          className="wb-mini ghost"
          disabled={busy}
          aria-label={`void this ${d.action}`}
          onClick={() => onVoid(d.id)}
          title="append a void — un-does this row on read; the row stays visible, greyed"
        >
          undo
        </button>
      )}
    </div>
  );
}
