import { useRecordStart } from "../api/hooks";
import { fmtDay } from "../components/valueWords";

// The quiet "the record starts on ..." note (W8c), shared by every dashboard that reads the calls history. A
// workspace's record starts fresh, so on day one those pages are empty or nearly so: this says why, honestly (the
// record starts on a date; nothing is broken). It shows only where it explains something: with no nightly call
// recorded yet (always), or on a page that has no history rows to show (`empty`); a page with rows stays clean.
// While the read loads, or if it fails, it renders nothing (never a spinner or an error for a note).
export function RecordStartNote({ empty }: { empty: boolean }) {
  const { data } = useRecordStart();
  if (data === undefined) return null;
  if (data !== null && !empty) return null;
  return (
    <div className="dash-note" data-testid="record-start-note">
      {data === null
        ? "The record starts with the first nightly call."
        : `The record starts on ${fmtDay(data)}. Nothing before that night is on it.`}
    </div>
  );
}
