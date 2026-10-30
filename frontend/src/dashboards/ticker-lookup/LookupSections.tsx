import { useState, type ReactNode } from "react";

import { businessTypeLabel, supersectorLabel } from "../../util/format";
import type { DashboardProps } from "../registry";
import {
  FILING_WINDOWS,
  GROUPS,
  ROLE_GLYPH,
  authorWords,
  capTitle,
  capWords,
  compactUsd,
  figureTitle,
  figuresOf,
  fillWords,
  firedWords,
  fmtDay,
  fmtDayHeader,
  fmtFigure,
  foldEntries,
  foldWords,
  keyWords,
  labelTitle,
  notListedWords,
  placeWords,
  plural,
  ratifiedWords,
  roleTag,
  sourceLabel,
  sparkPaths,
  stateWords,
  triggerCounts,
  usdHint,
  weightWords,
} from "./lookupView";
import type {
  LookupCard,
  LookupFiling,
  LookupFilingDay,
  LookupTrigger,
  TickerLookupOut,
} from "./useTickerLookup";

// The Ticker Lookup's seven sections (docs/dashboards/ticker-lookup.md), each stating the n it rests on. Display
// only: every figure arrives computed; these draw it. Loudness marks the exception only (#7 / WB#3): the one accent
// is the "armed" word in a card's place cell, the sign tone only on signed tape figures, nothing bold but the
// ticker.

type OpenThesis = DashboardProps["onSelectThesis"];

export function Section({
  title,
  n,
  children,
}: {
  title: string;
  n?: string;
  children: ReactNode;
}) {
  return (
    <section className="tl-sec" aria-label={title}>
      <h2 className="tl-h">
        {title}
        {n && <span className="tl-n">{n}</span>}
      </h2>
      {children}
    </section>
  );
}

// --- 1. identity -------------------------------------------------------------------------------------------------

export function IdentitySection({ d }: { d: TickerLookupOut }) {
  const i = d.identity;
  return (
    <section className="tl-sec tl-identity" aria-label="Identity">
      <div className="tl-title">
        <span className="tl-ticker">{i.ticker ?? "?"}</span>
        <span className="tl-name">{i.name ?? "unnamed company"}</span>
        {i.instrument_kind === "etf" && <span className="tl-tag">ETF</span>}
      </div>
      <dl className="tl-facts">
        <div>
          <dt>CIK</dt>
          <dd>
            {i.cik && i.edgar_url ? (
              <a href={i.edgar_url} target="_blank" rel="noopener noreferrer" title="Open the company on EDGAR">
                {i.cik} ↗
              </a>
            ) : (
              "—"
            )}
          </dd>
        </div>
        <div>
          <dt>Exchange</dt>
          <dd>{i.exchange ?? "—"}</dd>
        </div>
        <div>
          <dt>Business</dt>
          <dd>
            {businessTypeLabel(i.business_type)} · {supersectorLabel(i.supersector)}
            {i.royalty ? " · royalty/streaming" : ""}
          </dd>
        </div>
        <div>
          <dt>Origin</dt>
          <dd>{i.origin ?? "—"}</dd>
        </div>
        <div>
          <dt>Market cap</dt>
          <dd title={capTitle(i.cap)} data-testid="tl-cap">
            {capWords(i.cap)}
          </dd>
        </div>
        <div>
          <dt>$vol 20d</dt>
          <dd title={i.adv_note ?? undefined}>{i.adv_usd_20d != null ? compactUsd(i.adv_usd_20d) : "n/a"}</dd>
        </div>
        {i.price_symbol && (
          <div>
            <dt>Priced under</dt>
            <dd>{i.price_symbol}</dd>
          </div>
        )}
        {i.former_tickers.length > 0 && (
          <div>
            <dt>Formerly</dt>
            <dd>{i.former_tickers.join(", ")}</dd>
          </div>
        )}
      </dl>
      {i.flag && (
        <div className="tl-flag" data-testid="tl-flag">
          Data flag on its price readings: {i.flag}. The market cap is withheld and the price figures below are grayed.
        </div>
      )}
    </section>
  );
}

// --- 2. cards ----------------------------------------------------------------------------------------------------

