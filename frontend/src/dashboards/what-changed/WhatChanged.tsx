import { useMemo, useState } from "react";

import { ExternalLink } from "../../components/ExternalLink";
import { fmtDate, STATE_LABEL } from "../../util/format";
import type { DashboardProps } from "../registry";
import "./what-changed.css";
import { useWhatChanged, type WhatChangedItemOut, type WhatChangedOut } from "./useWhatChanged";
import { RecordStartNote } from "../RecordStartNote";

// What Changed (docs/dashboards/what-changed.md): what moved on the live cards between two recorded nights,
// and the clocks coming due on the newest one. A "now" view with its own "since" selector, display only.
// Every diff rule (which two cards, what counts as a change, the clocks, the order) is computed server-side;
// this page words each line and groups what is on screen.

type Mode = "last_night" | "7d" | "date";
type GroupBy = "kind" | "card";
type Item = WhatChangedItemOut;

const SECTIONS: readonly { key: Item["section"]; label: string }[] = [
  { key: "column_moves", label: "Column moves" },
  { key: "keys", label: "Keys turned" },
  { key: "names", label: "Names" },
  { key: "triggers", label: "Triggers" },
  { key: "clocks", label: "Clocks" },
  { key: "new_cards", label: "New cards" },
];

const SINCE: readonly { mode: Mode; label: string }[] = [
  { mode: "last_night", label: "Last night" },
  { mode: "7d", label: "Last 7 days" },
  { mode: "date", label: "Since a date" },
];

// the Board's column classes, so a state reads in its own column's color
const STATE_CLS: Record<string, string> = {
  incubating: "incub",
  warming: "warm",
  armed: "armed",
  managing: "manage",
};

const RECONSTRUCTED_TITLE =
  "this card's newest row was written by pipeline.backfill: a reconstruction of a missed night, " +
  "not the nightly's own record";
const LIMIT_TITLE =
  "the log records only on change, so a night the nightly didn't run looks the same as a quiet night";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function StateWord({ state }: { state: string | null }) {
  if (!state) return <span className="wc-st">—</span>;
  return <span className={`wc-st ${STATE_CLS[state] ?? ""}`}>{STATE_LABEL[state] ?? state}</span>;
}

/** label · kind · event date: what a trigger line names after its verb */
function signal(i: Item): string {
  return [i.label, i.signal_kind?.replace(/_/g, " "), i.on ? fmtDate(i.on) : null]
    .filter(Boolean)
    .join(" · ");
}

/** label · kind: a re-dated trigger's names, before its two dates */
function redated(i: Item): string {
  return [i.label, i.signal_kind?.replace(/_/g, " ")].filter(Boolean).join(" · ");
}

function entryWindow(i: Item): string {
  const days = i.days ?? 0;
  return days === 0
    ? `entry window closes today · ${fmtDate(i.on)}`
    : `entry window closes in ${plural(days, "day", "days")} · ${fmtDate(i.on)}`;
}

/** The line's own words. Neutral throughout: a trigger that stopped is "no longer firing", one still firing
 *  on a newer event date is "re-dated". */
function What({ item }: { item: Item }) {
  switch (item.change) {
    case "state":
      return (
        <>
          <StateWord state={item.from_state} /> <span className="wc-arrow">→</span>{" "}
          <StateWord state={item.to_state} />
        </>
      );
    case "key_on":
      return <>{item.label} key turned on</>;
    case "key_off":
      return <>{item.label} key turned off</>;
    case "armed":
      return <>newly armed</>;
    case "no_longer_armed":
      return <>no longer armed</>;
    case "new_watch":
      return <>new on watch</>;
    case "trigger_new":
      return <>fired · {signal(item)}</>;
    case "trigger_gone":
      return <>no longer firing · {signal(item)}</>;
    case "risk_new":
      return <>new risk signal · {signal(item)}</>;
    case "trigger_redated":
      return (
        <>
          re-dated · {redated(item)} · {fmtDate(item.previous_on)}{" "}
          <span className="wc-arrow">→</span> {fmtDate(item.on)}
        </>
      );
    case "entry_window":
      return <>{entryWindow(item)}</>;
    case "exit_by":
      return <>exit-by reached · {fmtDate(item.on)}</>;
    case "new_card":
      return (
        <>
          new card · <StateWord state={item.to_state} />
        </>
      );
  }
}

function ReconstructedTag() {
  return (
    <span className="wc-tag" title={RECONSTRUCTED_TITLE}>
      reconstructed
    </span>
  );
}

