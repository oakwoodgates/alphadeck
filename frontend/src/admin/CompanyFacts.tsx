import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { apiErrorText } from "../api/errorText";
import {
  useApproveCandidate,
  useCompanyValues,
  useCorrectValue,
  useDismissCandidate,
  useExplainFlag,
  useExtractNow,
  usePinValue,
  useUndoDecision,
  useUnpinValue,
  type ExtractedFact,
  type PlatformCandidateOut,
  type PlatformDecisionOut,
  type PlatformExtractionAttemptOut,
  type PlatformFactType,
  type PlatformValueOut,
} from "../api/hooks";
import { refusalText } from "../api/refusals";
import { ExternalLink, safeHttpUrl } from "../components/ExternalLink";
import { labelCurrency, storedCurrency } from "../components/currency";
import { LocatedPassages, MISSING_FLAGS } from "../components/LocatedPassages";
import { adminPath } from "../nav";
import { CorrectForm, blankInit, initFromCandidate, initFromValue, type CorrectInit } from "./CorrectForm";
import { FactSearch } from "./FactSearch";
import {
  EXTRACT_NOTE,
  FACT_TITLE,
  PINNED_FLAG,
  PINNED_HELD_WORDS,
  UNDO_OF_UNDO_WORDS,
  annualWords,
  attemptWords,
  day,
  decisionWords,
  explainBody,
  extractLine,
  isOpen,
  isStanding,
  lowerFirst,
  paceWords,
  pinEventOf,
  pinWords,
  setWords,
  statusWords,
  undoOf,
  valueWords,
} from "./factWords";

// The steward's company page (V2 W4b, /admin/facts/:securityId): a company's share count and cash/burn, each with its
// state, who set it and when, its filing and date, the evidence behind a waiting candidate, the six actions
// (Approve, Correct, Dismiss, Undo, Pin, Unpin) and the history. Workbench interaction principles: every action
// has a way back on this page (an undo cannot itself be undone, so the page says to decide again); nothing
// vanishes (a dismissed candidate and an undone decision stay on screen, gray); loudness marks only what awaits
// the steward. A refusal shows the server's own sentence, as given, inside the block that made the call.

type AnyMutation = { isError: boolean; error: unknown; isPending: boolean; variables?: unknown };

interface Mutations {
  approve: ReturnType<typeof useApproveCandidate>;
  correct: ReturnType<typeof useCorrectValue>;
  dismiss: ReturnType<typeof useDismissCandidate>;
  undo: ReturnType<typeof useUndoDecision>;
  pin: ReturnType<typeof usePinValue>;
  unpin: ReturnType<typeof useUnpinValue>;
}

/** The fact type a write was made for (every write's variables carry it; a correction's body does). */
function factTypeOfVars(vars: unknown): string | null {
  const v = vars as { factType?: string; body?: { fact_type?: string } } | undefined;
  return v?.factType ?? v?.body?.fact_type ?? null;
}

/** The server's refusal for THIS value, from whichever write last failed on it: its sentence exactly as sent. */
function errorFor(factType: PlatformFactType, m: Mutations): string | null {
  for (const x of Object.values(m) as AnyMutation[]) {
    if (x.isError && factTypeOfVars(x.variables) === factType) {
      return apiErrorText(x.error) ?? "The request was refused.";
    }
  }
  return null;
}

/** A filing link through ExternalLink (a data-fed URL never goes in a bare anchor), or the plain words saying there is none. */
function FilingLink({ href }: { href: string | null | undefined }) {
  return safeHttpUrl(href) ? (
    <ExternalLink href={href} title={href ?? undefined}>
      filing ↗
    </ExternalLink>
  ) : (
    <span className="cf-quiet">no filing link</span>
  );
}

function Flags({ flags }: { flags: string[] }) {
  return (
    <>
      {flags
        .filter((f) => f !== PINNED_FLAG)
        .map((f) => (
          <span className={MISSING_FLAGS.has(f) ? "rflag missing" : "rflag"} key={f}>
            {MISSING_FLAGS.has(f) ? "∅" : "⚠"} {f}
          </span>
        ))}
    </>
  );
}

