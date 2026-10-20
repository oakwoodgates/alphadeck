import type {
  AdminInsiderAmendmentOut,
  AdminInsiderAmendmentsOut,
  AdminInsiderOriginalOut,
  AdminInsiderTxnOut,
} from "../api/hooks";
import {
  useApplyAmendment,
  useInsiderAmendments,
  useKeepOriginal,
  useLinkAmendment,
  useRevokeAmendmentDecision,
} from "../api/hooks";
import { errText } from "../workbench/format";

type Decision = NonNullable<AdminInsiderAmendmentOut["decisions"]>[number];
type Original = Omit<AdminInsiderOriginalOut, "rows"> & { rows: AdminInsiderTxnOut[] };

/** One wire row with every list the server defaults filled in (the generated type marks them optional). */
type Amendment = Omit<
  AdminInsiderAmendmentOut,
  | "owners"
  | "footnotes"
  | "suppressing"
  | "candidate_accessions"
  | "amendment_rows"
  | "originals"
  | "decisions"
  | "in_window"
> & {
  in_window: string[];
  owners: string[];
  footnotes: string[];
  suppressing: string[];
  candidate_accessions: string[];
  amendment_rows: AdminInsiderTxnOut[];
  originals: Original[];
  decisions: Decision[];
};

const norm = (a: AdminInsiderAmendmentOut): Amendment => ({
  ...a,
  in_window: a.in_window ?? [],
  owners: a.owners ?? [],
  footnotes: a.footnotes ?? [],
  suppressing: a.suppressing ?? [],
  candidate_accessions: a.candidate_accessions ?? [],
  amendment_rows: a.amendment_rows ?? [],
  originals: (a.originals ?? []).map((o) => ({ ...o, rows: o.rows ?? [] })),
  decisions: a.decisions ?? [],
});

const bucket = (
  d: AdminInsiderAmendmentsOut,
  k: "pending" | "decided" | "auto" | "applied" | "inert",
) =>
  (d[k] ?? []).map(norm);

/** What each disposition means, in the operator's words (backend: domain/insider_amendments.py). */
const DISPOSITION: Record<string, string> = {
  void: "VOID — the amendment reports no transactions",
  duplicates: "DUPLICATE filings — the earliest stands, the rest are copies",
  partial: "PARTIAL restatement — fewer rows than the original",
  consolidation: "CONSOLIDATION — the original's rows merged into fewer",
  addition: "ADDITION — more rows than the original",
  restatement: "RESTATEMENT — rows that do not pair 1:1 with the original's",
  ambiguous: "AMBIGUOUS — more than one original it could amend",
  field_change: "FIELD CHANGE — Table I unchanged, but the owner's role / name or the 10b5-1 flag on a buy or sale changed",
  correction: "CORRECTION — 1:1 rows, applied automatically",
  clerical: "clerical — Table I unchanged",
  no_rows: "no Table I rows on either side",
  unlinked: "no stored original found",
};

const SUPPRESSES_WHILE_PENDING = new Set(["void", "duplicates"]);

const money = (v: number | null | undefined) =>
  v == null ? "—" : `$${Math.round(v).toLocaleString("en-US")}`;
const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("en-US"));

