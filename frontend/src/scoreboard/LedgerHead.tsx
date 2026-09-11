import type { LedgerView } from "./rows";

// The ledger's colgroup + header row (Slice 2), conditional on the view. Shared by the live ledger
// AND the replay panel so the two never drift on column count — the group-header `colSpan` at each
// call site tracks the SAME count via `ledgerColCount`. Summary keeps today's columns unchanged;
// Timing swaps the middle set to the timing-calibration lens (Path · Return · Peak · Worst · Past
// peak). Name (with the ⤢ drill-down) + Armed + Status are shared — a flip only changes which columns
// render, never the rows or the data. Only the summary return header differs between the two hosts,
// so it's a prop.
//
// Timing is 8 columns, matching Summary. Path and Worst were added TOGETHER rather than by promoting
// the wick figures into columns of their own: the wick is a ~2pp correction that agrees in sign and
// rough size with the close pair on essentially every row, and four unlabelled numbers per row on a
// table whose job is fast down-column scanning buys nothing the hover cannot say. Ten columns also
// breaks the ledger's flowing-document layout.

export function LedgerHead({
  view,
  returnHeader,
}: {
  view: LedgerView;
  /** the summary-view return column header — "Record return" (live) vs "Replayed return" (replay). */
  returnHeader: string;
}) {
  if (view === "timing") {
    return (
      <>
        <colgroup>
          <col className="c-tk" />
          <col className="c-armed" />
          <col className="c-path" />
          <col className="c-ret" />
          <col className="c-peak" />
          <col className="c-worst" />
          <col className="c-pp" />
          <col className="c-status" />
        </colgroup>
        <thead>
          <tr>
            <th>Name</th>
            <th>Armed</th>
            <th title="the episode's own closes over its own span — variable length, so a steeper line does NOT mean a faster move.">
              Path
            </th>
            <th>Return</th>
            <th title="maximum favourable excursion (MFE) — the best close in the scored window.">
              Peak
            </th>
            {/* Worst pairs with Peak on the SAME close basis the Return is measured on. A row that
                went straight up and a row that was 12% underwater before recovering used to render
                identically; the wick correction rides each cell's hover rather than a column. */}
            <th title="maximum adverse excursion (MAE) — the worst close in the scored window.">
              Worst
            </th>
            <th title="trading days from the realized peak to the measured exit — larger = the horizon overstayed the peak.">
              Past peak
            </th>
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
        <col className="c-why" />
        <col className="c-exit" />
        <col className="c-status" />
        <col className="c-ret" />
        <col className="c-peak" />
        <col className="c-op" />
      </colgroup>
      <thead>
        <tr>
          <th>Name</th>
          <th>Armed</th>
          <th>Why</th>
          <th>Exit-by</th>
          <th>Status</th>
          <th>{returnHeader}</th>
          <th>Peak</th>
          <th>Operator</th>
        </tr>
      </thead>
    </>
  );
}
