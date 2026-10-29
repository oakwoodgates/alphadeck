import { useState } from "react";

import type { DashboardProps } from "../registry";
import "./catalyst-calendar.css";
import {
  daysAway,
  dayBefore,
  fmtDay,
  fmtShort,
  isFiltered,
  keepGroup,
  keepItem,
  kindChips,
  kindKey,
  kindLabel,
  NO_FILTERS,
  stateChips,
  stateCls,
  STATES,
  workLine,
  type Filters,
} from "./calendar";
import {
  useCatalystCalendar,
  type CatalystCalendarBucketOut,
  type CatalystCalendarItemOut,
  type CatalystCalendarUndatedGroupOut,
} from "./useCatalystCalendar";

// Catalyst Calendar (docs/dashboards/catalyst-calendar.md): every live card's dated catalysts and its latest
// call's two deadlines, bucketed from today, then the passed catalysts to prune and the undated ones to date. A
// "now" view, display only: pruning and dating happen in the card's editor. The buckets, the week boundary and
// the order are computed server-side; this page words each line and filters what is on screen.

type Item = CatalystCalendarItemOut;
type Select = DashboardProps["onSelectThesis"];

const DEADLINE_TITLE: Record<string, string> = {
  entry_window: "a card deadline: the latest recorded call's entry window (arm_until) ends",
  exit_by: "a card deadline: the latest recorded call's exit-by (the signal-validity horizon)",
};
const PASSED_NOTE =
  "The Board keeps showing a passed catalyst until it's removed in the card's editor.";

function StateChip({ state }: { state: string | null }) {
  const label = STATES.find((s) => s.key === (state ?? "none"))?.label ?? state;
  return (
    <span
      className={`ccal-st ${stateCls(state)}`}
      title={state ? `Latest recorded state: ${label}` : "No recorded call yet"}
    >
      {state ? label : "—"}
    </span>
  );
}

function Line({ item, today, onSelect }: { item: Item; today: string; onSelect: Select }) {
  const deadline = item.item !== "catalyst";
  return (
    <li>
      <button
        type="button"
        className={[
          "ccal-line",
          deadline ? "ccal-deadline" : null,
          item.days < 0 ? "ccal-passed" : null,
        ]
          .filter(Boolean)
          .join(" ")}
        title={`Open ${item.thesis_name} in the Cockpit`}
        onClick={() => onSelect(item.thesis_id)}
      >
        <span className="ccal-date">{fmtDay(item.on, today)}</span>
        <span className="ccal-days">{daysAway(item.days)}</span>
        <span
          className="ccal-label"
          title={item.when_label ? `also labeled “${item.when_label}”` : undefined}
        >
          {item.label}
        </span>
        <span className="ccal-kind">
          {deadline ? (
            <span className="ccal-tag" title={DEADLINE_TITLE[item.item]}>
              deadline
            </span>
          ) : item.kind ? (
            kindLabel(kindKey(item.kind))
          ) : (
            "—"
          )}
        </span>
        <span className="ccal-card">{item.thesis_name}</span>
        <StateChip state={item.state} />
      </button>
    </li>
  );
}

function bucketRange(b: CatalystCalendarBucketOut, today: string): string {
  if (b.end === null) return `after ${fmtShort(dayBefore(b.start), today)}`;
  return b.start === b.end
    ? fmtShort(b.start, today)
    : `${fmtShort(b.start, today)} – ${fmtShort(b.end, today)}`;
}

function SectionHead({ label, count }: { label: string; count: number }) {
  return (
    <h2 className="ccal-h">
      {label}
      <span className="ccal-ct"> · {count}</span>
    </h2>
  );
}

