import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { useAppClock } from "../../clock";
import { fmtDate } from "../../util/format";
import { FlaggedNote, flaggedRule } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./daily-digest.css";
import {
  FRAMING,
  INGEST_TITLE,
  LIMITS,
  RECONSTRUCTED_TITLE,
  SECTIONS,
  afterNightNote,
  buyerWords,
  clockWords,
  clusterTitle,
  clusterWord,
  dayWord,
  dueWhat,
  dueWhen,
  fillWords,
  filedWords,
  foldCard,
  formingWords,
  headlineWords,
  holdWords,
  insiderEmpty,
  killTitle,
  killWords,
  lastNightCount,
  lastSessionFig,
  lineWords,
  loggedFillWords,
  nameKey,
  nightsWords,
  passedWords,
  plural,
  riskWords,
  sectionHref,
  signed,
  sinceArmFig,
  sinceFillFig,
  sizeWords,
  tapeNote,
  tapeRows,
  toneClass,
  triggerWords,
  workLines,
  type Fig,
  type SectionKey,
} from "./digestView";
import {
  useDailyDigest,
  type DailyDigestOut,
  type DigestBuyRowOut,
  type DigestCardOut,
  type DigestLineOut,
  type DigestPositionOut,
  type DigestTapeRowOut,
} from "./useDailyDigest";
import { ExternalLink } from "../../components/ExternalLink";
import { RecordStartNote } from "../RecordStartNote";

// Daily Digest (docs/dashboards/daily-digest.md): the morning page. Six sections in a fixed order, each a cut of a page
// that already exists, read by that page's own rules and worded here; the header of each opens the full page with ?asof=.
// The tape look at low energy (invariant #7): ONE accent, the Board's Armed color, on exactly three kinds of line (a card
// that entered Armed, a name newly armed, an entry window closing within 3 days); the warm tone on a card that left Armed
// and an exit-by reached; the sign tone only on a return; everything about Warming in the plain text color and everything
// about Incubating in the gray scale, at the end of its section. Quiet sentences, never badges, for a section with nothing.
// Display only: every figure, every order and the tone of every line is computed server-side.

type Select = DashboardProps["onSelectThesis"];

const SLOW =
  "Reading the last two recorded nights, the Form 4 filings since the earlier one and the closes of the armed names — the first load after a run or a basket edit can take a moment.";

function Head({ section, asof, extra }: { section: SectionKey; asof: string; extra?: ReactNode }) {
  const def = SECTIONS.find((s) => s.key === section)!;
  const [first, ...rest] = def.links;
  // the app's default as-of (F1: the server's current night) is what an absent ?asof= reads, so it is left off
  const { currentNight } = useAppClock();
  return (
    <h2 className="dd-h">
      {first ? (
        <Link className="dd-title" to={sectionHref(first.slug, asof, currentNight)} title={`Open ${first.label}`}>
          {def.title}
        </Link>
      ) : (
        <span className="dd-title">{def.title}</span>
      )}
      {extra && <span className="dd-ct">{extra}</span>}
      {rest.map((l) => (
        <Link key={l.slug} className="dd-side" to={sectionHref(l.slug, asof, currentNight)} title={`Open ${l.label}`}>
          {l.label}
        </Link>
      ))}
    </h2>
  );
}

function FigCell({ fig, extraSub, bar }: { fig: Fig; extraSub?: string; bar?: ReactNode }) {
  return (
    <>
      <span className={`dd-fig ${fig.cls}`.trim()} title={fig.title}>
        {fig.text}
      </span>
      {bar}
      {fig.sub && <span className="dd-sub">{fig.sub}</span>}
      {extraSub && <span className="dd-sub">{extraSub}</span>}
    </>
  );
}