function CardLine({ r, onSelectThesis }: { r: LookupCard; onSelectThesis: OpenThesis }) {
  const place = placeWords(r.place);
  const many = r.links.length > 1;
  const weight = weightWords(r.links);
  const fits = r.links.filter((k) => k.thesis_fit);
  return (
    <tr data-testid="tl-card">
      <td className="tl-lead">
        <button
          type="button"
          className="tl-card"
          title={`Open ${r.thesis_name} on ${r.ticker}`}
          onClick={() => onSelectThesis(r.thesis_id, r.ticker)}
        >
          {r.thesis_name}
        </button>
      </td>
      <td>{r.links.map((k) => k.link ?? "No link").join(" · ")}</td>
      <td className="tl-quiet">
        {[r.silent, r.context_role ? roleTag(r.context_role) : null].filter(Boolean).join(" · ") || "—"}
      </td>
      <td className="tl-quiet">{weight ?? "—"}</td>
      <td className="tl-quiet">{r.signed_off ? "signed off" : "—"}</td>
      <td className="tl-why">
        {fits.length === 0
          ? "—"
          : fits.map((k, idx) => (
              <span key={idx} className="tl-fit">
                {many ? `${k.link ?? "No link"}: ` : ""}
                {k.thesis_fit} <span className="tl-by">{authorWords(k.authored_by)}</span>
              </span>
            ))}
      </td>
      <td className="tl-quiet">{stateWords(r)}</td>
      <td>
        <span className="tl-place" data-armed={place.armed}>
          {place.armed ? (
            <>
              <span className="tl-armed">armed</span>
              {place.text.slice("armed".length)}
            </>
          ) : (
            place.text
          )}
        </span>
      </td>
    </tr>
  );
}

