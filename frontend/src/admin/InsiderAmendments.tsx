import type {
  AdminInsiderAmendmentOut,
  AdminInsiderAmendmentsOut,
  AdminInsiderOriginalOut,
  AdminInsiderTxnOut,
} from "../api/hooks";
import {
  useAddToOriginal,
  useApplyAmendment,
  useInsiderAmendments,
  useKeepOriginal,
  useLinkAmendment,
  useRevokeAmendmentDecision,
  useUndoSystemPick,
} from "../api/hooks";
import { errText } from "../workbench/format";
import { actionLine, filerSentence, pickSentence } from "./amendmentWords";

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
  | "kept_rows"
> & {
  in_window: string[];
  kept_rows: string[];
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
  kept_rows: a.kept_rows ?? [],
  owners: a.owners ?? [],
  footnotes: a.footnotes ?? [],
  suppressing: a.suppressing ?? [],
  candidate_accessions: a.candidate_accessions ?? [],
  amendment_rows: a.amendment_rows ?? [],
  originals: (a.originals ?? []).map((o) => ({ ...o, rows: o.rows ?? [] })),
  decisions: a.decisions ?? [],
});

type BucketKey = "owed" | "auto" | "decided" | "historical" | "queued" | "inert";

const bucket = (d: AdminInsiderAmendmentsOut, k: BucketKey) => (d[k] ?? []).map(norm);

/** What each disposition IS, in plain words (backend: domain/insider_amendments.py) — the card's headline. */
const WHAT: Record<string, string> = {
  void: "a correction that reports no trades",
  duplicates: "the same trades filed more than once",
  partial: "a correction that re-reports fewer trades than the original",
  consolidation: "a correction that merges the original's trades into fewer lines",
  addition: "a correction that reports more trades than the original",
  restatement: "a correction that re-reports the trades differently",
  ambiguous: "a correction that could belong to more than one Form 4",
  field_change: "a correction to the owner's title or name, or the trading-plan flag, on a buy or sale",
  correction: "a correction of the original, trade for trade",
  clerical: "a correction that changes none of the trades",
  no_rows: "a correction with no trades on either side",
  unlinked: "a correction whose original Form 4 is not stored",
};

/** The legal label, kept for the secondary filing line (backend: domain/insider_amendments.py). */
const DISPOSITION: Record<string, string> = {
  void: "void",
  duplicates: "duplicates",
  partial: "partial",
  consolidation: "consolidation",
  addition: "addition",
  restatement: "restatement",
  ambiguous: "ambiguous",
  field_change: "field change",
  correction: "correction",
  clerical: "clerical",
  no_rows: "no rows",
  unlinked: "unlinked",
};

/** Why the system applied it, per basis (backend: SYSTEM_BASES in domain/insider_amendments.py). */
const SYSTEM_WHY: Record<string, string> = {
  "system:filer-correction": "applied by the system — the filer's own amendment, resolved to the Form 4 it amends",
  "system:no-signal-effect": "applied by the system — it changes no buy or sale a call signal reads",
  "system:no-retraction":
    "kept as filed by the system — this zero-row 4/A retracts nothing (it fixes holdings, Table II or a power of attorney); the original stands",
};

/** How the filer's own words settled it (backend: match_rows / void_reading). */
const READING: Record<string, string> = {
  fix: "the filer's words FIX the trade it re-reports",
  add: "the filer's words ADD a trade it left out",
  retract: "the filer's words retract the single trade on the original",
};

const SUPPRESSES_WHILE_PENDING = new Set(["void", "duplicates"]);

const money = (v: number | null | undefined) =>
  v == null ? "—" : `$${Math.round(v).toLocaleString("en-US")}`;
const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("en-US"));