function UndatedGroup({
  group,
  open,
  onToggle,
  onSelect,
}: {
  group: CatalystCalendarUndatedGroupOut;
  open: boolean;
  onToggle: () => void;
  onSelect: Select;
}) {
  return (
    <li>
      <div className="ccal-urow">
        <button
          type="button"
          className="ccal-line ccal-uline"
          title={`Open ${group.thesis_name} in the Cockpit`}
          onClick={() => onSelect(group.thesis_id)}
        >
          <span className="ccal-count">{group.count}</span>
          <span className="ccal-card">{group.thesis_name}</span>
          <StateChip state={group.state} />
        </button>
        <button
          type="button"
          className="ccal-expand"
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} ${group.thesis_name}'s undated catalysts`}
          onClick={onToggle}
        >
          {open ? "hide" : "show"}
        </button>
      </div>
      {open && (
        <ul className="ccal-list ccal-ulist">
          {group.catalysts.map((c) => (
            <li key={c.catalyst_id}>
              <button
                type="button"
                className="ccal-line ccal-ucat"
                title={`Open ${group.thesis_name} in the Cockpit`}
                onClick={() => onSelect(group.thesis_id)}
              >
                <span className="ccal-label">{c.label}</span>
                <span className="ccal-when">{c.when_label ?? "no date"}</span>
                <span className="ccal-kind">{c.kind ? kindLabel(kindKey(c.kind)) : "—"}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function ChipGroup({
  label,
  chips,
  chosen,
  title,
  onToggle,
}: {
  label: string;
  chips: { key: string; label: string; count: number; cls?: string }[];
  chosen: ReadonlySet<string>;
  title: (chip: { label: string }) => string;
  onToggle: (key: string) => void;
}) {
  // a filter with one option doesn't discriminate, so it renders only once there's a choice (WB#3)
  if (chips.length < 2 && chosen.size === 0) return null;
  return (
    <div className="ccal-chips" role="group" aria-label={label}>
      {chips.map((c) => (
        <button
          key={c.key}
          type="button"
          className={[
            "ccal-chip",
            c.cls ? `ccal-state-chip ${c.cls}` : null,
            chosen.has(c.key) ? "on" : null,
          ]
            .filter(Boolean)
            .join(" ")}
          aria-pressed={chosen.has(c.key)}
          title={title(c)}
          onClick={() => onToggle(c.key)}
        >
          {c.label} · {c.count}
        </button>
      ))}
    </div>
  );
}

const toggled = (set: ReadonlySet<string>, key: string): ReadonlySet<string> => {
  const next = new Set(set);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
};

export default function CatalystCalendar({ onSelectThesis }: DashboardProps) {
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const q = useCatalystCalendar();
  const data = q.data;

  if (q.isLoading) return <div className="dash-note">Loading…</div>;
  if (q.error || !data) {
    return <div className="dash-note">Could not load the Catalyst Calendar.</div>;
  }

  const filtered = isFiltered(filters);
  const none = (empty: string) => (filtered ? "Nothing here matches these filters." : empty);
  const buckets = data.buckets
    .map((b) => ({ bucket: b, items: b.items.filter((i) => keepItem(i, filters)) }))
    .filter((b) => b.items.length > 0);
  const upcoming = buckets.reduce((n, b) => n + b.items.length, 0);
  const passed = data.passed.filter((i) => keepItem(i, filters));
  const groups = data.undated_groups
    .map((g) => keepGroup(g, filters))
    .filter((g): g is CatalystCalendarUndatedGroupOut => g !== null);
  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));

  return (
    <div className="ccal">
      <div className="ccal-controls">
        <input
          type="search"
          className="ccal-text"
          aria-label="Filter by card or label"
          placeholder="Filter card or label"
          value={filters.text}
          onChange={(e) => set({ text: e.target.value })}
        />
        <label className="ccal-toggle">
          <input
            type="checkbox"
            checked={filters.showDeadlines}
            onChange={(e) => set({ showDeadlines: e.target.checked })}
          />
          Show card deadlines
        </label>
        {filtered && (
          <button
            type="button"
            className="ccal-clear"
            onClick={() => set({ kinds: new Set(), states: new Set(), text: "" })}
          >
            Clear filters
          </button>
        )}
        {q.isFetching && <span className="dash-note">updating…</span>}
      </div>
      <ChipGroup
        label="Kinds"
        chips={kindChips(data)}
        chosen={filters.kinds}
        title={(c) =>
          `Show only ${c.label} catalysts (with any other chosen kinds); ` +
          "card deadlines follow their own toggle"
        }
        onToggle={(key) => set({ kinds: toggled(filters.kinds, key) })}
      />
      <ChipGroup
        label="States"
        chips={stateChips(data)}
        chosen={filters.states}
        title={(c) =>
          `Show only cards whose latest state is ${c.label.toLowerCase()} ` +
          "(with any other chosen states)"
        }
        onToggle={(key) => set({ states: toggled(filters.states, key) })}
      />
      <div className="dash-note ccal-work">{workLine(data, filters.showDeadlines)}</div>

      {data.live_cards === 0 ? (
        <div className="ccal-quiet">No live cards.</div>
      ) : (
        <>
          <section className="ccal-sec" aria-label="Upcoming">
            <SectionHead label="Upcoming" count={upcoming} />
            {buckets.length === 0 ? (
              <div className="ccal-quiet">{none("Nothing dated ahead.")}</div>
            ) : (
              buckets.map(({ bucket, items }) => (
                <div className="ccal-bucket" key={bucket.key} role="group" aria-label={bucket.label}>
                  <h3 className="ccal-bh">
                    {bucket.label}
                    <span className="ccal-range"> · {bucketRange(bucket, data.today)}</span>
                    <span className="ccal-ct"> · {items.length}</span>
                  </h3>
                  <ul className="ccal-list">
                    {items.map((i) => (
                      <Line
                        key={`${i.item}:${i.catalyst_id ?? i.thesis_id}`}
                        item={i}
                        today={data.today}
                        onSelect={onSelectThesis}
                      />
                    ))}
                  </ul>
                </div>
              ))
            )}
          </section>

          <section className="ccal-sec ccal-sec-passed" aria-label="Passed: prune?">
            <SectionHead label="Passed: prune?" count={passed.length} />
            {passed.length === 0 ? (
              <div className="ccal-quiet">{none("No passed catalysts.")}</div>
            ) : (
              <>
                <div className="dash-note ccal-note">{PASSED_NOTE}</div>
                <ul className="ccal-list">
                  {passed.map((i) => (
                    <Line
                      key={i.catalyst_id ?? `${i.thesis_id}:${i.on}`}
                      item={i}
                      today={data.today}
                      onSelect={onSelectThesis}
                    />
                  ))}
                </ul>
              </>
            )}
          </section>

          <section className="ccal-sec" aria-label="Undated">
            <SectionHead label="Undated" count={groups.reduce((n, g) => n + g.count, 0)} />
            {groups.length === 0 ? (
              <div className="ccal-quiet">{none("Every catalyst has a date.")}</div>
            ) : (
              <ul className="ccal-list">
                {groups.map((g) => (
                  <UndatedGroup
                    key={g.thesis_id}
                    group={g}
                    open={open.has(g.thesis_id)}
                    onToggle={() => setOpen((o) => toggled(o, g.thesis_id))}
                    onSelect={onSelectThesis}
                  />
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