/** Explain in plain English: an aid grounded in the located passage, never a value. Flag candidates only; a click. */
function Explain({ c }: { c: PlatformCandidateOut }) {
  const explain = useExplainFlag(explainBody(c) as ExtractedFact);
  return (
    <div className="drafted-wrap">
      <button
        type="button"
        className="wb-mini ghost"
        aria-label="Explain in plain English"
        onClick={() => explain.refetch()}
        disabled={explain.isFetching}
      >
        {explain.isFetching ? "Explaining…" : "✦ Explain in plain English"}
      </button>
      {explain.data?.grounded && (
        <div className="drafted">
          <span className="wb-author">drafted</span>
          <span className="drafted-text">{explain.data.explanation}</span>
        </div>
      )}
      {explain.data && !explain.data.grounded && !explain.isFetching && (
        <div className="drafted muted">
          No plain-English read grounded in the passage — read the excerpt above.
        </div>
      )}
      {explain.isError && !explain.isFetching && (
        <div className="drafted muted">
          {refusalText(explain.error) ??
            `Couldn't reach the explainer${apiErrorText(explain.error) ? ` — ${apiErrorText(explain.error)}` : ""}. Click Explain to try again.`}
        </div>
      )}
    </div>
  );
}

/** What a flag candidate holds: the raw value, flags, filing and date, who found it, and the located passages. */
function CandidateEvidence({ c }: { c: PlatformCandidateOut }) {
  const unreadable = c.passages_unreadable ?? 0;
  return (
    <div className="cf-evidence" data-testid="cf-candidate-evidence">
      <div>
        <b>{valueWords(c.fact_type, c, labelCurrency(c.statement_currency, c.flags))}</b> <Flags flags={c.flags ?? []} />
      </div>
      {(c.flags ?? []).includes(PINNED_FLAG) && <div className="cf-quiet">{PINNED_HELD_WORDS}</div>}
      <div className="cf-quiet">
        <FilingLink href={c.source_ref} /> dated {c.event_date ?? "unknown"} · found by {c.source ?? "the extractor"}{" "}
        on {day(c.recorded_at)}
        {c.filing_accession ? ` · accession ${c.filing_accession}` : ""}
      </div>
      {paceWords(c.pace) && (
        <div className="cf-quiet" data-testid="cf-candidate-pace">
          {paceWords(c.pace, labelCurrency(c.statement_currency, c.flags))}
          {c.pace?.caveat ? `. ${c.pace.caveat}` : ""}
        </div>
      )}
      {c.note?.trim() && <div className="cf-quiet">{c.note}</div>}
      {unreadable > 0 && (
        <div className="cf-quiet">
          {unreadable} stored {unreadable === 1 ? "passage" : "passages"} could not be read
        </div>
      )}
      <LocatedPassages passages={c.located_passages ?? []} />
      {c.outcome === "flag" && <Explain c={c} />}
    </div>
  );
}

/** Extract now (W4d): a quiet button that reads the company's latest SEC filings on a click, the honest note of what
 *  it can change, and, after a click, one line per value type (or the failure's reason, or the server's refusal,
 *  exactly as sent). It is not a steward decision, so there is no Undo here: the way back is Correct or Pin. */
function ExtractNow({
  securityId,
  extract,
  busy,
}: {
  securityId: string;
  extract: ReturnType<typeof useExtractNow>;
  busy: boolean;
}) {
  const out = extract.data && extract.data.company.security_id === securityId ? extract.data : undefined;
  const attempt = !extract.isPending ? out?.attempt : undefined;
  const refusal = extract.isError && !extract.isPending ? (apiErrorText(extract.error) ?? "The request was refused.") : null;
  return (
    <div className="cf-extract" data-testid="cf-extract">
      <div className="cf-row">
        <button
          type="button"
          className="adm-minibtn adm-ghost"
          disabled={busy}
          onClick={() => extract.mutate({ securityId })}
        >
          {extract.isPending ? "Extracting…" : "Extract now"}
        </button>
        <span className="cf-quiet">{EXTRACT_NOTE}</span>
      </div>
      {attempt && attempt.status === "error" && (
        <div className="adm-err" role="alert">
          The extraction failed: {attempt.error ?? "no reason recorded"}
        </div>
      )}
      {attempt && attempt.status !== "error" && (
        <ul className="cf-extract-result">
          <li className="cf-quiet" data-testid="cf-extract-line">
            {extractLine("shares_outstanding", attempt.shares)}
          </li>
          <li className="cf-quiet" data-testid="cf-extract-line">
            {extractLine("cash_burn", attempt.cash_burn)}
          </li>
        </ul>
      )}
      {refusal && (
        <div className="adm-err" role="alert">
          {refusal}
        </div>
      )}
    </div>
  );
}

