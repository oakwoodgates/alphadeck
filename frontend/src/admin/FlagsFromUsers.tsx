import { useState } from "react";

import { apiErrorText } from "../api/errorText";
import type {
  PlatformDecisionOut,
  PlatformUserFlagOut,
  useCloseFlag,
  useReopenFlag,
} from "../api/hooks";
import {
  FIXED_NEEDS_CORRECTION,
  FLAGS_HEADING,
  KEPT_NOTE_HINT,
  KEPT_NOTE_REQUIRED,
  LINKED_UNDONE_WORDS,
  closedWords,
  day,
  decisionWords,
  fixingDecision,
  seenWords,
} from "./factWords";

// The user flags on the steward's company page (V2 W5f). A flag is a user saying "this platform value looks wrong";
// the steward closes it as FIXED (naming the approve or correct that fixed it) or KEPT (a note the user reads), and
// can REOPEN one the steward closed (the close's visible inverse; it adds a new open flag, nothing is rewritten).
// Loud only while a flag is open (nothing renders when none is). Every refusal is the server's own sentence, shown on
// the flag that made the call. Everything a user or the steward wrote (email, note, outcome note) is a React text
// node: never markup, never a link.

type CloseM = ReturnType<typeof useCloseFlag>;
type ReopenM = ReturnType<typeof useReopenFlag>;

/** The server's refusal of the last write on THIS flag (a close or a reopen), exactly as sent. */
function refusalFor(flagId: string, ...ms: { isError: boolean; error: unknown; variables?: unknown }[]): string | null {
  for (const m of ms) {
    if (m.isError && (m.variables as { flagId?: string } | undefined)?.flagId === flagId) {
      return apiErrorText(m.error) ?? "The request was refused.";
    }
  }
  return null;
}

function OpenFlag({
  securityId,
  flag,
  fixer,
  busy,
  close,
  reopen,
}: {
  securityId: string;
  flag: PlatformUserFlagOut;
  fixer: PlatformDecisionOut | undefined;
  busy: boolean;
  close: CloseM;
  reopen: ReopenM;
}) {
  const [mode, setMode] = useState<"fixed" | "kept" | null>(null);
  const [note, setNote] = useState("");
  const [blank, setBlank] = useState(false);
  const refusal = refusalFor(flag.id, close, reopen);

  const pick = (m: "fixed" | "kept") => {
    setMode((cur) => (cur === m ? null : m));
    setBlank(false);
  };
  const cancel = () => {
    setMode(null);
    setNote("");
    setBlank(false);
  };
  const send = () => {
    const text = note.trim();
    if (mode === "kept") {
      if (!text) {
        setBlank(true);
        return;
      }
      close.mutate({ securityId, flagId: flag.id, outcome: "kept", note: text });
    } else if (mode === "fixed" && fixer) {
      close.mutate({
        securityId,
        flagId: flag.id,
        outcome: "fixed",
        decisionId: fixer.id,
        ...(text ? { note: text } : {}),
      });
    }
  };

  return (
    <li className="cf-flag" data-testid="cf-flag-open">
      <div>
        From <b>{flag.user_email ?? "a user"}</b> · {day(flag.opened_at)}
      </div>
      {flag.reopened_by && (
        <div className="cf-quiet">
          Reopened by {flag.reopened_by} on {day(flag.opened_at)}
        </div>
      )}
      <div className="cf-quiet">Current when flagged: {seenWords(flag)}</div>
      {flag.note?.trim() && (
        <div>
          <span className="cf-quiet">Note: </span>
          <span className="cf-flag-note">{flag.note}</span>
        </div>
      )}
      <div className="cf-row">
        <button type="button" className="adm-minibtn" disabled={busy || !fixer} onClick={() => pick("fixed")}>
          Close as fixed…
        </button>
        <button type="button" className="adm-minibtn" disabled={busy} onClick={() => pick("kept")}>
          Close as kept…
        </button>
        {!fixer && (
          <span className="cf-quiet" data-testid="cf-flag-fixed-reason">
            {FIXED_NEEDS_CORRECTION}
          </span>
        )}
      </div>
      {mode && (
        <div className="cf-row" data-testid="cf-flag-close-form">
          {mode === "fixed" && fixer && <span className="cf-quiet">Fixed by: {decisionWords(fixer)}</span>}
          <input
            type="text"
            className="cf-note"
            maxLength={2000}
            aria-label={mode === "kept" ? "Note to the user (required)" : "Note to the user (optional)"}
            placeholder={mode === "kept" ? "Why the value stands" : "Note (optional)"}
            value={note}
            onChange={(e) => {
              setNote(e.target.value);
              setBlank(false);
            }}
          />
          {mode === "kept" && <span className="cf-quiet">{KEPT_NOTE_HINT}</span>}
          <button type="button" className="adm-minibtn primary" disabled={busy} onClick={send}>
            {mode === "kept" ? "Close as kept" : "Close as fixed"}
          </button>
          <button type="button" className="adm-minibtn adm-ghost" onClick={cancel}>
            Cancel
          </button>
          {blank && (
            <div className="adm-err" role="alert">
              {KEPT_NOTE_REQUIRED}
            </div>
          )}
        </div>
      )}
      {refusal && (
        <div className="adm-err" role="alert">
          {refusal}
        </div>
      )}
    </li>
  );
}