function TxnLines({ rows }: { rows: AdminInsiderTxnOut[] }) {
  if (!rows.length) return <div className="adm-sub">no Table I rows</div>;
  return (
    <ul className="adm-problems adm-quietrows">
      {rows.map((r, i) => (
        <li key={`${r.seq ?? i}`}>
          <b>{r.txn_code ?? "?"}</b> {r.txn_date ?? "—"} · {num(r.shares)} @ {r.price == null ? "—" : `$${r.price}`} ={" "}
          {money(r.usd)}
          {r.insider_name ? <span className="adm-sub"> ({r.insider_name})</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** The evidence: the amendment's own words, and its rows beside each original's. */
function Evidence({ a }: { a: Amendment }) {
  return (
    <div className="adm-sub" data-testid="adm-amend-evidence">
      {a.remarks ? <div>Remarks: “{a.remarks}”</div> : null}
      {a.footnotes.map((f, i) => (
        <div key={i}>Footnote: “{f}”</div>
      ))}
      <div>
        This 4/A ({a.accession}){a.derivative_count ? `, plus ${a.derivative_count} Table II row(s)` : ""}:
      </div>
      <TxnLines rows={a.amendment_rows} />
      {a.originals.map((o) => (
        <div key={o.accession}>
          <div>
            {o.role === "voided" ? "Duplicate" : o.role === "candidate" ? "Candidate" : "Original"} {o.accession}
            {a.suppressing.includes(o.accession) ? " — suppressed now" : ""}:
          </div>
          <TxnLines rows={o.rows} />
        </div>
      ))}
    </div>
  );
}

/** `owed` = the row sits in the decision queue — only there does "inside today's window" mark the exception
 *  (honest loudness); a decided amendment in a window is already settled. */
function Header({ a, owed = false }: { a: Amendment; owed?: boolean }) {
  return (
    <div>
      <b>{a.ticker ?? a.security_id}</b> — Form 4/A{" "}
      {a.filing_url ? (
        <a className="adm-link" href={a.filing_url} target="_blank" rel="noreferrer">
          {a.accession}
        </a>
      ) : (
        a.accession
      )}{" "}
      filed {a.filed ?? "—"} by {a.owners.join(", ") || "—"} · {DISPOSITION[a.disposition] ?? a.disposition}
      {a.touches_buy ? " · touches a code-P buy" : ""}
      {owed && a.in_window.length > 0 && (
        <span className="adm-loud" data-testid="adm-amend-in-window">
          {" "}
          · in today's {a.in_window.join("/")} signal window — can move tonight's call
        </span>
      )}
    </div>
  );
}

/** The "Insider amendments" panel (R8-A): every stored Form 4/A, LOUD only where a decision is owed — an
 *  amendment whose effect changes a buy or sale a call signal reads (a void or a duplicates set SUPPRESSES
 *  its original meanwhile, so a cancelled buy never fires; a partial / consolidation / addition /
 *  restatement / field change has no effect until applied), or an ambiguous link (pick the original). The
 *  ones inside today's signal windows sort first and say so (the server orders them by the detectors' own
 *  horizons). An amendment that changes NO signal row is applied by the system (basis
 *  `system:no-signal-effect`) and listed quietly as "applied — no signal effect" with its Undo, which
 *  returns it to the queue. The auto-applied 1:1 corrections, the decided ones and the inert ones are QUIET
 *  lists that open too (still visible, never dropped — #9/WB#2). Every action has its Undo beside it (WB#1).
 *  Renders nothing when no amendment is stored (honest loudness). */
export function InsiderAmendments() {
  const q = useInsiderAmendments();
  const apply = useApplyAmendment();
  const keep = useKeepOriginal();
  const link = useLinkAmendment();
  const revoke = useRevokeAmendmentDecision();
  const busy = apply.isPending || keep.isPending || link.isPending || revoke.isPending;
  const failed = [apply, keep, link, revoke].find((m) => m.isError);

  if (q.isError) {
    return (
      <section className="adm-card" data-testid="adm-insider-amendments">
        <div className="adm-h">Insider amendments</div>
        <div className="adm-err">{errText(q.error)}</div>
      </section>
    );
  }
  if (!q.data || !q.data.total) return null;
  const data = {
    pending: bucket(q.data, "pending"),
    decided: bucket(q.data, "decided"),
    auto: bucket(q.data, "auto"),
    applied: bucket(q.data, "applied"),
    inert: bucket(q.data, "inert"),
  };
  const ref = (a: Amendment) => ({ securityId: a.security_id, accession: a.accession });

  return (
    <section className="adm-card" data-testid="adm-insider-amendments">
      <div className="adm-h">Insider amendments</div>

      {data.pending.length > 0 && (
        <div className="adm-stale" data-testid="adm-amend-pending">
          <div className="adm-line adm-loud">
            <b>{data.pending.length}</b> Form 4/A amendment(s) owed your decision
          </div>
          <ul className="adm-repairs">
            {data.pending.map((a) => {
              const linkDecision = a.decisions.find((d) => d.standing && d.action === "link");
              const ambiguous = a.disposition === "ambiguous" && !linkDecision;
              return (
                <li key={`${a.security_id}:${a.accession}`} data-testid="adm-amend-row">
                  <Header a={a} owed />
                  <div className="adm-sub">
                    {a.suppressing.length
                      ? `Suppressing ${a.suppressing.join(", ")} from every call until you decide.`
                      : ambiguous
                        ? "Nothing is suppressed until you pick the original it amends."
                        : "No effect until you apply it."}
                    {a.why ? ` ${a.why}.` : ""}
                  </div>
                  <Evidence a={a} />
                  <div className="adm-repair-act">
                    {ambiguous ? (
                      a.candidate_accessions.map((c) => (
                        <button
                          key={c}
                          type="button"
                          className="adm-minibtn"
                          disabled={busy}
                          onClick={() => link.mutate({ ...ref(a), target: c })}
                        >
                          Link to {c}
                        </button>
                      ))
                    ) : (
                      <>
                        {linkDecision && (
                          <>
                            <span className="adm-sub">linked by you to {linkDecision.target_accession}</span>
                            <button
                              type="button"
                              className="adm-minibtn"
                              disabled={busy}
                              onClick={() => revoke.mutate({ decisionId: linkDecision.id })}
                            >
                              Undo link
                            </button>
                          </>
                        )}
                        <button
                          type="button"
                          className="adm-minibtn primary"
                          disabled={busy}
                          onClick={() => apply.mutate(ref(a))}
                        >
                          {SUPPRESSES_WHILE_PENDING.has(a.disposition) ? "Ratify void" : "Apply amendment"}
                        </button>
                        <button
                          type="button"
                          className="adm-minibtn"
                          disabled={busy}
                          onClick={() => keep.mutate(ref(a))}
                        >
                          Keep original
                        </button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          <div className="adm-sub">
            The nightly Form 4 leg stored each amendment as filed and linked it to the original it names. A
            void suppresses its original while it waits; Keep original lifts that, and every decision has an
            Undo.
          </div>
        </div>
      )}

      {data.decided.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-amend-decided">
          <summary className="adm-line adm-quiet">{data.decided.length} decided by you (each undoable)</summary>
          <ul className="adm-problems adm-quietrows">
            {data.decided.map((a) => (
              <li key={`${a.security_id}:${a.accession}`}>
                <Header a={a} />
                <span className="adm-sub">
                  {a.status === "kept" ? "original kept" : "applied"}
                  {a.suppressing.length ? ` · suppressing ${a.suppressing.join(", ")}` : ""}
                </span>{" "}
                {a.undo_id && (
                  <button
                    type="button"
                    className="adm-minibtn"
                    disabled={busy}
                    onClick={() => revoke.mutate({ decisionId: a.undo_id! })}
                  >
                    Undo
                  </button>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      {data.auto.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-amend-auto">
          <summary className="adm-line adm-quiet">
            {data.auto.length} applied — no signal effect (resolved by the system, never paged; each undoable)
          </summary>
          <ul className="adm-problems adm-quietrows">
            {data.auto.map((a) => (
              <li key={`${a.security_id}:${a.accession}`} data-testid="adm-amend-auto-row">
                <Header a={a} />
                <span className="adm-sub">
                  applied by the system — it changes no buy or sale a call signal reads
                  {a.suppressing.length ? ` · suppressing ${a.suppressing.join(", ")}` : ""}
                </span>{" "}
                {a.undo_id && (
                  <button
                    type="button"
                    className="adm-minibtn"
                    disabled={busy}
                    onClick={() => revoke.mutate({ decisionId: a.undo_id! })}
                  >
                    Undo
                  </button>
                )}
                <Evidence a={a} />
              </li>
            ))}
          </ul>
        </details>
      )}

      {data.applied.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-amend-applied">
          <summary className="adm-line adm-quiet">
            {data.applied.length} correction(s) applied automatically (1:1 rows — never paged)
          </summary>
          <ul className="adm-problems adm-quietrows">
            {data.applied.map((a) => (
              <li key={`${a.security_id}:${a.accession}`}>
                <Header a={a} />
                <Evidence a={a} />
                <button
                  type="button"
                  className="adm-minibtn"
                  disabled={busy}
                  onClick={() => keep.mutate(ref(a))}
                >
                  Keep original
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}

      {data.inert.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-amend-inert">
          <summary className="adm-line adm-quiet">
            {data.inert.length} with nothing to apply (clerical, no Table I rows, or no stored original)
          </summary>
          <ul className="adm-problems adm-quietrows">
            {data.inert.map((a) => (
              <li key={`${a.security_id}:${a.accession}`}>
                <Header a={a} />
              </li>
            ))}
          </ul>
        </details>
      )}

      {failed && <div className="adm-err">{errText(failed.error)}</div>}
    </section>
  );
}
