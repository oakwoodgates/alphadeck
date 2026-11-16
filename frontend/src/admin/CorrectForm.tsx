import { useState } from "react";

import type { PlatformCandidateOut, PlatformCorrectIn, PlatformFactType } from "../api/hooks";
import { cashSymbol, labelCurrency } from "../components/LocatedPassages";
import { storedCurrency } from "./factWords";

// The steward's "Correct" form: the value they type, the filing it comes from, and its date. It starts from the
// candidate (when one waits), the value shown, or an undone correction's set-aside version ("Correct again"), and
// refuses to send a blank value, a blank filing URL or a blank date. The server judges the rest (a date older than
// the value shown is refused with its own sentence, shown beside the form by the block). A cash/burn value also
// takes its statement currency (an ISO code, e.g. CNY; blank = not recorded, never assumed USD) and the part of the
// cash that is short-term investments (blank = no split recorded; never negative, never above the cash).

export interface CorrectInit {
  shares: string;
  adsStatus: "" | "known" | "unread";
  adsRatio: string;
  cash: string;
  burn: string;
  sourceRef: string;
  eventDate: string;
  note: string;
  candidateId?: string;
  /** The label the figures are shown in before a code is typed ("?" for an unread currency, null = "$"). */
  currency?: string | null;
  /** The ISO code the correction states (blank = not recorded). */
  currencyCode?: string;
  /** The short-term-investment part of the cash (blank = no split recorded). */
  investments?: string;
}

const str = (n: number | null | undefined) => (n == null ? "" : String(n));
const adsStatusOf = (s: string | null | undefined): "" | "known" | "unread" =>
  s === "known" || s === "unread" ? s : "";

export const blankInit: CorrectInit = {
  shares: "",
  adsStatus: "",
  adsRatio: "",
  cash: "",
  burn: "",
  sourceRef: "",
  eventDate: "",
  note: "",
};

/** Prefill from a stored candidate: its own values, filing and date; the correction closes it. */
export function initFromCandidate(c: PlatformCandidateOut): CorrectInit {
  return {
    shares: str(c.shares),
    adsStatus: adsStatusOf(c.ads_ratio_status),
    adsRatio: str(c.ads_ratio),
    cash: str(c.cash_usd),
    burn: str(c.quarterly_burn_usd),
    sourceRef: c.source_ref ?? "",
    eventDate: c.event_date ?? "",
    note: "",
    candidateId: c.id,
    currency: labelCurrency(c.statement_currency, c.flags),
    currencyCode: c.statement_currency ?? "",
    investments: str(c.investments_usd),
  };
}

/** Prefill from a stored value (the value shown, or a set-aside version): same fields, no candidate. */
export function initFromValue(v: {
  shares?: number | null;
  ads_ratio?: number | null;
  ads_ratio_status?: string | null;
  cash_usd?: number | null;
  quarterly_burn_usd?: number | null;
  currency?: string | null;
  investments_usd?: number | null;
  source?: string | null;
  source_ref?: string | null;
  valid_from: string;
}): CorrectInit {
  return {
    shares: str(v.shares),
    adsStatus: adsStatusOf(v.ads_ratio_status),
    adsRatio: str(v.ads_ratio),
    cash: str(v.cash_usd),
    burn: str(v.quarterly_burn_usd),
    sourceRef: v.source_ref ?? "",
    eventDate: v.valid_from,
    note: "",
    currency: storedCurrency(v),
    currencyCode: v.currency ?? "",
    investments: str(v.investments_usd),
  };
}

/** An ISO currency code: three capital letters (the server's rule, `domain/text_types.CURRENCY_CODE_PATTERN`). */
export const CURRENCY_CODE = /^[A-Z]{3}$/;

/** A typed number that is really one: blank is not zero. */
const isNum = (s: string) => s.trim() !== "" && Number.isFinite(Number(s));