function ValueBlock({
  securityId,
  v,
  m,
  busy,
  attempts,
}: {
  securityId: string;
  v: PlatformValueOut;
  m: Mutations;
  busy: boolean;
  attempts: PlatformExtractionAttemptOut[];
}) {
  const factType = v.fact_type as PlatformFactType;
  const [form, setForm] = useState<CorrectInit | null>(null);
  const [dismissOpen, setDismissOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [approveNote, setApproveNote] = useState("");
  const [pinNote, setPinNote] = useState("");
  const [histOpen, setHistOpen] = useState(false);

  // a write's answer replaces the read: whatever form was open has done its job
  useEffect(() => {
    setForm(null);
    setDismissOpen(false);
    setReason("");
    setApproveNote("");
    setPinNote("");
  }, [v]);

  const status = statusWords(v);
  const value = v.value ?? null;
  const annual = value ? annualWords(value.source, value.valid_from) : null;
  const waiting = v.awaiting ?? v.pending ?? null;
  const pending = v.awaiting == null && v.pending != null;
  const decisions = v.decisions ?? [];
  const candidates = v.candidates ?? [];
  const versions = v.versions ?? [];
  const pinEvent = pinEventOf(v);
  const error = errorFor(factType, m);

  const lastStanding = decisions.find(isStanding);
  const newest = decisions[0];
  const undoneOne =
    newest?.action === "undo" ? decisions.find((d) => d.id === newest.revokes) : undefined;
  const versionOf = (d: PlatformDecisionOut) => versions.find((x) => x.id === d.fact_id);

  const undo = (d: PlatformDecisionOut) =>
    m.undo.mutate({ securityId, factType, decisionId: d.id });
  const approve = (c: PlatformCandidateOut, note?: string) =>
    m.approve.mutate({
      securityId,
      factType,
      candidateId: c.id,
      ...(note?.trim() ? { note: note.trim() } : {}),
    });
  const correctAgain = (d: PlatformDecisionOut) => {
    const ver = versionOf(d);
    if (ver) setForm(initFromValue(ver));
  };
  const canCorrectAgain = (d: PlatformDecisionOut) => d.action === "correct" && versionOf(d) != null;

  return (
    <section className="cf-block" data-testid={`cf-${v.fact_type === "shares_outstanding" ? "shares" : v.fact_type}`}>
      <h2 className="cf-h">{FACT_TITLE[factType] ?? v.fact_type}</h2>

      <div className={`cf-status${status.loud ? " cf-loud" : ""}`} data-testid="cf-status">
        <b>{status.label}</b>
        {value && (
          <>
            {" · "}
            {valueWords(v.fact_type, value, storedCurrency(value))}
            {paceWords(value.pace) && (
              <>
                {" · "}
                <span data-testid="cf-pace">{paceWords(value.pace, storedCurrency(value))}</span>
              </>
            )}
            {annual && (
              <>
                {" · "}
                <span data-testid="cf-annual">{annual}</span>
              </>
            )}
            {" · "}
            <FilingLink href={value.source_ref} /> dated {day(value.valid_from)}
            {" · "}
            {setWords(value.ratified_by, value.recorded_at)}
          </>
        )}
      </div>
      {/* the pace caveat (V2 extractor correctness PR 3): quiet, only when last quarter is far off the year-to-date
          pace; a caveat on the applied value, never something the steward owes a decision on */}
      {value?.pace?.caveat && (
        <div className="cf-quiet" data-testid="cf-pace-caveat">
          {value.pace.caveat}
        </div>
      )}

      {v.pinned && (
        <div className="cf-pin">
          <span className="cf-chip" data-testid="cf-pin-chip">
            Pinned: new filings wait for you
          </span>
          {pinEvent && (
            <span className="cf-quiet" data-testid="cf-pin-who">
              {" "}
              {pinWords(pinEvent)}
            </span>
          )}
        </div>
      )}

      {pending && waiting && (
        <div className="cf-pending cf-loud" data-testid="cf-pending">
          A newer filing ({waiting.event_date ?? "undated"}) is awaiting you
        </div>
      )}

      {waiting && (
        <div className="cf-candidate" data-testid="cf-candidate">
          <CandidateEvidence c={waiting} />
          <div className="cf-row">
            <input
              type="text"
              className="cf-note"
              aria-label="Approve note (optional)"
              placeholder="Note (optional)"
              value={approveNote}
              onChange={(e) => setApproveNote(e.target.value)}
            />
            <button type="button" className="adm-minibtn primary" disabled={busy} onClick={() => approve(waiting, approveNote)}>
              Approve
            </button>
            <button type="button" className="adm-minibtn" disabled={busy} onClick={() => setForm(initFromCandidate(waiting))}>
              Correct…
            </button>
            <button type="button" className="adm-minibtn" disabled={busy} onClick={() => setDismissOpen((o) => !o)}>
              Dismiss…
            </button>
          </div>
          {dismissOpen && (
            <div className="cf-row">
              <input
                type="text"
                className="cf-note"
                aria-label="Reason for dismissing"
                placeholder="Why dismiss this candidate?"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <button
                type="button"
                className="adm-minibtn primary"
                disabled={busy || !reason.trim()}
                onClick={() => m.dismiss.mutate({ securityId, factType, candidateId: waiting.id, reason: reason.trim() })}
              >
                Dismiss
              </button>
              <button type="button" className="adm-minibtn adm-ghost" onClick={() => setDismissOpen(false)}>
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {lastStanding && (
        <div className="cf-last cf-quiet" data-testid="cf-last-decision">
          {decisionWords(lastStanding)}{" "}
          <button type="button" className="adm-minibtn" disabled={busy} onClick={() => undo(lastStanding)}>
            Undo
          </button>
        </div>
      )}

      {undoneOne && (
        <div className="cf-last cf-quiet" data-testid="cf-undone-note">
          {decisionWords(undoneOne)} was undone.{" "}
          {undoneOne.action === "correct" && canCorrectAgain(undoneOne) ? (
            <button type="button" className="adm-minibtn" disabled={busy} onClick={() => correctAgain(undoneOne)}>
              Correct again
            </button>
          ) : (
            <span>{UNDO_OF_UNDO_WORDS}</span>
          )}
        </div>
      )}

      {form && (
        <CorrectForm
          factType={factType}
          init={form}
          busy={busy}
          onSave={(body) => m.correct.mutate({ securityId, body })}
          onCancel={() => setForm(null)}
        />
      )}

      <div className="cf-row cf-quiet-row">
        {!waiting && !form && (
          <button
            type="button"
            className="adm-minibtn adm-ghost"
            disabled={busy}
            onClick={() => setForm(value ? initFromValue(value) : blankInit)}
          >
            Correct…
          </button>
        )}
        <input
          type="text"
          className="cf-note"
          aria-label="Pin note (optional)"
          placeholder="Pin note (optional)"
          value={pinNote}
          onChange={(e) => setPinNote(e.target.value)}
        />
        {v.pinned ? (
          <button
            type="button"
            className="adm-minibtn adm-ghost"
            disabled={busy}
            onClick={() =>
              m.unpin.mutate({ securityId, factType, ...(pinNote.trim() ? { note: pinNote.trim() } : {}) })
            }
          >
            Unpin
          </button>
        ) : (
          <button
            type="button"
            className="adm-minibtn adm-ghost"
            disabled={busy}
            onClick={() =>
              m.pin.mutate({ securityId, factType, ...(pinNote.trim() ? { note: pinNote.trim() } : {}) })
            }
          >
            Pin
          </button>
        )}
      </div>

      {error && (
        <div className="adm-err" role="alert">
          {error}
        </div>
      )}

      <button
        type="button"
        className="adm-linkbtn"
        aria-expanded={histOpen}
        onClick={() => setHistOpen((o) => !o)}
      >
        {histOpen ? "▾ History" : "▸ History"}
      </button>
      {histOpen && (
        <div className="cf-history">
          <h3 className="cf-h3">Versions</h3>
          <ul data-testid="cf-versions">
            {versions.length === 0 && <li className="cf-quiet">No stored value.</li>}
            {versions.map((x) => {
              const dec = decisions.find((d) => d.fact_id === x.id && d.undone);
              const undoer = dec ? undoOf(dec, decisions) : undefined;
              return (
                <li key={x.id} data-testid="cf-version-row" className={x.set_aside ? "cf-undone" : undefined}>
                  {valueWords(v.fact_type, x, storedCurrency(x))} · dated {day(x.valid_from)} · <FilingLink href={x.source_ref} /> ·{" "}
                  {setWords(x.ratified_by, x.recorded_at)}
                  {x.set_aside && (
                    <b>
                      {" "}
                      · undone{undoer ? ` by ${undoer.decided_by} on ${day(undoer.recorded_at)}` : ""}
                    </b>
                  )}
                </li>
              );
            })}
          </ul>

          <h3 className="cf-h3">Decisions</h3>
          <ul data-testid="cf-decisions">
            {decisions.some((d) => d.action === "undo") && <li className="cf-quiet">{UNDO_OF_UNDO_WORDS}</li>}
            {decisions.length === 0 && <li className="cf-quiet">No decisions yet.</li>}
            {decisions.map((d) => {
              const undoer = d.undone ? undoOf(d, decisions) : undefined;
              return (
                <li key={d.id} data-testid="cf-decision-row" className={d.undone ? "cf-undone" : undefined}>
                  {decisionWords(d)}
                  {d.undone && (
                    <b>
                      {" "}
                      · undone{undoer ? ` by ${undoer.decided_by} on ${day(undoer.recorded_at)}` : ""}
                    </b>
                  )}{" "}
                  {isStanding(d) && (
                    <button type="button" className="adm-minibtn" disabled={busy} onClick={() => undo(d)}>
                      Undo
                    </button>
                  )}
                  {d.undone && canCorrectAgain(d) && (
                    <button type="button" className="adm-minibtn" disabled={busy} onClick={() => correctAgain(d)}>
                      Correct again
                    </button>
                  )}
                </li>
              );
            })}
          </ul>

          <h3 className="cf-h3">Candidates</h3>
          <ul data-testid="cf-candidates">
            {candidates.length === 0 && <li className="cf-quiet">No candidates.</li>}
            {candidates.map((c, i) => {
              const gray = c.decision != null && (c.decision.action === "dismiss" || c.decision.undone);
              const reopenedOlder = c.outcome === "flag" && isOpen(c) && i > 0;
              return (
                <li key={c.id} data-testid="cf-candidate-row" className={gray ? "cf-undone" : undefined}>
                  {c.outcome === "flag"
                    ? `${valueWords(c.fact_type, c, labelCurrency(c.statement_currency, c.flags))} · dated ${c.event_date ?? "unknown"} · `
                    : `Nothing found${c.reason ? `: ${c.reason}` : ""} · ${day(c.recorded_at)}`}
                  {c.outcome === "flag" && <FilingLink href={c.source_ref} />}
                  {c.outcome === "flag" &&
                    (c.decision ? (
                      <> · {lowerFirst(decisionWords(c.decision))}</>
                    ) : reopenedOlder ? (
                      <> · open, superseded by a newer candidate</>
                    ) : (
                      <> · open</>
                    ))}{" "}
                  {reopenedOlder && (
                    <button type="button" className="adm-minibtn" disabled={busy} onClick={() => approve(c)}>
                      Approve
                    </button>
                  )}
                </li>
              );
            })}
          </ul>

          <h3 className="cf-h3">Recent extraction attempts</h3>
          <ul data-testid="cf-attempts">
            {attempts.length === 0 && <li className="cf-quiet">No extraction attempts yet.</li>}
            {attempts.map((a) => (
              <li key={a.id} data-testid="cf-attempt-row" className="cf-quiet">
                {attemptWords(a, factType)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export function CompanyFacts({
  securityId,
  asof,
  header,
}: {
  securityId: string;
  asof: string | null;
  header?: ReactNode;
}) {
  const q = useCompanyValues(securityId);
  const m: Mutations = {
    approve: useApproveCandidate(),
    correct: useCorrectValue(),
    dismiss: useDismissCandidate(),
    undo: useUndoDecision(),
    pin: usePinValue(),
    unpin: useUnpinValue(),
  };
  const extract = useExtractNow();
  // a company's writes hold its lock, so one at a time (an extraction writes too)
  const busy = (Object.values(m) as AnyMutation[]).some((x) => x.isPending) || extract.isPending;
  const d = q.data;
  const attempts = d?.attempts ?? [];
  return (
    <div className="cf-page">
      {header}
      <div className="cf-wrap">
        <div className="cf-head">
          <Link className="adm-linkbtn" to={adminPath(asof)}>
            ← Back to Admin
          </Link>
          <h1 className="cf-title">
            {d ? (
              <>
                <span className="cf-ticker">{d.ticker ?? "(no ticker)"}</span>{" "}
                <span className="cf-name">{d.name ?? ""}</span>
              </>
            ) : (
              "Shared facts"
            )}
          </h1>
          <FactSearch asof={asof} />
        </div>
        {q.isLoading && <div className="cf-quiet">Loading…</div>}
        {q.isError && (
          <div className="adm-err" role="alert">
            {apiErrorText(q.error) ?? "Couldn't load this company."}
          </div>
        )}
        {d && (
          <>
            <ExtractNow securityId={securityId} extract={extract} busy={busy} />
            <ValueBlock securityId={securityId} v={d.shares} m={m} busy={busy} attempts={attempts} />
            <ValueBlock securityId={securityId} v={d.cash_burn} m={m} busy={busy} attempts={attempts} />
          </>
        )}
      </div>
    </div>
  );
}
