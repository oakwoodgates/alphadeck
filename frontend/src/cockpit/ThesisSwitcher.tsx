import { QueryClientContext } from "@tanstack/react-query";
import { useContext, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { CallCardResponse, ThesisSummary } from "../api/hooks";
import { accentVar, STATE_CLASS, STATE_LABEL, tickerLabel } from "../util/format";

/** Threshold above which the switcher menu shows its filter input (WB#3: only render controls that discriminate). */
const SEARCH_MIN_THESES = 6;

interface Props {
  thesisId: string;
  currentName: string;
  currentTicker?: string | null;
  currentBasketSize?: number | null;
  stateClass: string;
  asof: string;
  theses: readonly ThesisSummary[];
  onSwitch: (thesisId: string) => void;
}

/** Top-bar thesis switcher: a searchable dropdown menu + quiet prev/next steppers for moving between
 *  cards without leaving the Cockpit. Preserves workspace list order (never ranks theses, invariant #4)
 *  and reads any already-warm lifecycle state straight from the TanStack Query cache (zero extra API calls). */
export function ThesisSwitcher({
  thesisId,
  currentName,
  currentTicker,
  currentBasketSize,
  stateClass,
  asof,
  theses,
  onSwitch,
}: Props) {
  const qc = useContext(QueryClientContext);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const showSearch = theses.length >= SEARCH_MIN_THESES;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return theses;
    return theses.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        (t.ticker ?? "").toLowerCase().includes(q),
    );
  }, [theses, query]);

  // Read any warm call state from the query cache — strictly opportunistic, zero network requests.
  const cachedStateById = useMemo(() => {
    const map = new Map<string, string>();
    if (!qc || !asof) return map;
    for (const t of theses) {
      const card = qc.getQueryData<CallCardResponse>(["call", t.id, asof]);
      if (card?.state) map.set(t.id, card.state);
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qc, theses, asof, open]);

  const currentIdx = theses.findIndex((t) => t.id === thesisId);
  const prevThesis = theses.length > 1 ? theses[(currentIdx <= 0 ? theses.length : currentIdx) - 1] : null;
  const nextThesis = theses.length > 1 ? theses[(currentIdx + 1) % theses.length] : null;

  useEffect(() => {
    if (!open) return;
    if (showSearch) searchRef.current?.focus();
    else listRef.current?.querySelector<HTMLElement>('[aria-selected="true"], [role="option"]')?.focus();

    const onPointerDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, showSearch]);

  const openMenu = () => {
    setQuery("");
    const idx = theses.findIndex((t) => t.id === thesisId);
    setActiveIdx(idx >= 0 ? idx : 0);
    setOpen(true);
  };

  const pick = (id: string) => {
    setOpen(false);
    setQuery("");
    if (id !== thesisId) onSwitch(id);
    triggerRef.current?.focus();
  };

  const handleListKey = (e: KeyboardEvent<HTMLElement>) => {
    if (filtered.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => (i + 1) % filtered.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => (i - 1 + filtered.length) % filtered.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const target = filtered[Math.min(activeIdx, filtered.length - 1)];
      if (target) pick(target.id);
    }
  };

  const badgeText = tickerLabel(currentTicker, currentBasketSize);

  return (
    <div className="tsw-wrap" ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        className="tsw-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Switch thesis: ${currentName}`}
        title="Switch thesis"
        onClick={() => (open ? setOpen(false) : openMenu())}
      >
        <span className="tk" style={{ color: `var(${accentVar(stateClass)})` }}>
          {badgeText}
        </span>
        <h1 className="tsw-name">{currentName}</h1>
        <span className="tsw-chev" aria-hidden="true">
          ▾
        </span>
      </button>

      {theses.length > 1 && prevThesis && nextThesis && (
        <span className="tsw-steppers" role="group" aria-label="Step between theses">
          <button
            type="button"
            className="tsw-step"
            aria-label={`Previous thesis: ${prevThesis.name}`}
            title={`Previous: ${prevThesis.name} ([)`}
            onClick={() => onSwitch(prevThesis.id)}
          >
            ‹
          </button>
          <button
            type="button"
            className="tsw-step"
            aria-label={`Next thesis: ${nextThesis.name}`}
            title={`Next: ${nextThesis.name} (])`}
            onClick={() => onSwitch(nextThesis.id)}
          >
            ›
          </button>
        </span>
      )}

      {open && (
        <div className="tsw-menu" onKeyDown={handleListKey}>
          {showSearch && (
            <div className="tsw-search-row">
              <input
                ref={searchRef}
                type="search"
                className="tsw-search"
                aria-label="Filter theses"
                placeholder="Filter theses…"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActiveIdx(0);
                }}
              />
            </div>
          )}
          <div ref={listRef} className="tsw-list" role="listbox" aria-label="Theses">
            {filtered.length === 0 ? (
              <div className="tsw-empty">No matching theses.</div>
            ) : (
              filtered.map((t, i) => {
                const selected = t.id === thesisId;
                const focused = i === Math.min(activeIdx, filtered.length - 1);
                const st = cachedStateById.get(t.id);
                const sc = st ? (STATE_CLASS[st] ?? "incub") : null;
                return (
                  <button
                    key={t.id}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    className={`tsw-opt${selected ? " sel" : ""}${focused ? " act" : ""}`}
                    onMouseEnter={() => setActiveIdx(i)}
                    onClick={() => pick(t.id)}
                  >
                    <span
                      className={`tsw-dot${sc ? ` st-${sc}` : ""}`}
                      aria-hidden="true"
                      title={st ? STATE_LABEL[st] : undefined}
                    />
                    <span className="tsw-opt-name">{t.name}</span>
                    {t.meta_index && <span className="tsw-opt-meta">meta</span>}
                    {st && st !== "incubating" && (
                      <span className={`tsw-opt-st st-${sc}`}>{STATE_LABEL[st]}</span>
                    )}
                    <span className="tsw-opt-tk">{tickerLabel(t.ticker, t.basket_size)}</span>
                  </button>
                );
              })
            )}
          </div>
          <div className="tsw-foot">
            <span>
              {theses.length} {theses.length === 1 ? "thesis" : "theses"}
            </span>
            <span className="tsw-keys">[ / ] to step</span>
          </div>
        </div>
      )}
    </div>
  );
}
