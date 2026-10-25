import type { ReactNode } from "react";

import type { DataFlagSplitOut, DataNoteOut, MemberDataFlagsOut } from "../api/hooks";
import { fmtDate } from "../util/format";
import {
  CELL_LABEL,
  LEVEL_WORD,
  type CellFlag,
  factorWords,
  markTitle,
  rowLevel,
} from "./dataFlags";

/** The row mark's glyph (a small flag; see `DataFlagMark`). */
export const FLAG_GLYPH = "⚑";

/** One flagged cell's content (split plan S3): IN DOUBT keeps the number, grayed, with the reason on hover;
 *  KNOWN WRONG replaces it with a muted "—" carrying the reason. No flag → the children, untouched. */
export function FlagWrap({ flag, children }: { flag?: CellFlag | null; children: ReactNode }) {
  if (!flag) return <>{children}</>;
  if (flag.level === "known_wrong")
    return (
      <span className="muted flag-wrong" title={flag.reason}>
        —
      </span>
    );
  return (
    <span className="flag-doubt" title={flag.reason}>
      {children}
    </span>
  );
}

/** The quiet mark after a ticker: only when some flag reaches a cell on the row (WB#3 — a mark true of
 *  every row would be noise, and a flag no cell reads is the panel's and Admin's to tell). Hover lists
 *  each flag and the cells it touches. Never an alert (#7). The glyph is a small flag (⚑) — NOT the ◇,
 *  which already means "a basket / no ticker" across the app (the Board's "◇ 90", the Scoreboard's
 *  thesis-level rows), so a flagged ticker can never read as a basket marker. */
export function DataFlagMark({ member }: { member: MemberDataFlagsOut | null | undefined }) {
  const level = rowLevel(member);
  if (!level) return null;
  return (
    <span
      className={`flag-mark ${level === "known_wrong" ? "wrong" : "doubt"}`}
      title={markTitle(member)}
      aria-label={`data flag: ${LEVEL_WORD[level]}`}
    >
      {FLAG_GLYPH}
    </span>
  );
}

/** The split row's events, in the order they happen (plan section 11): announced, confirmed (and by
 *  which sources), took effect, history corrected. A needs-review split says it is not applied. */
export function splitEvents(s: DataFlagSplitOut): string[] {
  const out: string[] = [];
  if (s.sec_filed) out.push(`announced — SEC ${s.sec_form ?? "filing"} filed ${s.sec_filed}`);
  else if (s.polygon) out.push("listed by Massive.com");
  if (s.status === "needs_review")
    out.push("needs review — not applied until it is confirmed (Admin → Splits)");
  else if (s.confirmed_by) out.push(`confirmed — ${s.confirmed_by}`);
  if (s.took_effect) out.push(`took effect ${s.effective_date} (prices before it ${factorWords(s.factor)})`);
  else if (s.status === "announced") out.push(`takes effect ${s.effective_date}`);
  if (s.corrected_on)
    out.push(`history corrected ${s.corrected_on}${s.corrected_note ? ` — ${s.corrected_note}` : ""}`);
  return out;
}

/** A quiet marker's line (data hygiene): information about the name, not a defect in its data — muted,
 *  no level badge, no border, never a row mark. */
export function DataNoteLine({ note }: { note: DataNoteOut }) {
  return (
    <div className="np-note" data-testid="np-note">
      {note.reason}
    </div>
  );
}

/** The NamePanel's "Data · this name" section: every flag on the name (the full reason, the evidence,
 *  since when, what clears it, the cells it touches — or that it touches none), the company's split
 *  rows with their events, and last, quietest, its markers (a short price history). Renders nothing for
 *  a name with none of them (the common case). */
export function DataFlagsSection({ member }: { member: MemberDataFlagsOut | null | undefined }) {
  const flags = member?.flags ?? [];
  const splits = member?.splits ?? [];
  const notes = member?.notes ?? [];
  if (flags.length === 0 && splits.length === 0 && notes.length === 0) return null;
  return (
    <>
      <div className="np-h">Data · this name</div>
      {flags.map((f, i) => (
        <div className={`np-flag ${f.level === "known_wrong" ? "wrong" : "doubt"}`} key={`f${i}`}>
          <div className="np-flag-h">
            <span className="np-flag-lvl">{LEVEL_WORD[f.level]}</span>
            <b>{f.label}</b>
            {f.since && <span className="np-flag-since">since {fmtDate(f.since)}</span>}
          </div>
          <div className="np-flag-reason">{f.reason}</div>
          <div className="np-flag-meta">
            {(f.columns ?? []).length > 0
              ? `affects ${(f.columns ?? []).map((c) => CELL_LABEL[c] ?? c).join(", ")}`
              : "no number on the row is affected"}
            {f.call_affected && " · price triggers held on the call card"}
          </div>
          <div className="np-flag-meta">{f.clears}</div>
          {(f.evidence ?? []).length > 0 && (
            <div className="np-flag-meta">
              {(f.evidence ?? []).map((e, j) =>
                e.url ? (
                  <a key={j} className="np-flag-ev" href={e.url} target="_blank" rel="noreferrer">
                    {e.label}
                  </a>
                ) : (
                  <span key={j} className="np-flag-ev">
                    {e.label}
                  </span>
                ),
              )}
            </div>
          )}
        </div>
      ))}
      {splits.map((s) => (
        <div
          className={`np-split${s.status === "needs_review" ? " pending" : ""}`}
          key={`${s.key_date}-${s.effective_date}`}
        >
          <div className="np-split-h">
            <b>{s.label}</b> · first session {s.effective_date}
            {s.sec_url && (
              <>
                {" "}
                <a className="np-flag-ev" href={s.sec_url} target="_blank" rel="noreferrer">
                  SEC {s.sec_form ?? "filing"}
                </a>
              </>
            )}
          </div>
          {splitEvents(s).map((line, j) => (
            <div className="np-split-ev" key={j}>
              {line}
            </div>
          ))}
        </div>
      ))}
      {notes.map((n) => (
        <DataNoteLine note={n} key={n.kind} />
      ))}
    </>
  );
}
