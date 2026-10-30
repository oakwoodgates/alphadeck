import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyEvent } from "react";
import { useSearchParams } from "react-router";

import { useResolveSecurities } from "../../api/hooks";
import type { DashboardProps } from "../registry";
import "./ticker-lookup.css";
import {
  CardsSection,
  CatalystsSection,
  DecisionsSection,
  FilingsSection,
  IdentitySection,
  TapeSection,
  TriggersSection,
} from "./LookupSections";
import {
  DEFAULT_FILING_DAYS,
  LIMITS,
  T_PARAM,
  countFilings,
  windowDays,
  workLines,
} from "./lookupView";
import { fetchResolve, useResolveTicker, useTickerLookup } from "./useTickerLookup";

// Ticker Lookup (docs/dashboards/ticker-lookup.md): type a ticker, see what the platform holds about that company at
// the as-of on one screen. Display only. The search box suggests as you type through the Workbench's company search;
// Enter resolves an exact ticker first, else the first suggestion; the chosen ticker rides the URL as `?t=` beside the
// shell's `?asof=`, so a lookup is a link. The page itself is one read per company (the server holds it in the shared
// dashboard cache); the filings window is the only client-side dial.

const SLOW = "Reading this company — the first load after a run or a basket edit can take a moment.";
const SUGGEST_DEBOUNCE_MS = 180;

const clean = (raw: string | null | undefined) => raw?.trim().toUpperCase() || null;

/** `value`, settled for `ms` (the suggestions fire once typing pauses). */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return settled;
}

type Match = { security_id: string; ticker: string; name?: string | null };