/** The loud block under a value's status line: only while a flag of this value is open. */
export function OpenFlags({
  securityId,
  flags,
  decisions,
  busy,
  close,
  reopen,
}: {
  securityId: string;
  flags: PlatformUserFlagOut[];
  decisions: PlatformDecisionOut[];
  busy: boolean;
  close: CloseM;
  reopen: ReopenM;
}) {
  if (flags.length === 0) return null;
  const fixer = fixingDecision(decisions);
  return (
    <div className="cf-flags" data-testid="cf-flags">
      <div className="cf-loud">{FLAGS_HEADING}</div>
      <ul>
        {flags.map((f) => (
          <OpenFlag
            key={f.id}
            securityId={securityId}
            flag={f}
            fixer={fixer}
            busy={busy}
            close={close}
            reopen={reopen}
          />
        ))}
      </ul>
    </div>
  );
}

/** The closed flags of a value, gray, under History: outcome, notes, who and when; Reopen when the server says it
 *  can be, else the server's reason as quiet text. `shown` / `total` count every closed flag of the company, loaded
 *  against all, so "Show older" stays honest. */
export function ClosedFlags({
  securityId,
  flags,
  busy,
  close,
  reopen,
  shown,
  total,
  paging,
  onOlder,
}: {
  securityId: string;
  flags: PlatformUserFlagOut[];
  busy: boolean;
  close: CloseM;
  reopen: ReopenM;
  shown: number;
  total: number;
  paging: { isFetching: boolean; error: string | null };
  onOlder: () => void;
}) {
  const more = shown < total;
  if (flags.length === 0 && !more) return null;
  return (
    <>
      <h3 className="cf-h3">{FLAGS_HEADING}</h3>
      <ul data-testid="cf-flags-closed">
        {flags.map((f) => {
          const refusal = refusalFor(f.id, reopen, close);
          return (
            <li key={f.id} className="cf-undone" data-testid="cf-flag-closed">
              <div>
                {closedWords(f)} · flagged by {f.user_email ?? "a user"} on {day(f.opened_at)}
              </div>
              {f.note?.trim() && (
                <div>
                  <span>Flag note: </span>
                  <span>{f.note}</span>
                </div>
              )}
              {f.outcome_note?.trim() && (
                <div>
                  <span>Note to the user: </span>
                  <span>{f.outcome_note}</span>
                </div>
              )}
              {f.fixed_decision && <div>Linked to: {decisionWords(f.fixed_decision)}</div>}
              {f.fixed_decision_undone && <div>{LINKED_UNDONE_WORDS}</div>}
              {f.can_reopen ? (
                <button
                  type="button"
                  className="adm-minibtn"
                  disabled={busy}
                  onClick={() => reopen.mutate({ securityId, flagId: f.id })}
                >
                  Reopen
                </button>
              ) : (
                f.reopen_refusal && <div className="cf-quiet">{f.reopen_refusal}</div>
              )}
              {refusal && (
                <div className="adm-err" role="alert">
                  {refusal}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {more && (
        <div className="cf-row">
          <span className="cf-quiet">
            {shown} of {total} closed flags shown for this company
          </span>
          <button type="button" className="adm-minibtn" disabled={paging.isFetching} onClick={onOlder}>
            {paging.isFetching ? "Loading…" : "Show older"}
          </button>
        </div>
      )}
      {paging.error && <div className="adm-err">{paging.error}</div>}
    </>
  );
}
