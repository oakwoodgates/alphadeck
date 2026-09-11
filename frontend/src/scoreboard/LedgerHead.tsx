import type { LedgerView } from "./rows";
import type { LedgerSort, LedgerSortColId } from "./sortLedger";

// The ledger's colgroup + header row (Slice 2), conditional on the view. Shared by the live ledger
// AND the replay panel so the two never drift on column count — the group-header `colSpan` at each
// call site tracks the SAME count via `ledgerColCount`. Summary keeps today's columns; Timing swaps
// the middle set to the timing-calibration lens. Name (with the ⤢ drill-down) + Armed + De-armed +
// Status are shared — a flip only changes which columns render, never the rows or the data. Only the
// summary return header differs between the two hosts, so it's a prop.
//
// Summary is 9 columns, Timing 11. Two changes moved those numbers off the old shared 8:
//
// * **Armed split into Armed · De-armed.** One cell reading "Aug 7 → Aug 11" packed two dates, two
//   meanings and a censorship marker into a column the operator could neither scan nor sort. They
//   are two measurements; they are two columns.
// * **The excursion pair became four columns.** The wick figures used to ride the close cells'
//   hovers on the grounds that a ~2pp correction is checked rather than scanned. The operator
//   overruled that: all four excursions are visible, each close figure adjacent to its own wick so
//   the pair reads together (Peak · Peak high · Worst · Worst low). The hovers no longer carry the
//   wick — each of the four describes only its own figure.

/** One sortable ledger header: the label rides a <button> (keyboard-focusable, cycles the sort) and
 *  the direction arrow is `aria-hidden`, so the header's accessible NAME stays exactly the label.
 *  The Cockpit's `SortableTh` idiom, reused rather than re-invented — including `aria-sort` on the
 *  <th> and the `.th-sort` / `.th-arrow` classes, which are already defined on the `.basket` skin
 *  this table wears. */
function SortTh({
  col,
  label,
  title,
  sort,
  onSort,
}: {
  col: LedgerSortColId;
  label: string;
  /** The column's own explanation — kept on the <th>, never on the button, so it does not become
   *  part of the accessible name (the button carries its own "sort by …"). */
  title?: string;
  sort: LedgerSort | null;
  onSort: (col: LedgerSortColId) => void;
}) {
  const dir = sort && sort.col === col ? sort.dir : null;
  return (
    <th
      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
      title={title}
    >
      <button
        type="button"
        className={`th-sort${dir ? " active" : ""}`}
        onClick={() => onSort(col)}
      >
        {label}
        {dir && (
          <span className="th-arrow" aria-hidden="true">
            {dir === "asc" ? "▲" : "▼"}
          </span>
        )}
      </button>
    </th>
  );
}

export function LedgerHead({
  view,
  returnHeader,
  sort = null,
  onSort,
}: {
  view: LedgerView;
  /** the summary-view return column header — "Record return" (live) vs "Replayed return" (replay). */
  returnHeader: string;
  /** the host's active sort (null = the record's own order). Optional so the head stays renderable
   *  in isolation; a host with no `onSort` renders inert labels rather than dead buttons. */
  sort?: LedgerSort | null;
  onSort?: (col: LedgerSortColId) => void;
}) {
  // No sort handler → plain labels. A button that cycles nothing would be a control that does not
  // discriminate, which is the one thing a header must not be.
  const th = (col: LedgerSortColId, label: string, title?: string) =>
    onSort ? (
      <SortTh col={col} label={label} title={title} sort={sort} onSort={onSort} />
    ) : (
      <th title={title}>{label}</th>
    );

  if (view === "timing") {
    return (
      <>
        <colgroup>
          <col className="c-tk" />
          <col className="c-armed" />
          <col className="c-dearm" />
          <col className="c-path" />
          <col className="c-ret" />
          <col className="c-peak" />
          <col className="c-peakhi" />
          <col className="c-worst" />
          <col className="c-worstlo" />
          <col className="c-pp" />
          <col className="c-status" />
        </colgroup>
        <thead>
          <tr>
            {th("name", "Name")}
            {th("armed", "Armed", "the day the call armed — * marks a record that began mid-arm.")}
            {th("dearmed", "De-armed", "the day it left the armed set — “—” while it is still armed.")}
            {/* Path is a SHAPE, not a number — nothing honest to rank it on, so it stays a plain
                header (the same ruling the Cockpit's own Path column carries). */}
            <th title="the episode's own closes over its own span — variable length, so a steeper line does NOT mean a faster move.">
              Path
            </th>
            {th("ret", "Return")}
            {/* Each close figure sits beside its own wick: the pair is read together, and each of
                the four hovers answers only for its own cell. */}
            {th(
              "peak",
              "Peak",
              "maximum favorable excursion (MFE) — the best close in the scored window.",
            )}
            {th(
              "peak_high",
              "Peak high",
              "the best price that actually traded — the intraday high. All-or-nothing: one bar missing a wick nulls the whole column for that episode, and the close is never substituted.",
            )}
            {th(
              "worst",
              "Worst",
              "maximum adverse excursion (MAE) — the worst close in the scored window.",
            )}
            {th(
              "worst_low",
              "Worst low",
              "the worst price that actually traded — the intraday low. All-or-nothing, like Peak high.",
            )}
            {th(
              "past_peak",
              "Past peak",
              "trading days from the realized peak to the measured exit — larger = the horizon overstayed the peak.",
            )}
            {/* Status is a set of badges, not a value — ranking OPEN against CENSORED would invent
                an ordering the record does not have. Plain header, deliberately. */}
            <th>Status</th>
          </tr>
        </thead>
      </>
    );
  }
  return (
    <>
      <colgroup>
        <col className="c-tk" />
        <col className="c-armed" />
        <col className="c-dearm" />
        <col className="c-why" />
        <col className="c-exit" />
        <col className="c-status" />
        <col className="c-ret" />
        <col className="c-peak" />
        <col className="c-op" />
      </colgroup>
      <thead>
        <tr>
          {th("name", "Name")}
          {th("armed", "Armed", "the day the call armed — * marks a record that began mid-arm.")}
          {th("dearmed", "De-armed", "the day it left the armed set — “—” while it is still armed.")}
          {/* Why (chip set), Exit-by (a per-row horizon, not in the requested sortable set) and
              Operator (a sentence) stay plain — see `sortLedger.ts` for why each is out. */}
          <th>Why</th>
          <th>Exit-by</th>
          <th>Status</th>
          {th("ret", returnHeader)}
          {th(
            "peak",
            "Peak",
            "maximum favorable excursion (MFE) — the best close in the scored window.",
          )}
          <th>Operator</th>
        </tr>
      </thead>
    </>
  );
}