function Line({
  item,
  showCard,
  onSelect,
}: {
  item: Item;
  showCard: boolean;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  // a name or trigger line opens the Cockpit on that name (ticker, else the security id); a card-level line
  // opens the card
  const nameKey = item.ticker ?? item.security_id;
  const open = () => (nameKey ? onSelect(item.thesis_id, nameKey) : onSelect(item.thesis_id));
  const src = item.source;
  return (
    <li className="wc-li">
      <button
        type="button"
        // a re-dated trigger is still firing: quieter than a fired / no-longer-firing line (WB#3)
        className={`wc-line${showCard ? "" : " in-card"}${item.change === "trigger_redated" ? " wc-redated" : ""}`}
        title={`Open ${item.thesis_name}${item.ticker ? ` on ${item.ticker}` : ""}`}
        onClick={open}
      >
        {showCard && <span className="wc-card">{item.thesis_name}</span>}
        <span
          className="wc-tk"
          title={item.security_id && !item.ticker ? item.security_id : undefined}
        >
          {item.security_id ? (item.ticker ?? "—") : ""}
        </span>
        <span className="wc-what">
          <What item={item} />
          {showCard && item.reconstructed && <ReconstructedTag />}
        </span>
      </button>
      {src &&
        (src.url ? (
          <ExternalLink className="wc-src" href={src.url}>
            {src.source} {src.ref}
          </ExternalLink>
        ) : (
          <span className="wc-src">
            {src.source} {src.ref}
          </span>
        ))}
    </li>
  );
}

type ListProps = { items: Item[]; onSelect: DashboardProps["onSelectThesis"] };

function BySection({ items, onSelect }: ListProps) {
  return (
    <>
      {SECTIONS.map(({ key, label }) => {
        const rows = items.filter((i) => i.section === key);
        if (rows.length === 0) return null; // only what moved renders (honest loudness, WB#3)
        return (
          <section className="wc-sec" key={key} aria-label={label}>
            <h2 className="wc-h">
              {label}
              <span className="wc-ct"> · {rows.length}</span>
            </h2>
            <ul className="wc-list">
              {rows.map((i, n) => (
                <Line key={n} item={i} showCard onSelect={onSelect} />
              ))}
            </ul>
          </section>
        );
      })}
    </>
  );
}

type CardGroup = { thesis_id: string; thesis_name: string; reconstructed: boolean; items: Item[]; touchesArmed: boolean };

function itemTouchesArmed(i: Item): boolean {
  return (
    i.to_state === "armed" ||
    i.from_state === "armed" ||
    i.change === "armed" ||
    i.change === "no_longer_armed" ||
    i.change === "entry_window"
  );
}

const ROUTINE_CHANGES = new Set<Item["change"]>(["trigger_redated", "trigger_gone", "new_watch"]);
const isRoutineItem = (i: Item) => ROUTINE_CHANGES.has(i.change);
const AUTO_FOLD_THRESHOLD = 12;

/** Each card's lines together (in the server's section order), cards by name (or Armed-touching first on request). */
function groupByCard(items: Item[], armedFirst: boolean): CardGroup[] {
  const groups = new Map<string, CardGroup>();
  for (const i of items) {
    let g = groups.get(i.thesis_id);
    if (!g) {
      g = {
        thesis_id: i.thesis_id,
        thesis_name: i.thesis_name,
        reconstructed: i.reconstructed,
        items: [],
        touchesArmed: false,
      };
      groups.set(i.thesis_id, g);
    }
    g.items.push(i);
    if (itemTouchesArmed(i)) g.touchesArmed = true;
  }
  return [...groups.values()].sort(
    (a, b) =>
      (armedFirst ? Number(b.touchesArmed) - Number(a.touchesArmed) : 0) ||
      a.thesis_name.localeCompare(b.thesis_name, undefined, { sensitivity: "base" }) ||
      a.thesis_id.localeCompare(b.thesis_id),
  );
}

function ByCard({ items, onSelect, armedFirst }: ListProps & { armedFirst: boolean }) {
  return (
    <>
      {groupByCard(items, armedFirst).map((g) => (
        <section className="wc-sec" key={g.thesis_id} aria-label={g.thesis_name}>
          <h2 className="wc-h wc-cardh">
            <button type="button" className="wc-cardname" onClick={() => onSelect(g.thesis_id)}>
              {g.thesis_name}
            </button>
            <span className="wc-ct"> · {g.items.length}</span>
            {g.reconstructed && <ReconstructedTag />}
          </h2>
          <ul className="wc-list">
            {g.items.map((i, n) => (
              <Line key={n} item={i} showCard={false} onSelect={onSelect} />
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

/** Top summary strip: clickable section counts to isolate one section + net state-transition tally. */
function SummaryStrip({
  items,
  activeSection,
  onPickSection,
}: {
  items: readonly Item[];
  activeSection: Item["section"] | null;
  onPickSection: (sec: Item["section"] | null) => void;
}) {
  const counts = SECTIONS.map((s) => ({
    ...s,
    count: items.filter((i) => i.section === s.key).length,
  })).filter((s) => s.count > 0);

  const moves = items.filter((i) => i.change === "state");
  const netByState = new Map<string, number>();
  for (const m of moves) {
    if (m.to_state) netByState.set(m.to_state, (netByState.get(m.to_state) ?? 0) + 1);
    if (m.from_state) netByState.set(m.from_state, (netByState.get(m.from_state) ?? 0) - 1);
  }
  const transitions = ["armed", "managing", "warming", "incubating"]
    .map((st) => ({ st, net: netByState.get(st) ?? 0 }))
    .filter((x) => x.net !== 0);

  if (counts.length === 0) return null;

  return (
    <div className="wc-summary" data-testid="wc-summary">
      <div className="wc-sum-pills" role="group" aria-label="Sections">
        {counts.map((s) => {
          const on = activeSection === s.key;
          return (
            <button
              key={s.key}
              type="button"
              className={`wc-sum-pill${on ? " on" : ""}`}
              aria-pressed={on}
              title={on ? "Show all sections" : `Show only ${s.label}`}
              onClick={() => onPickSection(on ? null : s.key)}
            >
              {s.label} <span className="wc-sum-n">{s.count}</span>
            </button>
          );
        })}
      </div>
      {transitions.length > 0 && (
        <div className="wc-sum-moves" aria-label="Net column moves">
          <span className="wc-sum-lbl">Net moves</span>
          {transitions.map((t) => (
            <span key={t.st} className="wc-sum-mv">
              <StateWord state={t.st} /> {t.net > 0 ? `+${t.net}` : t.net}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** The quiet line that shows the work: the two nights compared, every count off the payload, and the rule. */
function WorkLine({ data }: { data: WhatChangedOut }) {
  const unrecorded = data.unrecorded.length > 0 && (
    <span title={data.unrecorded.map((c) => c.thesis_name).join(", ")}>
      {" · "}
      {plural(data.unrecorded.length, "live card", "live cards")} with no recorded call yet
    </span>
  );
  if (!data.latest) {
    return (
      <div className="dash-note wc-work">
        {plural(data.live_cards, "live card", "live cards")} · no recorded call yet
      </div>
    );
  }
  const nights = data.baseline
    ? `${fmtDate(data.baseline)} → ${fmtDate(data.latest)}`
    : `the record begins ${fmtDate(data.latest)}`;
  return (
    <div className="dash-note wc-work" title={LIMIT_TITLE}>
      {[
        nights,
        plural(data.live_cards, "live card", "live cards"),
        plural(data.cards_with_change, "card with a change", "cards with a change"),
        "the log records only on change, so a card with no new row is unchanged",
      ].join(" · ")}
      {unrecorded}
    </div>
  );
}

function quietNote(data: WhatChangedOut): string {
  if (!data.latest) return "No recorded calls yet.";
  return data.baseline ? `No changes since ${fmtDate(data.baseline)}.` : "No changes.";
}

/** A 422 carries its reason as a string `detail` (a picked date outside the record); anything else is generic. */
function errorNote(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error && typeof error.detail === "string") {
    return error.detail;
  }
  return "Could not load What Changed.";
}

export default function WhatChanged({ onSelectThesis }: DashboardProps) {
  const [mode, setMode] = useState<Mode>("last_night");
  const [picked, setPicked] = useState<string | null>(null);
  // the date picker's bounds, taken from the record when it opens (a failed read must not lose them)
  const [range, setRange] = useState<{ min: string | null; max: string | null }>({
    min: null,
    max: null,
  });
  const [groupBy, setGroupBy] = useState<GroupBy>("kind");
  const [sectionFilter, setSectionFilter] = useState<Item["section"] | null>(null);
  const [search, setSearch] = useState("");
  const [armedFirst, setArmedFirst] = useState(false);
  const [foldOverride, setFoldOverride] = useState<boolean | null>(null);
  const q = useWhatChanged(mode !== "date" ? mode : (picked ?? "last_night"));
  const data = q.data;

  const allItems = useMemo(() => data?.items ?? [], [data]);
  const routineCount = useMemo(() => allItems.filter(isRoutineItem).length, [allItems]);
  const foldRoutine = foldOverride ?? routineCount >= AUTO_FOLD_THRESHOLD;

  const visibleItems = useMemo(() => {
    const t = search.trim().toLocaleLowerCase();
    return allItems.filter((i) => {
      if (sectionFilter && i.section !== sectionFilter) return false;
      if (foldRoutine && isRoutineItem(i)) return false;
      if (t) {
        const hay = `${i.thesis_name} ${i.ticker ?? ""} ${i.label ?? ""} ${i.signal_kind ?? ""} ${i.from_state ?? ""} ${i.to_state ?? ""}`.toLocaleLowerCase();
        if (!hay.includes(t)) return false;
      }
      return true;
    });
  }, [allItems, sectionFilter, foldRoutine, search]);

  const hasFilters = sectionFilter !== null || search.trim() !== "";

  const choose = (m: Mode) => {
    if (m === "date") {
      // open on the night in view, so picking "Since a date" alone changes nothing on screen (a 7-day
      // baseline from before the record began opens on the first recorded night instead)
      const min = data?.first_recorded ?? range.min;
      const start = data?.baseline ?? data?.latest ?? null;
      setPicked((p) => p ?? (start && min && start < min ? min : start));
      setRange({ min, max: data?.latest ?? range.max });
    }
    setMode(m);
  };

  return (
    <div className="wc">
      <div className="wc-controls">
        <div className="wc-seg" role="group" aria-label="Since">
          {SINCE.map((s) => (
            <button
              type="button"
              key={s.mode}
              aria-pressed={mode === s.mode}
              className={mode === s.mode ? "on" : undefined}
              disabled={s.mode === "date" && mode !== "date" && !data?.latest}
              onClick={() => choose(s.mode)}
            >
              {s.label}
            </button>
          ))}
        </div>
        {mode === "date" && (
          <input
            type="date"
            className="wc-date"
            aria-label="Since date"
            value={picked ?? ""}
            min={range.min ?? undefined}
            max={range.max ?? undefined}
            onChange={(e) => {
              if (ISO_DATE.test(e.target.value)) setPicked(e.target.value);
            }}
          />
        )}
        <div className="wc-seg" role="group" aria-label="Group by">
          <button
            type="button"
            aria-pressed={groupBy === "kind"}
            className={groupBy === "kind" ? "on" : undefined}
            onClick={() => setGroupBy("kind")}
          >
            By kind
          </button>
          <button
            type="button"
            aria-pressed={groupBy === "card"}
            className={groupBy === "card" ? "on" : undefined}
            onClick={() => setGroupBy("card")}
          >
            By card
          </button>
        </div>
        {groupBy === "card" && allItems.length > 0 && (
          <button
            type="button"
            className={`wc-chip${armedFirst ? " on" : ""}`}
            aria-pressed={armedFirst}
            onClick={() => setArmedFirst((v) => !v)}
          >
            Armed first
          </button>
        )}
        {allItems.length > 0 && (
          <input
            type="search"
            className="wc-search"
            aria-label="Filter changes"
            placeholder="Filter card, ticker, or label"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        )}
        {routineCount > 0 && (
          <button
            type="button"
            className={`wc-chip${foldRoutine ? " on" : ""}`}
            aria-pressed={foldRoutine}
            title="Fold routine churn (re-dated triggers, no-longer-firing triggers, new watch names)"
            onClick={() => setFoldOverride(!foldRoutine)}
          >
            {foldRoutine ? `Show ${routineCount} routine` : `Fold routine (${routineCount})`}
          </button>
        )}
        {hasFilters && (
          <button
            type="button"
            className="wc-clear"
            onClick={() => {
              setSectionFilter(null);
              setSearch("");
            }}
          >
            Clear filters
          </button>
        )}
        {q.isFetching && <span className="dash-note">updating…</span>}
      </div>

      {q.isLoading ? (
        <div className="dash-note">Loading…</div>
      ) : q.error || !data ? (
        <div className="dash-note wc-error">{errorNote(q.error)}</div>
      ) : (
        <>
          <RecordStartNote empty={data.latest === null} />
          <WorkLine data={data} />
          <SummaryStrip items={allItems} activeSection={sectionFilter} onPickSection={setSectionFilter} />
          {data.items.length === 0 ? (
            <div className="wc-quiet">{quietNote(data)}</div>
          ) : visibleItems.length === 0 ? (
            <div className="wc-quiet">No changes match these filters.</div>
          ) : groupBy === "kind" ? (
            <BySection items={visibleItems} onSelect={onSelectThesis} />
          ) : (
            <ByCard items={visibleItems} onSelect={onSelectThesis} armedFirst={armedFirst} />
          )}
        </>
      )}
    </div>
  );
}