export default function TickerLookup({ asof, onSelectThesis }: DashboardProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const t = clean(searchParams.get(T_PARAM));
  const setT = useCallback(
    (next: string | null) =>
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev);
          if (next) params.set(T_PARAM, next);
          else params.delete(T_PARAM);
          return params;
        },
        { replace: true },
      ),
    [setSearchParams],
  );

  const [input, setInput] = useState(t ?? "");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [note, setNote] = useState<{ for: string; text: string } | null>(null);
  const [formerMiss, setFormerMiss] = useState<string | null>(null);
  const [days, setDays] = useState(DEFAULT_FILING_DAYS);
  const inputRef = useRef<HTMLInputElement>(null);

  // the box follows the URL (a link, Back, or a pick)
  useEffect(() => setInput(t ?? ""), [t]);

  // `/` focuses the box (unless a field already has the keys)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      if (el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable)) return;
      e.preventDefault();
      inputRef.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const typed = input.trim();
  const settled = useDebounced(typed, SUGGEST_DEBOUNCE_MS);
  const suggestionsQ = useResolveSecurities(open ? settled : "");
  const suggestions: Match[] = useMemo(
    () => (open && settled === typed && typed ? (suggestionsQ.data ?? []) : []),
    [open, settled, typed, suggestionsQ.data],
  );

  const pick = (ticker: string) => {
    setOpen(false);
    setActive(-1);
    setInput(clean(ticker) ?? "");
    setT(clean(ticker));
  };

  // Enter: an exact ticker first, else the first suggestion, else the typed text (the page then says so)
  const submit = async () => {
    const want = clean(typed);
    if (!want) return;
    setOpen(false);
    setActive(-1);
    const hit = await fetchResolve(want).catch(() => null);
    if (hit) return setT(clean(hit.ticker));
    const first = settled === typed ? suggestionsQ.data?.[0] : undefined;
    setT(first ? clean(first.ticker) : want);
  };

  const onKeyDown = (e: ReactKeyEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, -1));
    } else if (e.key === "Escape") {
      setOpen(false);
      setActive(-1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const chosen = active >= 0 ? suggestions[active] : undefined;
      if (chosen) pick(chosen.ticker);
      else void submit();
    }
  };

  // the company behind ?t=
  const resolved = useResolveTicker(t);
  const company = resolved.data ?? null;
  const notFound = t != null && !resolved.isLoading && !resolved.error && resolved.data === null;
  const alternatives = useResolveSecurities(notFound ? t : "");

  const searchFormer = async () => {
    if (!t) return;
    const hit = await fetchResolve(t, true).catch(() => null);
    if (hit) {
      setNote({ for: clean(hit.ticker) ?? hit.ticker, text: `${t} is a former ticker of ${hit.ticker}.` });
      setFormerMiss(null);
      setT(clean(hit.ticker));
    } else {
      setFormerMiss(t);
    }
  };

  const securityId = company?.security_id ?? null;
  const q = useTickerLookup(asof, securityId);
  const data = q.data;
  const stale = data != null && (data.identity.security_id !== securityId || data.asof !== asof);

  const windowed = useMemo(() => (data ? windowDays(data.filings.days, data.asof, days) : []), [data, days]);

  return (
    <div className="tl">
      <div className="tl-search" role="search">
        <label className="tl-seg-label" htmlFor="tl-box">
          Ticker
        </label>
        <div className="tl-boxwrap">
          <input
            id="tl-box"
            ref={inputRef}
            type="search"
            className="tl-input"
            role="combobox"
            aria-expanded={open && suggestions.length > 0}
            aria-controls="tl-suggest"
            aria-autocomplete="list"
            aria-label="Ticker or company name"
            placeholder="Type a ticker or a company name"
            autoComplete="off"
            spellCheck={false}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              setOpen(true);
              setActive(-1);
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => setOpen(false)}
            onKeyDown={onKeyDown}
          />
          {suggestions.length > 0 && (
            <ul id="tl-suggest" role="listbox" className="tl-suggest">
              {suggestions.map((s, i) => (
                <li
                  key={s.security_id}
                  role="option"
                  aria-selected={i === active}
                  className={i === active ? "on" : undefined}
                  onMouseDown={(e) => {
                    e.preventDefault(); // keep focus in the box until the pick lands
                    pick(s.ticker);
                  }}
                >
                  <span className="tl-sticker">{s.ticker}</span>
                  <span className="tl-sname">{s.name ?? ""}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <span className="tl-hint">
          <kbd>/</kbd> to focus · Enter takes an exact ticker first, else the first suggestion
        </span>
      </div>

      {t == null ? (
        <div className="dash-note" data-testid="tl-empty">
          Type a ticker above to see every card the company sits on, the record's triggers on it, its tape, its filings
          and your decisions on it, as of the dial.
        </div>
      ) : resolved.isLoading ? (
        <div className="dash-note">Finding {t}…</div>
      ) : resolved.error ? (
        <div className="dash-note">Could not look up {t}.</div>
      ) : notFound ? (
        <div className="tl-none" data-testid="tl-none">
          <div className="dash-note">No company with ticker {t}.</div>
          {(alternatives.data ?? []).length > 0 && (
            <ul className="tl-alt" aria-label="Suggestions">
              {(alternatives.data ?? []).map((s) => (
                <li key={s.security_id}>
                  <button type="button" className="tl-card" onClick={() => pick(s.ticker)}>
                    {s.ticker}
                  </button>
                  <span className="tl-sname">{s.name ?? ""}</span>
                </li>
              ))}
            </ul>
          )}
          {formerMiss === t ? (
            <div className="dash-note">No company has had ticker {t}, now or formerly.</div>
          ) : (
            <button type="button" className="tl-former" onClick={() => void searchFormer()}>
              search former tickers too
            </button>
          )}
        </div>
      ) : q.isLoading || (!data && q.isFetching) ? (
        <div className="dash-note">{SLOW}</div>
      ) : q.error || !data ? (
        <div className="dash-note">Could not load the Ticker Lookup.</div>
      ) : (
        <>
          {note && note.for === t && <div className="dash-note tl-work">{note.text}</div>}
          {stale && <div className="dash-note">{q.isFetching ? SLOW : "updating…"}</div>}
          <IdentitySection d={data} />
          <CardsSection d={data} onSelectThesis={onSelectThesis} />
          <TriggersSection d={data} onSelectThesis={onSelectThesis} />
          <TapeSection d={data} />
          <FilingsSection
            d={data}
            days={windowed}
            rowCount={countFilings(windowed)}
            windowN={days}
            onWindow={setDays}
          />
          <CatalystsSection d={data} onSelectThesis={onSelectThesis} />
          <DecisionsSection d={data} onSelectThesis={onSelectThesis} />
          {workLines(data, countFilings(windowed), days).map((line, i) => (
            <div key={i} className="dash-note tl-work">
              {line}
            </div>
          ))}
          <ul className="dash-note tl-limits">
            {LIMITS.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