/** `standing` = the seqs of an original's rows this 4/A does not account for (R8-B's row match) — they stand. */
function TxnLines({ rows, standing }: { rows: AdminInsiderTxnOut[]; standing?: Set<number> }) {
  if (!rows.length) return <div className="adm-sub">no Table I rows</div>;
  return (
    <ul className="adm-problems adm-quietrows">
      {rows.map((r, i) => (
        <li key={`${r.seq ?? i}`}>
          <b>{r.txn_code ?? "?"}</b> {r.txn_date ?? "—"} · {num(r.shares)} @ {r.price == null ? "—" : `$${r.price}`} ={" "}
          {money(r.usd)}
          {r.insider_name ? <span className="adm-sub"> ({r.insider_name})</span> : null}
          {r.seq != null && standing?.has(r.seq) ? (
            <span className="adm-sub" data-testid="adm-amend-row-stands">
              {" "}
              — stands (this 4/A does not account for it)
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** The seqs of `accession`'s rows the amendment does not account for (`kept_rows` keys are "<accession>#<seq>"). */
const standingOf = (a: Amendment, accession: string) =>
  new Set(
    a.kept_rows
      .filter((k) => k.startsWith(`${accession}#`))
      .map((k) => Number(k.slice(accession.length + 1))),
  );

/** The evidence: the amendment's own words, and its rows beside each original's. */
function Evidence({ a }: { a: Amendment }) {
  return (
    <div className="adm-sub" data-testid="adm-amend-evidence">
      {a.reading && READING[a.reading] && a.passage ? (
        <div data-testid="adm-amend-passage">
          Read as: {READING[a.reading]} — “{a.passage}”
        </div>
      ) : null}
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
            {o.filed ? ` (filed ${o.filed})` : ""}
            {a.suppressing.includes(o.accession) ? " — suppressed now" : ""}:
          </div>
          <TxnLines rows={o.rows} standing={standingOf(a, o.accession)} />
        </div>
      ))}
    </div>
  );
}

/** `owed` = the row sits in the decision queue — only there does "inside today's window" mark the exception
 *  (honest loudness); a settled amendment in a window needs nothing from the operator. */
function Header({ a, owed = false }: { a: Amendment; owed?: boolean }) {
  return (
    <div>
      <b>{a.ticker ?? a.security_id}</b> — {WHAT[a.disposition] ?? a.disposition}
      {owed && a.in_window.length > 0 && (
        <span className="adm-loud" data-testid="adm-amend-in-window">
          {" "}
          · in today's {a.in_window.join("/")} signal window — can move tonight's call
        </span>
      )}
    </div>
  );
}

/** What the filer did, first — "This changes no call." BEFORE it when no answer can (the review line's test). */
function Plain({ a, withPick = true }: { a: Amendment; withPick?: boolean }) {
  const pick = withPick ? pickSentence(a) : null;
  return (
    <div className="adm-plain" data-testid="adm-amend-plain">
      {a.changes_no_call ? (
        <span className="adm-plain-nocall" data-testid="adm-amend-no-call">
          This changes no call.{" "}
        </span>
      ) : null}
      <span data-testid="adm-amend-sentence">{filerSentence(a)}</span>
      {pick ? (
        <span data-testid="adm-amend-pick">
          {" "}
          {pick}
        </span>
      ) : null}
    </div>
  );
}

/** The accession numbers and the legal label — available, secondary. */
function FilingRef({ a }: { a: Amendment }) {
  return (
    <div className="adm-filing-ref" data-testid="adm-amend-ref">
      Form 4/A{" "}
      {a.filing_url ? (
        <a className="adm-link" href={a.filing_url} target="_blank" rel="noreferrer">
          {a.accession}
        </a>
      ) : (
        a.accession
      )}{" "}
      · filed {a.filed ?? "—"} by {a.owners.join(", ") || "—"} · {DISPOSITION[a.disposition] ?? a.disposition}
      {a.touches_buy ? " · touches a code-P buy" : ""}
    </div>
  );
}

/** The "Insider amendments" panel (R8-A / R8-B), in PLAIN ENGLISH (2026-10-24). A Form 4/A the linker resolves to
 *  the Form 4 it amends is the filer's own legal correction: the SYSTEM resolves it — applies it, replacing only
 *  the original rows it accounts for, or keeps the original of a zero-row 4/A that retracts nothing — and, for an
 *  ambiguous one whose every trade matches ONE candidate by shares and price, picks that original (rule 2b) — and
 *  lists it quietly as "resolved automatically" with its exact Undo, never paged. LOUD only where the operator
 *  is truly owed a decision and a live call or an edition page reads the rows it touches. Each card says, first,
 *  whether any answer can change a call ("This changes no call."), then what the filer did (who, bought or sold,
 *  shares, price, date, dollars), then one line per button on what it does to the calls; the accession numbers
 *  ride a secondary line. Every quiet list opens (still visible, never dropped — #9/WB#2) and every action has
 *  its inverse beside it (WB#1). Renders nothing when no amendment is stored. */
export function InsiderAmendments() {
  const q = useInsiderAmendments();
  const apply = useApplyAmendment();
  const keep = useKeepOriginal();
  const add = useAddToOriginal();
  const link = useLinkAmendment();
  const revoke = useRevokeAmendmentDecision();
  const undoPick = useUndoSystemPick();
  const busy =
    apply.isPending || keep.isPending || add.isPending || link.isPending || revoke.isPending || undoPick.isPending;
  const failed = [apply, keep, add, link, revoke, undoPick].find((m) => m.isError);

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
    owed: bucket(q.data, "owed"),
    auto: bucket(q.data, "auto"),
    decided: bucket(q.data, "decided"),
    historical: bucket(q.data, "historical"),
    queued: bucket(q.data, "queued"),
    inert: bucket(q.data, "inert"),
  };
  const pinned = q.data.review_horizon_days;
  const edges = q.data.review_edges ?? {};
  const ref = (a: Amendment) => ({ securityId: a.security_id, accession: a.accession });
  const zeroRow = (a: Amendment) => SUPPRESSES_WHILE_PENDING.has(a.disposition);

  /** One button and its line: what it is, and what it does to the calls. */
  const act = (a: Amendment, key: string, label: string, onClick: () => void, opts: { primary?: boolean; target?: string } = {}) => (
    <div className="adm-act" key={`${key}:${opts.target ?? ""}`}>
      <button type="button" className={`adm-minibtn${opts.primary ? " primary" : ""}`} disabled={busy} onClick={onClick}>
        {label}
      </button>
      <span className="adm-act-line" data-testid="adm-amend-effect">
        {actionLine(a, key, opts.target)}
      </span>
    </div>
  );
  /** The operator's three verdicts on an amendment that is theirs (Add only where it has rows to add). */
  const verdictButtons = (a: Amendment) => (
    <>
      {act(a, "apply", "Use the correction", () => apply.mutate(ref(a)), { primary: true })}
      {act(a, "keep", "Keep the original", () => keep.mutate(ref(a)))}
      {!zeroRow(a) && act(a, "add", "Add to the original", () => add.mutate(ref(a)))}
    </>
  );
  const linkButtons = (a: Amendment) =>
    a.candidate_accessions.map((c) => act(a, "link", `Link to ${c}`, () => link.mutate({ ...ref(a), target: c }), { target: c }));
  const undoPickButton = (a: Amendment) =>
    a.pick?.standing ? act(a, "undo_pick", "Undo the system's pick", () => undoPick.mutate(ref(a))) : null;

  const edgeText =
    pinned != null
      ? `older than the ${pinned}-day review horizon`
      : edges.P || edges.S
        ? `older than any live call or edition page reads (buys before ${edges.P ?? "—"}, sales before ${edges.S ?? "—"}${
            q.data.review_anchor ? `: the earliest edition night, ${q.data.review_anchor}, less each signal's read window` : ": today's signal windows"
          })`
        : "older than any call reads";

  return (
    <section className="adm-card" data-testid="adm-insider-amendments">
      <div className="adm-h">Insider amendments</div>

      {data.owed.length > 0 && (
        <div className="adm-stale" data-testid="adm-amend-owed">
          <div className="adm-line adm-loud">
            <b>{data.owed.length}</b> Form 4/A amendment(s) owed your decision
          </div>
          <ul className="adm-repairs">
            {data.owed.map((a) => {
              const linkDecision = a.decisions.find((d) => d.standing && d.action === "link");
              const ambiguous = a.disposition === "ambiguous" && !linkDecision;
              const byYou = linkDecision && !linkDecision.basis.startsWith("system:");
              return (
                <li key={`${a.security_id}:${a.accession}`} data-testid="adm-amend-row">
                  <Header a={a} owed />
                  <Plain a={a} />
                  <div className="adm-sub">
                    {ambiguous
                      ? "Several Form 4s could be the one this amends, so the system cannot apply it — pick the original. Nothing changes until you do."
                      : a.unresolved
                        ? "A buy or sale it re-reports matches no original row, and the filer's words neither fix nor add — restated, or an added trade? Use the correction if it restates the original, Add to the original if it reports a new trade, Keep the original if neither. The original stands until you decide."
                        : a.held
                          ? "The filer's words retract a trade, but the original holds several — which one is your call. The original stands until you decide."
                          : a.suppressing.length
                            ? `Suppressing ${a.suppressing.join(", ")} from every call until you decide.`
                            : "No effect until you decide."}
                    {a.why ? ` ${a.why}.` : ""}
                  </div>
                  <div className="adm-acts">
                    {ambiguous ? (
                      linkButtons(a)
                    ) : (
                      <>
                        {byYou && (
                          <div className="adm-act">
                            <span className="adm-sub">linked by you to {linkDecision.target_accession}</span>
                            <button
                              type="button"
                              className="adm-minibtn"
                              disabled={busy}
                              onClick={() => revoke.mutate({ decisionId: linkDecision.id })}
                            >
                              Undo link
                            </button>
                          </div>
                        )}
                        {verdictButtons(a)}
                        {undoPickButton(a)}
                      </>
                    )}
                  </div>
                  <FilingRef a={a} />
                  <Evidence a={a} />
                </li>
              );
            })}
          </ul>
          <div className="adm-sub">
            Each one touches a buy or sale a live call or an edition page reads; every decision has an Undo. A 4/A the
            system applies replaces only the rows it accounts for — a buy or sale it never mentions stands.
          </div>
        </div>
      )}

      {data.auto.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-amend-auto">
          <summary className="adm-line adm-quiet">
            {data.auto.length} resolved automatically — the filer's own correction applied, its original picked, or a
            zero-row 4/A kept as filed (never paged; each undoable)
          </summary>
          <ul className="adm-problems adm-quietrows">
            {data.auto.map((a) => (
              <li key={`${a.security_id}:${a.accession}`} data-testid="adm-amend-auto-row">
                <Header a={a} />
                <Plain a={a} />
                <span className="adm-sub">
                  {(a.system_basis && SYSTEM_WHY[a.system_basis]) ??
                    (a.pick?.standing
                      ? "the system picked the Form 4 it amends; there is nothing to apply"
                      : "applied by the system — a 1:1 correction, applied by its link")}
                  {a.suppressing.length ? ` · suppressing ${a.suppressing.join(", ")}` : ""}
                </span>
                <div className="adm-acts">
                  {a.system_basis === "system:no-retraction"
                    ? act(a, "apply", "Use the correction", () => apply.mutate(ref(a)))
                    : // Keep the original is the Undo of an application — never offered where nothing applies
                      // (a system pick against an identical filing: its Undo is the pick's own)
                      (!a.actions || a.actions.some((x) => x.action === "keep")) &&
                      act(a, "keep", "Keep the original", () => keep.mutate(ref(a)))}
                  {undoPickButton(a)}
                </div>
                <FilingRef a={a} />
                <Evidence a={a} />
              </li>
            ))}
          </ul>
        </details>
      )}

      {data.decided.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-amend-decided">
          <summary className="adm-line adm-quiet">{data.decided.length} decided by you (each undoable)</summary>
          <ul className="adm-problems adm-quietrows">
            {data.decided.map((a) => {
              const overSystem = a.decisions.some((d) => d.standing && d.basis.startsWith("system:"));
              const added = a.decisions.some((d) => d.standing && d.basis === "operator:add");
              return (
                <li key={`${a.security_id}:${a.accession}`}>
                  <Header a={a} />
                  <Plain a={a} withPick={false} />
                  <span className="adm-sub">
                    {a.status === "kept"
                      ? "original kept by you"
                      : added
                        ? "added to the original by you"
                        : "correction used by you"}
                    {overSystem ? " — Undo restores the system's resolution" : ""}
                    {a.suppressing.length ? ` · suppressing ${a.suppressing.join(", ")}` : ""}
                  </span>
                  {a.undo_id && <div className="adm-acts">{act(a, "undo", "Undo", () => revoke.mutate({ decisionId: a.undo_id! }))}</div>}
                  <FilingRef a={a} />
                </li>
              );
            })}
          </ul>
        </details>
      )}

      {data.historical.length > 0 && (
        <details className="adm-quietlist adm-grayed" data-testid="adm-amend-historical">
          <summary className="adm-line adm-quiet">
            {data.historical.length} unresolved — historical (no call reads these rows)
          </summary>
          <div className="adm-sub">
            The system could not resolve these (several possible originals, the filer's words neither fixing nor
            adding, or retraction words against several trades), but every buy or sale they touch is {edgeText}, or
            they touch none. The originals stand; you can still decide any one.
          </div>
          <ul className="adm-problems adm-quietrows">
            {data.historical.map((a) => (
              <li key={`${a.security_id}:${a.accession}`} data-testid="adm-amend-historical-row">
                <Header a={a} />
                <Plain a={a} />
                <div className="adm-acts">{a.disposition === "ambiguous" ? linkButtons(a) : verdictButtons(a)}</div>
                <FilingRef a={a} />
              </li>
            ))}
          </ul>
        </details>
      )}

      {data.queued.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-amend-queued">
          <summary className="adm-line adm-quiet">
            {data.queued.length} awaiting the system's application at the next nightly pass (a void suppresses its
            original meanwhile)
          </summary>
          <ul className="adm-problems adm-quietrows">
            {data.queued.map((a) => (
              <li key={`${a.security_id}:${a.accession}`}>
                <Header a={a} />
                <FilingRef a={a} />
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
                <FilingRef a={a} />
              </li>
            ))}
          </ul>
        </details>
      )}

      {failed && <div className="adm-err">{errText(failed.error)}</div>}
    </section>
  );
}
