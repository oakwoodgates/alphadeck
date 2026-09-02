import type { LedgerView } from "./rows";

// The ledger's colgroup + header row (Slice 2), conditional on the view. Shared by the live ledger
// AND the replay panel so the two never drift on column count — the group-header `colSpan` at each
// call site tracks the SAME count via `ledgerColCount`. Summary keeps today's columns unchanged;
// Timing swaps the middle set to the timing-calibration lens (Return · Peak · Past peak). Name (with
// the ⤢ drill-down) + Armed + Status are shared — a flip only changes which columns render, never
// the rows or the data. Only the summary return header differs between the two hosts, so it's a prop.

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
          <col className="c-ret" />
          <col className="c-peak" />
          <col className="c-pp" />
          <col className="c-status" />
        </colgroup>
        <thead>
          <tr>
            <th>Name</th>
            <th>Armed</th>
            <th>Return</th>
            <th>Peak</th>
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
