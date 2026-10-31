import { useState } from "react";

import { fmtDate, STATE_LABEL } from "../../util/format";
import type { DashboardProps } from "../registry";
import "./what-changed.css";
import { useWhatChanged, type WhatChangedItemOut, type WhatChangedOut } from "./useWhatChanged";

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
  return (
    <li>
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

type CardGroup = { thesis_id: string; thesis_name: string; reconstructed: boolean; items: Item[] };

/** Each card's lines together (in the server's section order), cards by name. */
function groupByCard(items: Item[]): CardGroup[] {
  const groups = new Map<string, CardGroup>();
  for (const i of items) {
    let g = groups.get(i.thesis_id);
    if (!g) {
      g = {
        thesis_id: i.thesis_id,
        thesis_name: i.thesis_name,
        reconstructed: i.reconstructed,
        items: [],
      };
      groups.set(i.thesis_id, g);
    }
    g.items.push(i);
  }
  return [...groups.values()].sort(
    (a, b) =>
      a.thesis_name.localeCompare(b.thesis_name, undefined, { sensitivity: "base" }) ||
      a.thesis_id.localeCompare(b.thesis_id),
  );
}

function ByCard({ items, onSelect }: ListProps) {
  return (
    <>
      {groupByCard(items).map((g) => (
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
  const q = useWhatChanged(mode !== "date" ? mode : (picked ?? "last_night"));
  const data = q.data;

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
        {q.isFetching && <span className="dash-note">updating…</span>}
      </div>

      {q.isLoading ? (
        <div className="dash-note">Loading…</div>
      ) : q.error || !data ? (
        <div className="dash-note wc-error">{errorNote(q.error)}</div>
      ) : (
        <>
          <WorkLine data={data} />
          {data.items.length === 0 ? (
            <div className="wc-quiet">{quietNote(data)}</div>
          ) : groupBy === "kind" ? (
            <BySection items={data.items} onSelect={onSelectThesis} />
          ) : (
            <ByCard items={data.items} onSelect={onSelectThesis} />
          )}
        </>
      )}
    </div>
  );
}