/** The request body for the value type, or null while the form is not ready to send. */
export function correctBody(factType: PlatformFactType, f: CorrectInit): PlatformCorrectIn | null {
  const sourceRef = f.sourceRef.trim();
  if (!sourceRef || !f.eventDate) return null;
  const common = {
    source_ref: sourceRef,
    event_date: f.eventDate,
    ...(f.note.trim() ? { note: f.note.trim() } : {}),
    ...(f.candidateId ? { candidate_id: f.candidateId } : {}),
  };
  if (factType === "shares_outstanding") {
    if (!isNum(f.shares)) return null;
    if (f.adsStatus === "known" && !isNum(f.adsRatio)) return null;
    return {
      fact_type: "shares_outstanding",
      shares: Number(f.shares),
      ...(f.adsStatus === "known" ? { ads_ratio: Number(f.adsRatio), ads_ratio_status: "known" as const } : {}),
      ...(f.adsStatus === "unread" ? { ads_ratio_status: "unread" as const } : {}),
      ...common,
    };
  }
  if (!isNum(f.cash) || !isNum(f.burn)) return null;
  const code = (f.currencyCode ?? "").trim();
  if (code && !CURRENCY_CODE.test(code)) return null;
  const part = (f.investments ?? "").trim();
  if (part && (!isNum(part) || Number(part) < 0 || Number(part) > Number(f.cash))) return null;
  return {
    fact_type: "cash_burn",
    cash_usd: Number(f.cash),
    quarterly_burn_usd: Number(f.burn),
    ...(code ? { currency: code } : {}),
    ...(part ? { investments_usd: Number(part) } : {}),
    ...common,
  };
}

export function CorrectForm({
  factType,
  init,
  busy,
  onSave,
  onCancel,
}: {
  factType: PlatformFactType;
  init: CorrectInit;
  busy: boolean;
  onSave: (body: PlatformCorrectIn) => void;
  onCancel: () => void;
}) {
  const [f, setF] = useState<CorrectInit>(init);
  const set = <K extends keyof CorrectInit>(k: K, v: CorrectInit[K]) => setF((p) => ({ ...p, [k]: v }));
  const body = correctBody(factType, f);
  const code = (f.currencyCode ?? "").trim();
  const sym = cashSymbol(CURRENCY_CODE.test(code) ? code : f.currency);
  return (
    <form
      className="cf-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (body && !busy) onSave(body);
      }}
    >
      {factType === "shares_outstanding" ? (
        <>
          <label className="cf-field">
            Share count
            <input type="number" step="any" value={f.shares} onChange={(e) => set("shares", e.target.value)} />
          </label>
          <label className="cf-field">
            ADS ratio status
            <select value={f.adsStatus} onChange={(e) => set("adsStatus", e.target.value as CorrectInit["adsStatus"])}>
              <option value="">not an ADS</option>
              <option value="known">known</option>
              <option value="unread">unread</option>
            </select>
          </label>
          {f.adsStatus === "known" && (
            <label className="cf-field">
              ADS ratio
              <input type="number" step="any" value={f.adsRatio} onChange={(e) => set("adsRatio", e.target.value)} />
            </label>
          )}
        </>
      ) : (
        <>
          <label className="cf-field">
            Cash ({sym})
            <input type="number" step="any" value={f.cash} onChange={(e) => set("cash", e.target.value)} />
          </label>
          <label className="cf-field">
            Quarterly burn ({sym})
            <input type="number" step="any" value={f.burn} onChange={(e) => set("burn", e.target.value)} />
          </label>
          <label className="cf-field">
            Currency (ISO code)
            <input
              type="text"
              maxLength={3}
              placeholder="blank = not recorded"
              value={f.currencyCode ?? ""}
              onChange={(e) => set("currencyCode", e.target.value.toUpperCase())}
            />
          </label>
          <label className="cf-field">
            Of which short-term investments ({sym})
            <input
              type="number"
              step="any"
              min={0}
              placeholder="blank = no split"
              value={f.investments ?? ""}
              onChange={(e) => set("investments", e.target.value)}
            />
          </label>
        </>
      )}
      <label className="cf-field wide">
        Filing URL
        <input type="text" value={f.sourceRef} onChange={(e) => set("sourceRef", e.target.value)} />
      </label>
      <label className="cf-field">
        Filing date
        <input type="date" value={f.eventDate} onChange={(e) => set("eventDate", e.target.value)} />
      </label>
      <label className="cf-field wide">
        Note (optional)
        <input type="text" value={f.note} onChange={(e) => set("note", e.target.value)} />
      </label>
      <div className="cf-row">
        <button type="submit" className="adm-minibtn primary" disabled={!body || busy}>
          Save correction
        </button>
        <button type="button" className="adm-minibtn adm-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
