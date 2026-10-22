import type { ScoreboardEditionOut } from "../api/hooks";
import { fmtDate } from "../util/format";

// Scoreboard EDITIONS (S4) — pure helpers for the header's quiet label + switcher. Edition 0 is the original
// record: the calls log, what the platform actually said each night. Edition N >= 1 is a stored re-run of
// today's rules from each company's join date, judged on the public clock and frozen at its cut. A re-run has
// no reconstructed, annotated or late rows and no ingest history, so the page hides those lines, their
// toggles and the INGEST badge for it (`isRerun`). Copy lives here so it is testable on its own.

type EditionLike = Pick<ScoreboardEditionOut, "number" | "label" | "cut">;

/** True for a stored re-run edition (N >= 1); the record (0) and an unknown edition read as the record. */
export function isRerun(edition: number | null | undefined): boolean {
  return edition != null && edition >= 1;
}

/** The cut's MARKET day ("Oct 22") — the same calendar the backend's drawer caption dates it on. */
export function cutDay(cut: string | null | undefined): string | null {
  if (!cut) return null;
  const t = Date.parse(cut);
  if (Number.isNaN(t)) return null;
  return new Date(t).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "America/New_York",
  });
}

/** "Edition 0 · the original record" / "Edition 1 · first re-run · cut Oct 22". */
export function editionLabel(e: EditionLike): string {
  const base = `Edition ${e.number} · ${e.label}`;
  const day = cutDay(e.cut);
  return isRerun(e.number) && day ? `${base} · cut ${day}` : base;
}

/** The hover on a switcher button / the label: what the edition IS, its nights, its join dates. */
export function editionTitle(e: ScoreboardEditionOut): string {
  const nights =
    e.first_night && e.last_night ? `nights ${fmtDate(e.first_night)} – ${fmtDate(e.last_night)}` : "no nights yet";
  if (!isRerun(e.number)) {
    return `the calls log: what the platform actually said each night · ${nights}`;
  }
  const joins =
    e.n_entries != null && e.n_entries_estimated != null
      ? ` · ${e.n_entries_estimated} of ${e.n_entries} join dates estimated`
      : "";
  const causes = e.causes.length ? ` · cut for: ${e.causes.join(", ")}` : "";
  return (
    "today's rules re-run from each company's join date, judged on the public clock, frozen at its cut" +
    ` · ${nights}${joins}${causes}`
  );
}

/** The quiet line under the switcher for a re-run: its nights and how many join dates are estimates. */
export function editionNote(e: ScoreboardEditionOut | null | undefined): string | null {
  if (!e || !isRerun(e.number)) return null;
  const parts = [] as string[];
  if (e.first_night && e.last_night) parts.push(`nights ${fmtDate(e.first_night)} – ${fmtDate(e.last_night)}`);
  if (e.n_entries != null && e.n_entries_estimated != null && e.n_entries_estimated > 0) {
    parts.push(`${e.n_entries_estimated} of ${e.n_entries} join dates estimated`);
  }
  parts.push("no reconstructed, annotated or late nights in a re-run");
  return parts.join(" · ");
}
