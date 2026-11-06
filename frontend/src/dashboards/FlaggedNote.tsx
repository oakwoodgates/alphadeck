import "./flagged-note.css";

// The quiet page note every price dashboard shares (docs/DASHBOARDS.md rule 5): the companies it leaves out of
// every number because their data flags reach their price readings (the reason the Cockpit grays those cells;
// dashboards.baskets.price_flags on the server). One line, expandable to each ticker and its flags' labels:
// left out, never silently gone (#9). A member row inside an expanded group says the same in its own place
// (`leftOutWords`). Quiet by construction (#7 / WB#3): gray, and nothing at all when no name is left out.

/** A name a price dashboard leaves out: its ticker and the flags' labels ("mixed-scale price history"). */
export type FlaggedName = {
  security_id?: string;
  ticker: string;
  reason: string;
};

const LEAD =
  "Their data flags reach their price readings (the Cockpit grays those cells), so";

/** The default hover, for a page whose own rule leaves a flagged name out of every number it shows. */
const TITLE = `${LEAD} they are left out of every number on this page.`;

/** A page whose rule keeps the row (or leaves the name out of only some readings) states its own rule: the hover
 *  reads the shared lead plus the page's sentence. Operator decision 2026-11-04: the default says "every number",
 *  which is false on a page that keeps the row. */
export const flaggedRule = (rule: string) => `${LEAD} ${rule}`;

/** The words a left-out member row shows in place of its readings. */
export const leftOutWords = (reason: string) => `left out: ${reason}`;

/** "N names left out for a flagged price history", expandable to each ticker and reason; nothing when none is. */
export function FlaggedNote({
  flagged,
  title,
}: {
  flagged: readonly FlaggedName[];
  title?: string;
}) {
  if (flagged.length === 0) return null;
  const n = flagged.length;
  return (
    <details className="dash-note dash-flagged">
      <summary title={title ?? TITLE}>
        {n.toLocaleString("en-US")} {n === 1 ? "name" : "names"} left out for a
        flagged price history
      </summary>
      <ul className="dash-flagged-list">
        {flagged.map((f) => (
          <li key={f.security_id ?? f.ticker}>
            <span className="dash-flagged-tk">{f.ticker}</span> {f.reason}
          </li>
        ))}
      </ul>
    </details>
  );
}