export function CardsSection({ d, onSelectThesis }: { d: TickerLookupOut; onSelectThesis: OpenThesis }) {
  const c = d.cards;
  return (
    <Section title="Cards" n={`on ${c.rows.length} of ${plural(c.counted, "counted card", "counted cards")}`}>
      {c.rows.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-cards">
          Not on any counted card at this as-of. Its identity, tape and filings stand on their own below.
        </div>
      ) : (
        <div className="tl-scroll">
          <table className="tl-table">
            <thead>
              <tr>
                <th className="tl-lead">Card</th>
                <th>Link</th>
                <th>Part</th>
                <th>Weight</th>
                <th>Signed off</th>
                <th>Why it sits there</th>
                <th>Recorded call</th>
                <th>Place on the card</th>
              </tr>
            </thead>
            <tbody>
              {c.rows.map((r) => (
                <CardLine key={r.thesis_id} r={r} onSelectThesis={onSelectThesis} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {c.archived.length > 0 && (
        <details className="tl-fold" data-testid="tl-archived">
          <summary>also on {plural(c.archived.length, "archived card", "archived cards")}</summary>
          <ul>
            {c.archived.map((a) => (
              <li key={a.thesis_id}>{a.thesis_name}</li>
            ))}
          </ul>
        </details>
      )}
      {c.excluded.length > 0 && (
        <details className="tl-fold" data-testid="tl-excluded">
          <summary>excluded from {plural(c.excluded.length, "card", "cards")}</summary>
          <ul>
            {c.excluded.map((x) => (
              <li key={x.thesis_id}>
                {x.thesis_name}
                {x.reason ? `: ${x.reason}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Section>
  );
}

// --- 3. the record's triggers ------------------------------------------------------------------------------------

function TriggerLine({ e, onSelectThesis }: { e: LookupTrigger; onSelectThesis: OpenThesis }) {
  const role = ROLE_GLYPH[e.role];
  return (
    <tr data-testid="tl-trigger" data-live={e.live}>
      <td className="tl-num">
        {fmtDay(e.recorded_on)}
        {e.reconstructed && <span className="tl-quiet"> reconstructed</span>}
      </td>
      <td className="tl-num">{firedWords(e)}</td>
      <td>
        <button
          type="button"
          className="tl-card"
          title={`Open ${e.thesis_name} on ${e.ticker}`}
          onClick={() => onSelectThesis(e.thesis_id, e.ticker)}
        >
          {e.thesis_name}
        </button>
      </td>
      <td className="tl-glyph" title={role.label} aria-label={role.label}>
        {role.glyph}
      </td>
      <td>{e.kind.replace(/_/g, " ")}</td>
      <td className="tl-quiet">{e.grade ?? "—"}</td>
      <td className="tl-label" title={labelTitle(e)}>
        {e.label}
      </td>
      <td className="tl-quiet">{keyWords(e)}</td>
      <td className="tl-src">
        {e.sources.length === 0
          ? "—"
          : e.sources.map((s, idx) =>
              s.url ? (
                <a
                  key={idx}
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={s.detail.join("\n") || s.ref}
                >
                  {sourceLabel(s)} ↗
                </a>
              ) : (
                <span key={idx} title={s.detail.join("\n") || s.ref}>
                  {sourceLabel(s)}
                </span>
              ),
            )}
      </td>
      <td className="tl-quiet" title={e.live ? undefined : "no longer on the card in effect at the as-of"}>
        {e.live ? "live" : "—"}
      </td>
    </tr>
  );
}

export function TriggersSection({ d, onSelectThesis }: { d: TickerLookupOut; onSelectThesis: OpenThesis }) {
  const t = d.triggers;
  const tc = triggerCounts(t.events);
  return (
    <Section
      title="Triggers the record shows"
      n={`${plural(t.events.length, "row", "rows")} since ${fmtDay(t.window_start)} · ${tc.live} live`}
    >
      {t.events.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-triggers">
          No trigger or risk signal on this company in the recorded calls of the cards it sits on, since{" "}
          {fmtDay(t.window_start)}
          {t.before_window_live > 0
            ? `; ${plural(t.before_window_live, "live trigger", "live triggers")} first recorded earlier are not listed`
            : ""}
          .
        </div>
      ) : (
        <div className="tl-scroll">
          <table className="tl-table">
            <thead>
              <tr>
                <th>Recorded</th>
                <th>Fired</th>
                <th>Card</th>
                <th title="● entry trigger · ○ risk signal">Role</th>
                <th>Kind</th>
                <th>Grade</th>
                <th>Label</th>
                <th>Key</th>
                <th>Source</th>
                <th>Now</th>
              </tr>
            </thead>
            <tbody>
              {t.events.map((e, i) => (
                <TriggerLine
                  key={`${e.thesis_id}|${e.role}|${e.kind}|${e.event_date}|${i}`}
                  e={e}
                  onSelectThesis={onSelectThesis}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// --- 4. tape -----------------------------------------------------------------------------------------------------

const SPARK_W = 300;
const SPARK_H = 56;

export function TapeSection({ d }: { d: TickerLookupOut }) {
  const tape = d.tape;
  const flagged = d.identity.flag != null;
  const paths = tape.sparkline ? sparkPaths(tape.sparkline, SPARK_W, SPARK_H) : null;
  return (
    <Section
      title="Tape"
      n={`${tape.members_read - tape.members_without} of ${tape.members_read} display members read${tape.window_end ? ` · last bar ${fmtDay(tape.window_end)}` : ""}`}
    >
      {tape.figures.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-tape">
          No price tape on file at this as-of.
        </div>
      ) : (
        <div className="tl-tape">
          {GROUPS.map(({ group, label, price }) => {
            const figs = figuresOf(tape.figures, group);
            if (figs.length === 0) return null;
            return (
              <div key={group} className={`tl-group${price && flagged ? " flagged" : ""}`} data-group={group}>
                <h3 className="tl-gh">{label}</h3>
                <dl className="tl-figs">
                  {figs.map((f) => (
                    <div
                      key={f.key}
                      className={`tl-fig${f.tone ? ` ${f.tone}` : ""}${f.value == null ? " na" : ""}`}
                      title={figureTitle(f)}
                      data-testid="tl-fig"
                      data-key={f.key}
                    >
                      <dt>{f.label}</dt>
                      <dd>
                        {fmtFigure(f)}
                        {f.on && <small className="tl-on">{fmtDay(f.on)}</small>}
                        {f.value == null && f.note && <small className="tl-on">{f.note}</small>}
                      </dd>
                    </div>
                  ))}
                </dl>
                {group === "strength" && tape.leading.length > 0 && (
                  <div className="tl-mark" data-testid="tl-leading">
                    leading: relative strength at a 13-week high vs {tape.leading.join(" and ")}
                  </div>
                )}
                {group === "strength" && tape.coiling && (
                  <div className="tl-mark" data-testid="tl-coiling">
                    base coiling{tape.coiling_detail ? `: ${tape.coiling_detail}` : ""}
                  </div>
                )}
              </div>
            );
          })}
          {paths && tape.sparkline && (
            <div className={`tl-group tl-spark${flagged ? " flagged" : ""}`} data-group="path">
              <h3 className="tl-gh">Last {tape.sparkline.bars} sessions</h3>
              <svg
                viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
                width={SPARK_W}
                height={SPARK_H}
                role="img"
                aria-label={`The last ${tape.sparkline.bars} closes, indexed to 100, against IWM`}
                data-testid="tl-spark"
              >
                {paths.iwm && <path className="tl-spark-iwm" d={paths.iwm} fill="none" />}
                {paths.stock && <path className="tl-spark-stock" d={paths.stock} fill="none" />}
              </svg>
              <div className="tl-spark-cap">
                the company (bright) and IWM (dim), both indexed to 100
                {tape.sparkline.iwm_bars === 0
                  ? "; no IWM bars on file"
                  : tape.sparkline.iwm_bars < tape.sparkline.bars
                    ? `; IWM has ${tape.sparkline.iwm_bars} of ${tape.sparkline.bars} bars`
                    : ""}
                {tape.sparkline.note ? ` · ${tape.sparkline.note}` : ""}
              </div>
            </div>
          )}
        </div>
      )}
      {tape.gaps.length > 0 && (
        <ul className="tl-gaps" data-testid="tl-gaps">
          {tape.gaps.map((g) => (
            <li key={g.member}>
              No reading: {g.label} ({g.note})
            </li>
          ))}
        </ul>
      )}
      {tape.headline_notes.length > 0 && (
        <ul className="tl-gaps">
          {tape.headline_notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// --- 5. filings --------------------------------------------------------------------------------------------------

function FilingLine({
  row,
  open,
  onToggle,
}: {
  row: LookupFiling;
  open: boolean;
  onToggle: () => void;
}) {
  const hint = usdHint(row);
  const ratified = ratifiedWords(row);
  const expandable = row.detail.length > 0;
  const source = row.url?.includes("sec.gov") ? "EDGAR" : "source";
  return (
    <li className={`tl-frow${open ? " open" : ""}`} data-testid="tl-filing" data-kind={row.kind}>
      <div className="tl-fline">
        <span className="tl-form">{row.form}</span>
        <span className="tl-fmain">
          {expandable ? (
            <button type="button" className="tl-fhead" aria-expanded={open} onClick={onToggle}>
              <span className="tl-chev" aria-hidden="true">
                {open ? "▾" : "▸"}
              </span>
              {row.headline}
            </button>
          ) : (
            <span className="tl-fhead plain">{row.headline}</span>
          )}
          {row.tags.map((tag) => (
            <span key={tag} className="tl-ftag">
              {tag}
            </span>
          ))}
          {ratified && <span className="tl-ftag">{ratified}</span>}
        </span>
        <span className="tl-usd">{hint}</span>
        {row.url ? (
          <a
            className="tl-fsrc"
            href={row.url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Open ${row.form} on ${source === "EDGAR" ? "EDGAR" : "its source"}`}
          >
            {source} ↗
          </a>
        ) : (
          <span className="tl-fsrc none" />
        )}
      </div>
      {open && (
        <ul className="tl-fdetail" data-testid="tl-fdetail">
          {row.detail.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function FilingsSection({
  days,
  rowCount,
  windowN,
  onWindow,
  d,
}: {
  days: LookupFilingDay[];
  rowCount: number;
  windowN: number;
  onWindow: (n: number) => void;
  d: TickerLookupOut;
}) {
  const [showRoutine, setShowRoutine] = useState(false);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [foldOpen, setFoldOpen] = useState<ReadonlySet<string>>(new Set());
  const toggle = (set: ReadonlySet<string>, k: string, put: (s: ReadonlySet<string>) => void) => {
    const next = new Set(set);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    put(next);
  };
  const hasRoutine = days.some((x) => x.rows.some((r) => r.routine));
  const notListed = notListedWords(d.filings);
  return (
    <Section title="Filings" n={`${plural(rowCount, "row", "rows")} in the last ${windowN} days`}>
      <div className="tl-controls">
        <div className="tl-seg" role="group" aria-label="Filings window">
          <span className="tl-seg-label">Window</span>
          {FILING_WINDOWS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`tl-seg-btn${windowN === o.value ? " on" : ""}`}
              aria-pressed={windowN === o.value}
              onClick={() => onWindow(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        {hasRoutine && (
          <label
            className="tl-check"
            title="Show each routine row in place instead of one folded line per kind and day"
          >
            <input type="checkbox" checked={showRoutine} onChange={(e) => setShowRoutine(e.target.checked)} />
            Show routine rows
          </label>
        )}
      </div>
      {days.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-filings">
          No stored filing-derived fact for this company in the last {windowN} days.
        </div>
      ) : (
        <div className="tl-stream">
          {days.map((day) => (
            <section key={day.date} className="tl-day" aria-label={fmtDayHeader(day.date)}>
              <h3 className="tl-dh">
                {fmtDayHeader(day.date)} <span className="tl-n">{plural(day.rows.length, "row", "rows")}</span>
              </h3>
              <ul className="tl-flist">
                {foldEntries(day.rows, showRoutine).map((entry, idx) => {
                  const line = (row: LookupFiling, j: number) => {
                    const key = `${day.date}|${row.accession ?? row.headline}|${idx}|${j}`;
                    return <FilingLine key={key} row={row} open={open.has(key)} onToggle={() => toggle(open, key, setOpen)} />;
                  };
                  if (entry.type === "row") return line(entry.row, 0);
                  const foldKey = `${day.date}|${entry.group}`;
                  const isOpen = foldOpen.has(foldKey);
                  return (
                    <li key={`fold-${foldKey}`} className="tl-fold-item" data-fold={entry.group}>
                      <button
                        type="button"
                        className="tl-foldbtn"
                        aria-expanded={isOpen}
                        onClick={() => toggle(foldOpen, foldKey, setFoldOpen)}
                      >
                        <span className="tl-chev" aria-hidden="true">
                          {isOpen ? "▾" : "▸"}
                        </span>
                        {foldWords(entry)}
                      </button>
                      {isOpen && <ul className="tl-flist tl-folded">{entry.rows.map(line)}</ul>}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
      {notListed && <div className="dash-note tl-work">{notListed}</div>}
    </Section>
  );
}

// --- 6. catalysts ------------------------------------------------------------------------------------------------

export function CatalystsSection({ d, onSelectThesis }: { d: TickerLookupOut; onSelectThesis: OpenThesis }) {
  const facts = d.catalyst_facts;
  const cards = d.card_catalysts;
  return (
    <Section
      title="Catalysts"
      n={`${plural(cards.length, "card catalyst", "card catalysts")} · ${plural(facts.length, "company fact", "company facts")}`}
    >
      <h3 className="tl-gh">Of the cards it counts on</h3>
      {cards.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-card-catalysts">
          No catalyst listed on a card this company counts on.
        </div>
      ) : (
        <ul className="tl-clist">
          {cards.map((c, i) => (
            <li key={`${c.thesis_id}|${c.label}|${i}`} className={c.passed ? "passed" : undefined} data-testid="tl-catalyst">
              <span className="tl-num">{c.when_date ? fmtDay(c.when_date) : (c.when_label ?? "undated")}</span>
              <span className="tl-clabel">{c.label}</span>
              {c.kind && <span className="tl-quiet">{c.kind}</span>}
              {c.passed && <span className="tl-quiet">passed</span>}
              <button type="button" className="tl-card" onClick={() => onSelectThesis(c.thesis_id, d.identity.ticker)}>
                {c.thesis_name}
              </button>
            </li>
          ))}
        </ul>
      )}
      <h3 className="tl-gh">Its own catalyst facts</h3>
      {facts.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-facts">
          No catalyst fact stored for this company.
        </div>
      ) : (
        <ul className="tl-clist">
          {facts.map((f, i) => {
            const ratified = ratifiedWords(f);
            return (
              <li key={`${f.accession ?? f.headline}|${i}`} data-testid="tl-catalyst-fact">
                <span className="tl-num">{fmtDay(f.date)}</span>
                <span className="tl-clabel">{f.headline}</span>
                {ratified && <span className="tl-quiet">{ratified}</span>}
                {f.url && (
                  <a href={f.url} target="_blank" rel="noopener noreferrer">
                    source ↗
                  </a>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

// --- 7. decisions ------------------------------------------------------------------------------------------------

export function DecisionsSection({ d, onSelectThesis }: { d: TickerLookupOut; onSelectThesis: OpenThesis }) {
  const rows = d.decisions;
  return (
    <Section
      title="Your decisions"
      n={`${plural(rows.length, "row", "rows")}${d.decisions_voided > 0 ? ` · ${d.decisions_voided} voided` : ""}`}
    >
      {rows.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-decisions">
          No decision logged on this company as of {fmtDay(d.asof)}.
        </div>
      ) : (
        <div className="tl-scroll">
          <table className="tl-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Card</th>
                <th>Action</th>
                <th>Fill</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((x) => (
                <tr key={x.id} className={x.voided ? "voided" : undefined} data-testid="tl-decision" data-voided={x.voided}>
                  <td className="tl-num">{fmtDay(x.decision_date)}</td>
                  <td>
                    <button
                      type="button"
                      className="tl-card"
                      onClick={() => onSelectThesis(x.thesis_id, d.identity.ticker)}
                    >
                      {x.thesis_name}
                    </button>
                  </td>
                  <td>
                    <span className={x.voided ? "tl-struck" : undefined}>{x.action}</span>
                    {x.voided && <span className="tl-quiet"> voided</span>}
                  </td>
                  <td className="tl-num">
                    <span className={x.voided ? "tl-struck" : undefined}>{fillWords(x)}</span>
                  </td>
                  <td className="tl-why">{x.reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