/** Zero-centered monochrome diverging micro-bar for a return, with an optional IWM reference tick. */
function RetBar({ pct, maxAbs, benchPct }: { pct: number | null | undefined; maxAbs: number; benchPct?: number | null }) {
  if (pct === null || pct === undefined || maxAbs <= 0) return null;
  const clamped = Math.max(-maxAbs, Math.min(maxAbs, pct));
  const half = (Math.abs(clamped) / maxAbs) * 50;
  const left = clamped >= 0 ? 50 : 50 - half;
  const benchLeft =
    benchPct !== null && benchPct !== undefined
      ? 50 + (Math.max(-maxAbs, Math.min(maxAbs, benchPct)) / maxAbs) * 50
      : null;
  return (
    <span className="dd-ret-bar" aria-hidden="true">
      <span className="dd-ret-zero" />
      {half > 0.5 && <span className={`dd-ret-fill ${clamped >= 0 ? "is-up" : "is-dn"}`} style={{ left: `${left}%`, width: `${half}%` }} />}
      {benchLeft !== null && <span className="dd-ret-bench" style={{ left: `${benchLeft}%` }} />}
    </span>
  );
}

/** Quiet monochrome morning orientation strip: book state counts + jump buttons to each section. */
function OrientationStrip({ data }: { data: DailyDigestOut }) {
  const c = data.counts;
  const total = c.cards_armed + c.cards_warming + c.cards_managing + c.cards_incubating;
  const items: { id: string; label: string; count: number }[] = [
    { id: "dd-sec-last_night", label: "Last night", count: data.last_night.cards_with_change },
    { id: "dd-sec-positions", label: "Positions", count: data.positions.length },
    { id: "dd-sec-due", label: "Due", count: data.due.items.length },
    { id: "dd-sec-insider", label: "Insider", count: data.insider.rows.length },
    { id: "dd-sec-tape", label: "Tape", count: data.tape.counts.names },
    { id: "dd-sec-forming", label: "Forming", count: data.forming.warming },
  ];
  const jump = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return (
    <div className="dd-strip" data-testid="dd-strip">
      <div className="dd-strip-book">
        <span className="dd-strip-kv">
          <strong>{c.cards_armed}</strong> Armed
        </span>
        <span className="dd-strip-kv">
          <strong>{c.cards_managing}</strong> Managing
        </span>
        <span className="dd-strip-kv">
          <strong>{c.cards_warming}</strong> Warming
        </span>
        <span className="dd-strip-kv is-muted">
          <strong>{c.cards_incubating}</strong> Incubating
        </span>
        {total > 0 && (
          <span className="dd-book-bar" aria-hidden="true">
            {c.cards_armed > 0 && <span className="dd-book-seg seg-a" style={{ flexGrow: c.cards_armed }} />}
            {c.cards_managing > 0 && <span className="dd-book-seg seg-m" style={{ flexGrow: c.cards_managing }} />}
            {c.cards_warming > 0 && <span className="dd-book-seg seg-w" style={{ flexGrow: c.cards_warming }} />}
            {c.cards_incubating > 0 && <span className="dd-book-seg seg-i" style={{ flexGrow: c.cards_incubating }} />}
          </span>
        )}
      </div>
      <nav className="dd-jump" aria-label="Digest sections">
        {items.map((it) => (
          <button
            key={it.id}
            type="button"
            className={`dd-jump-btn${it.count === 0 ? " is-zero" : ""}`}
            onClick={() => jump(it.id)}
          >
            <span>{it.label}</span>
            <span className="dd-jump-n">{it.count.toLocaleString("en-US")}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}

function Marks({ reconstructed, ingestFresh }: { reconstructed: boolean; ingestFresh: boolean | null }) {
  return (
    <>
      {reconstructed && (
        <span className="dd-mark" title={RECONSTRUCTED_TITLE}>
          R
        </span>
      )}
      {ingestFresh === false && (
        <span className="dd-mark" title={INGEST_TITLE}>
          ingest not fresh
        </span>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------------------------------------
// 1. last night
// ---------------------------------------------------------------------------------------------------------

function Line({ card, line, onSelect }: { card: DigestCardOut; line: DigestLineOut; onSelect: Select }) {
  const key = nameKey(line);
  const src = line.source;
  return (
    <li className="dd-li">
      <button
        type="button"
        className={`dd-line ${toneClass(line.tone)}`.trim()}
        data-testid="dd-line"
        data-tone={line.tone ?? ""}
        title={`Open ${card.thesis_name}${line.ticker ? ` on ${line.ticker}` : ""}`}
        onClick={() => (key ? onSelect(card.thesis_id, key) : onSelect(card.thesis_id))}
      >
        <span className="dd-tk">{line.security_id ? (line.ticker ?? "—") : ""}</span>
        <span className="dd-what">{lineWords(line)}</span>
      </button>
      {src &&
        (src.url ? (
          <ExternalLink className="dd-src" href={src.url}>
            {src.source} {src.ref}
          </ExternalLink>
        ) : (
          <span className="dd-src">
            {src.source} {src.ref}
          </span>
        ))}
    </li>
  );
}

function CardBlock({ card, onSelect }: { card: DigestCardOut; onSelect: Select }) {
  // the routine nightly churn (triggers re-dated, new or gone, new risk signals, names new on watch) is ONE quiet count
  // line, closed by default; opening it shows the folded lines in place and closing it puts them back (plain state)
  const [open, setOpen] = useState(false);
  const { shown, folded, words } = foldCard(card);
  return (
    <div className={`dd-card tier-${card.tier}`} data-testid="dd-card" data-tier={card.tier}>
      <div className="dd-card-h">
        <button type="button" className="dd-cardname" title={`Open ${card.thesis_name}`} onClick={() => onSelect(card.thesis_id)}>
          {card.thesis_name}
        </button>
        <span
          className={`dd-move ${toneClass(card.headline?.tone ?? null)}`.trim()}
          data-testid="dd-move"
          data-tone={card.headline?.tone ?? ""}
        >
          {headlineWords(card)}
        </span>
        <Marks reconstructed={card.reconstructed} ingestFresh={card.ingest_fresh} />
      </div>
      {(shown.length > 0 || words) && (
        <ul className="dd-lines">
          {shown.map((ln, i) => (
            <Line key={i} card={card} line={ln} onSelect={onSelect} />
          ))}
          {words && (
            <li className="dd-li">
              <button
                type="button"
                className="dd-fold"
                data-testid="dd-fold"
                aria-expanded={open}
                title={open ? "Fold these lines back into the count" : "Show these lines"}
                onClick={() => setOpen((o) => !o)}
              >
                <span className="dd-fold-mark" aria-hidden="true">
                  {open ? "−" : "+"}
                </span>
                {words}
              </button>
            </li>
          )}
          {open &&
            folded.map((ln, i) => <Line key={`f${i}`} card={card} line={ln} onSelect={onSelect} />)}
        </ul>
      )}
    </div>
  );
}

function LastNight({ data, asof, onSelect }: { data: DailyDigestOut; asof: string; onSelect: Select }) {
  const l = data.last_night;
  const count = lastNightCount(l);
  return (
    <section id="dd-sec-last_night" className="dd-sec" aria-label="Last night" data-testid="dd-last-night">
      <Head section="last_night" asof={asof} extra={count} />
      {l.no_baseline ? (
        <p className="dd-empty">No earlier night to compare.</p>
      ) : l.cards.length === 0 ? (
        <p className="dd-empty">Nothing changed last night.</p>
      ) : (
        l.cards.map((c) => <CardBlock key={c.thesis_id} card={c} onSelect={onSelect} />)
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------
// 2. positions
// ---------------------------------------------------------------------------------------------------------

function PositionRow({ p, onSelect }: { p: DigestPositionOut; onSelect: Select }) {
  const [killOpen, setKillOpen] = useState(false);
  const logged = loggedFillWords(p);
  const sinceIwm =
    p.since_fill.status === "priced" && p.since_fill.bench_pct !== null
      ? `IWM ${signed(p.since_fill.bench_pct)}`
      : undefined;
  const holdPassed = p.hold.exit_by !== null && p.hold.sessions_left === null;
  return (
    <tr data-testid="dd-pos-row" className="dd-row">
      <td data-label="Card" className="dd-card-td">
        <button type="button" className="dd-cardname" title={`Open ${p.thesis_name}`} onClick={() => onSelect(p.thesis_id)}>
          {p.thesis_name}
        </button>
        <Marks reconstructed={p.reconstructed} ingestFresh={p.ingest_fresh} />
      </td>
      <td data-label="Name">
        {p.ticker ? (
          <button type="button" className="dd-tk dd-tk-btn" title={`Open ${p.thesis_name} on ${p.ticker}`} onClick={() => onSelect(p.thesis_id, p.ticker)}>
            {p.ticker}
          </button>
        ) : (
          <span className="dd-gray" title={p.has_position ? "the logged position names no company" : undefined}>
            {p.has_position ? "unattributed" : "—"}
          </span>
        )}
      </td>
      <td data-label="Fill">
        <span>{fillWords(p)}</span>
        {logged && <span className="dd-sub">{logged}</span>}
      </td>
      <td data-label="Since fill" className="dd-num">
        <FigCell fig={sinceFillFig(p.since_fill)} extraSub={sinceIwm} />
      </td>
      <td data-label="Last session" className="dd-num">
        <FigCell fig={lastSessionFig(p.last_session)} />
      </td>
      <td data-label="Hold clock" className={holdPassed ? "dd-em" : undefined}>
        {holdWords(p.hold)}
      </td>
      <td data-label="Risk" className={p.risk_new > 0 ? "dd-em" : undefined}>
        {riskWords(p)}
      </td>
      <td data-label="Kill criteria">
        {p.kill_criteria.length > 0 ? (
          <>
            <button
              type="button"
              className="dd-help dd-kill-btn"
              title={killTitle(p)}
              aria-expanded={killOpen}
              onClick={() => setKillOpen((o) => !o)}
            >
              {killWords(p)}
            </button>
            {killOpen && (
              <ul className="dd-kill-list" data-testid="dd-kill-list">
                {p.kill_criteria.map((k, idx) => (
                  <li key={idx}>{k}</li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <span className="dd-gray">{killWords(p)}</span>
        )}
      </td>
    </tr>
  );
}

function Positions({ data, asof, onSelect }: { data: DailyDigestOut; asof: string; onSelect: Select }) {
  const rows = data.positions;
  return (
    <section id="dd-sec-positions" className="dd-sec" aria-label="Positions" data-testid="dd-positions">
      <Head section="positions" asof={asof} extra={rows.length > 0 ? ` · ${plural(rows.length, "Managing card", "Managing cards")}` : null} />
      {rows.length === 0 ? (
        <p className="dd-empty">No position logged.</p>
      ) : (
        <div className="dd-scroll">
          <table className="dd-table">
            <thead>
              <tr>
                {["Card", "Name", "Fill", "Since fill", "Last session", "Hold clock", "Risk", "Kill criteria"].map((h, i) => (
                  <th key={h} className={i === 3 || i === 4 ? "dd-num" : undefined}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <PositionRow key={p.thesis_id} p={p} onSelect={onSelect} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------
// 3. due this week
// ---------------------------------------------------------------------------------------------------------

function Due({ data, asof, onSelect }: { data: DailyDigestOut; asof: string; onSelect: Select }) {
  const due = data.due;
  const passed = passedWords(data);
  return (
    <section id="dd-sec-due" className="dd-sec" aria-label="Due this week" data-testid="dd-due">
      <Head
        section="due"
        asof={asof}
        extra={due.items.length > 0 ? ` · ${plural(due.items.length, "item", "items")} through ${dayWord(due.end)}` : null}
      />
      {due.items.length === 0 ? (
        <p className="dd-empty">Nothing due this week.</p>
      ) : (
        <ul className="dd-lines">
          {due.items.map((i, n) => (
            <li key={n} className="dd-li">
              <button
                type="button"
                className={`dd-line dd-due-line dd-due-${i.item}${i.today ? " dd-today" : ""}`}
                data-testid="dd-due-row"
                data-kind={i.item}
                title={`Open ${i.thesis_name}`}
                onClick={() => onSelect(i.thesis_id)}
              >
                <span className="dd-when">
                  <span className="dd-due-pip" aria-hidden="true" />
                  {dueWhen(i, data.clock === "public")}
                </span>
                <span className="dd-cardcol">{i.thesis_name}</span>
                <span className="dd-what">{dueWhat(i)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {passed && (
        <p className="dd-note" data-testid="dd-passed">
          {passed}
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------
// 4. insider buys
// ---------------------------------------------------------------------------------------------------------

function BuyRow({ r, maxLogUsd, onSelect }: { r: DigestBuyRowOut; maxLogUsd: number; onSelect: Select }) {
  const [clusterOpen, setClusterOpen] = useState(false);
  const first = r.cards[0];
  const cluster = clusterWord(r);
  const sizePct =
    r.usd !== null && r.usd > 0 && maxLogUsd > 0
      ? Math.max(8, Math.min(100, (Math.log10(r.usd + 1) / maxLogUsd) * 100))
      : null;
  return (
    <li className="dd-buy" data-testid="dd-buy-row">
      <div className="dd-buy-main">
        {first ? (
          <button type="button" className="dd-tk dd-tk-btn" title={`Open ${first.thesis_name} on ${first.ticker}`} onClick={() => onSelect(first.thesis_id, first.ticker)}>
            {r.ticker}
          </button>
        ) : (
          <span className="dd-tk">{r.ticker}</span>
        )}
        <span className="dd-chips">
          {r.cards.map((c) => (
            <button key={c.thesis_id} type="button" className="dd-chip" title={`Open ${c.thesis_name}`} onClick={() => onSelect(c.thesis_id, c.ticker)}>
              {c.thesis_name}
            </button>
          ))}
        </span>
        <span className="dd-buyer">{buyerWords(r)}</span>
        <span className="dd-size-wrap">
          <span className="dd-size">{sizeWords(r)}</span>
          {sizePct !== null && (
            <span className="dd-size-bar" aria-hidden="true">
              <span className="dd-size-fill" style={{ width: `${sizePct}%` }} />
            </span>
          )}
        </span>
      </div>
      <div className="dd-buy-sub">
        <span>{filedWords(r)}</span>
        {cluster && (
          <button
            type="button"
            className="dd-word dd-cluster-btn"
            title={clusterTitle(r)}
            aria-expanded={clusterOpen}
            onClick={() => setClusterOpen((o) => !o)}
          >
            {cluster}
          </button>
        )}
        <span className={r.triggers.length > 0 ? "dd-word" : "dd-gray"}>{triggerWords(r)}</span>
        {r.url && (
          <ExternalLink className="dd-src" href={r.url}>
            {r.form} {r.accession}
          </ExternalLink>
        )}
      </div>
      {clusterOpen && r.cluster && r.cluster.others.length > 0 && (
        <ul className="dd-cluster-list" data-testid="dd-cluster-list">
          {r.cluster.others.map((o, idx) => (
            <li key={idx}>
              {o.name} · filed {fmtDate(o.filed)}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Insider({ data, asof, onSelect }: { data: DailyDigestOut; asof: string; onSelect: Select }) {
  const i = data.insider;
  const span = !i.no_baseline && i.span_start ? ` · filed since ${dayWord(i.span_start)}` : "";
  const count = i.rows.length > 0 ? ` · ${plural(i.rows.length, "purchase", "purchases")}` : "";
  const maxLogUsd = i.rows.reduce((m, r) => (r.usd !== null && r.usd > 0 ? Math.max(m, Math.log10(r.usd + 1)) : m), 0);
  return (
    <section id="dd-sec-insider" className="dd-sec" aria-label="Insider buys" data-testid="dd-insider">
      <Head section="insider" asof={asof} extra={`${span}${count}` || null} />
      {i.rows.length === 0 ? (
        <p className="dd-empty">{insiderEmpty(data)}</p>
      ) : (
        <ul className="dd-buys">
          {i.rows.map((r) => (
            <BuyRow key={r.key} r={r} maxLogUsd={maxLogUsd} onSelect={onSelect} />
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------
// 5. the tape
// ---------------------------------------------------------------------------------------------------------

function TapeRow({
  r,
  maxLast,
  maxSince,
  iwmPct,
  splitBefore,
  onSelect,
}: {
  r: DigestTapeRowOut;
  maxLast: number;
  maxSince: number;
  iwmPct: number | null;
  splitBefore: boolean;
  onSelect: Select;
}) {
  const lastPct = r.last_session.status === "priced" ? r.last_session.ret_pct : null;
  const sincePct = r.since_arm.ret_pct;
  return (
    <tr data-testid="dd-tape-row" data-cut={r.cut ?? ""} className={`dd-row${splitBefore ? " dd-tape-split" : ""}`}>
      <td data-label="Name">
        <button type="button" className="dd-tk dd-tk-btn" title={`Open ${r.thesis_name} on ${r.ticker}`} onClick={() => onSelect(r.thesis_id, r.ticker)}>
          {r.ticker}
        </button>
      </td>
      <td data-label="Card" className="dd-card-td">
        <button type="button" className="dd-cardname" title={`Open ${r.thesis_name}`} onClick={() => onSelect(r.thesis_id)}>
          {r.thesis_name}
        </button>
      </td>
      <td data-label="Last session" className="dd-num">
        <FigCell fig={lastSessionFig(r.last_session)} bar={<RetBar pct={lastPct} maxAbs={maxLast} benchPct={iwmPct} />} />
      </td>
      <td data-label="Since arm" className="dd-num">
        <FigCell fig={sinceArmFig(r)} bar={<RetBar pct={sincePct} maxAbs={maxSince} benchPct={r.since_arm.iwm_pct} />} />
      </td>
      <td data-label="Armed">
        <span>{r.arm_night ? fmtDate(r.arm_night) : "—"}</span>
        {r.arm_reconstructed && (
          <span className="dd-mark" title={RECONSTRUCTED_TITLE}>
            R
          </span>
        )}
        <span className="dd-sub" title="sessions since the arm night, then the sessions left in the entry window">
          {clockWords(r)}
        </span>
      </td>
    </tr>
  );
}

function Tape({ data, asof, onSelect }: { data: DailyDigestOut; asof: string; onSelect: Select }) {
  const [showAll, setShowAll] = useState(false);
  const tape = data.tape;
  const { shown, extra } = tapeRows(tape, showAll);
  const maxLast = shown.reduce(
    (m, r) => (r.last_session.status === "priced" && r.last_session.ret_pct !== null ? Math.max(m, Math.abs(r.last_session.ret_pct)) : m),
    tape.iwm_pct !== null ? Math.abs(tape.iwm_pct) : 0,
  );
  const maxSince = shown.reduce(
    (m, r) => (r.since_arm.ret_pct !== null ? Math.max(m, Math.abs(r.since_arm.ret_pct)) : m),
    0,
  );
  const firstFallIdx = shown.findIndex((r, idx) => idx > 0 && r.cut === "fall" && shown[idx - 1].cut !== "fall");
  return (
    <section id="dd-sec-tape" className="dd-sec" aria-label="The tape" data-testid="dd-tape">
      <Head section="tape" asof={asof} extra={tape.rows.length > 0 ? ` · ${plural(tape.counts.names, "armed name", "armed names")}` : null} />
      {tape.rows.length === 0 ? (
        <p className="dd-empty">No armed name.</p>
      ) : (
        <>
          <p className="dd-note" data-testid="dd-tape-note">
            {tapeNote(tape)}
          </p>
          {extra > 0 && (
            <label className="dd-check" title="Add the armed names between the largest rises and falls, in the same order">
              <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
              Show every armed name <span className="dd-n">{showAll ? "" : `+${extra.toLocaleString("en-US")}`}</span>
            </label>
          )}
          {shown.length === 0 ? (
            <p className="dd-empty">No armed name has a last-session return.</p>
          ) : (
            <div className="dd-scroll">
              <table className="dd-table">
                <thead>
                  <tr>
                    {["Name", "Card", "Last session", "Since arm", "Armed"].map((h, i) => (
                      <th key={h} className={i === 2 || i === 3 ? "dd-num" : undefined}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r, idx) => (
                    <TapeRow
                      key={r.key}
                      r={r}
                      maxLast={maxLast}
                      maxSince={maxSince}
                      iwmPct={tape.iwm_pct}
                      splitBefore={idx === firstFallIdx}
                      onSelect={onSelect}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------
// 6. forming
// ---------------------------------------------------------------------------------------------------------

function Forming({ data, asof, onSelect }: { data: DailyDigestOut; asof: string; onSelect: Select }) {
  const f = data.forming;
  const w = formingWords(f);
  return (
    <section id="dd-sec-forming" className="dd-sec dd-forming-sec" aria-label="Forming" data-testid="dd-forming">
      <Head section="forming" asof={asof} />
      <p className="dd-forming">
        <span>{w.warming}</span>
        {w.turned && (
          <>
            {" · "}
            <span>{w.turned}</span>{" "}
            {f.key_turned.map((k, i) => (
              <span key={k.thesis_id}>
                {i > 0 && ", "}
                <button type="button" className="dd-chip" title={`Open ${k.thesis_name}`} onClick={() => onSelect(k.thesis_id)}>
                  {k.thesis_name}
                </button>{" "}
                <span className="dd-sub">({k.labels.join(", ")})</span>
              </span>
            ))}
          </>
        )}
        {" · "}
        <span className="dd-gray">{w.incubating}</span>
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------
// the page
// ---------------------------------------------------------------------------------------------------------

export default function DailyDigest({ asof, onSelectThesis }: DashboardProps) {
  const q = useDailyDigest(asof);
  const data = q.data;

  if (q.isLoading || (!data && q.isFetching)) return <div className="dash-note">{SLOW}</div>;
  if (q.error && !data) return <div className="dash-note">Could not load the Daily Digest.</div>;
  if (!data) return <div className="dash-note">{SLOW}</div>;

  const stale = q.isFetching && data.asof !== asof;
  const after = afterNightNote(data.nights);
  return (
    <div className="dd">
      <RecordStartNote empty={data.nights.latest === null} />
      <p className="dd-framing" data-testid="dd-framing">
        {FRAMING}
      </p>
      <p className="dd-night" data-testid="dd-night">
        {nightsWords(data.nights)}
      </p>
      {after && (
        <p className="dd-after" data-testid="dd-after">
          {after}
        </p>
      )}
      <OrientationStrip data={data} />
      {(q.isFetching || stale) && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}

      <LastNight data={data} asof={asof} onSelect={onSelectThesis} />
      <Positions data={data} asof={asof} onSelect={onSelectThesis} />
      <Due data={data} asof={asof} onSelect={onSelectThesis} />
      <Insider data={data} asof={asof} onSelect={onSelectThesis} />
      <Tape data={data} asof={asof} onSelect={onSelectThesis} />
      <Forming data={data} asof={asof} onSelect={onSelectThesis} />

      <div className="dd-foot">
        {workLines(data).map((line, i) => (
          <div key={i} className="dash-note dd-work" data-testid="dd-work">
            {line}
          </div>
        ))}
        <FlaggedNote flagged={data.flagged} title={flaggedRule("their price cells read the flag's reason. They keep their lines.")} />
        <ul className="dash-note dd-limits">
          {LIMITS.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
