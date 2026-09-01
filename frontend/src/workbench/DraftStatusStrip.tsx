import type { DraftReportOut } from "../api/hooks";

export interface DraftCounts {
  placed: number;
  verify: number;
  ambiguous: number;
  absent: number;
}

interface Props {
  counts: DraftCounts;
  report: DraftReportOut;
}

// The draft run's honesty report (#9 rules 2/3 on screen), rendered per the inverse-loudness rule: a 100%
// healthy run is ONE muted line; any gap is a loud ⚑ block naming ONLY the failing dimensions. "skipped"
// (no key / live disabled) is the operator's own configuration — shown quietly, never alarmed. This strip is
// also what disambiguates done-but-empty (a strip with 0 placed + full coverage) from failed (an error toast,
// no strip). Display-only RUN state from the last draft — never persisted.
export function DraftStatusStrip({ counts, report }: Props) {
  const cov = report.coverage;
  // The seeds-only scope badge (draft-scope PR-2): the operator CHOSE the fast lane, so this is a state, not
  // a fault — NOT the ⚑ block, but persistent + mid-loudness while the state holds (a scoped draft must never
  // read as completed discovery). Renders ONLY for scope === "seeds_only" (honest loudness — a badge on every
  // draft is noise): "full" AND an absent scope (an old restored session blob predating the field — the wire
  // type says required, but the blob is what it is) both render exactly as before.
  const seedsOnly = report.scope === "seeds_only";
  const gaps: string[] = [];
  if (cov.pages_ok < cov.pages_attempted) {
    gaps.push(
      `EFTS coverage ${cov.pages_ok}/${cov.pages_attempted} — pages still missing for: ` +
        `${(cov.failed_terms ?? []).join(", ")}. Names surfacing only under these terms may be absent; ` +
        "re-draft to retry.",
    );
  }
  if ((report.capped_terms ?? []).length > 0) {
    gaps.push(
      `Hit-capped: ${(report.capped_terms ?? []).join(", ")} — more filings matched than the enumeration ` +
        "cap; deep hits for these terms were not searched, so names may be missing.",
    );
  }
  if ((report.empty_terms ?? []).length > 0) {
    gaps.push(
      `Zero EDGAR hits: ${(report.empty_terms ?? []).join(", ")} — matched no filer; a seed here ` +
        "placed no names.",
    );
  }
  if (report.tail_sweep === "failed") {
    gaps.push("Tail-sweep failed — foreign / newly-listed names may be missing; re-draft to retry it.");
  }
  if (report.narration_filled < report.narration_needed) {
    gaps.push(
      `Narration ${report.narration_filled} of ${report.narration_needed} — some names lack thesis-fit ` +
        "prose (the names are kept; only the prose is empty).",
    );
  }

  const sweepLabel = report.tail_sweep === "skipped" ? "skipped (no key)" : report.tail_sweep;
  const summary =
    `${counts.placed} placed · ${counts.verify} to review · ${counts.ambiguous} to pick · ` +
    `${counts.absent} absent · coverage ${cov.pages_ok}/${cov.pages_attempted}` +
    // Under seeds_only the skip is the CHOSEN scope, not the no-key config — the badge below is the skip's
    // one explanation, so the quiet "sweep skipped (no key)" mention is suppressed IN THIS CASE ONLY (it
    // would double-report the same skip with the wrong reason). Every other case renders exactly as before.
    (seedsOnly && report.tail_sweep === "skipped" ? "" : ` · sweep ${sweepLabel}`) +
    (report.narration_needed > 0
      ? ` · narration ${report.narration_filled}/${report.narration_needed}`
      : "");

  // Persistent while the state holds — beneath the quiet line AND the ⚑ block alike.
  const scopeBadge = seedsOnly ? (
    <div className="wb-draft-strip scoped">
      Seeds-only draft — BROAD terms + tail-sweep not run · run a full draft to complete discovery
    </div>
  ) : null;

  if (gaps.length === 0) {
    return (
      <>
        <div className="wb-draft-strip note">Draft complete — {summary}</div>
        {scopeBadge}
      </>
    );
  }
  return (
    <>
      <div className="wb-draft-strip loud">
        <div>
          <b>⚑ Draft completed with gaps</b> — {summary}
        </div>
        <ul>
          {gaps.map((g, i) => (
            <li key={i}>{g}</li>
          ))}
        </ul>
      </div>
      {scopeBadge}
    </>
  );
}
