import type { ScoreboardEditionOut } from "../api/hooks";
import { useEditionDiff } from "../api/hooks";
import { fmtDate } from "../util/format";
import { editionLabel } from "./editions";
import {
  causeNote,
  causeText,
  countsLines,
  episodeLine,
  namesFor,
  nightChange,
  nightSpan,
  outsideLine,
  sortCompanies,
  thesisLine,
} from "./whatChanged";

// "WHAT CHANGED" (editions S6) — the comparison view the quiet link beside the edition switcher opens: the
// viewed edition against the one before it, thesis -> company, in plain English, with the counts on top.
// Server state only (`useEditionDiff`, fetched when this mounts — the drawer is open); nothing stored in the
// browser. Every thesis is listed, a quiet "no change" included, so nothing vanishes; within a thesis only the
// companies that changed are listed and the rest are counted. No alarm colors: a comparison is context (#7).

export function EditionChanges({
  before,
  after,
}: {
  before: ScoreboardEditionOut;
  after: ScoreboardEditionOut;
}) {
  const { data, isLoading, error } = useEditionDiff(before.number, after.number, true);
  const names = namesFor(before, after);

  return (
    <div className="wc">
      <div className="wc-intro">
        {editionLabel(after)} compared with {editionLabel(before)}
        {data?.through && data.first_night && (
          <>
            {" "}
            · the nights both cover, {fmtDate(data.first_night)} – {fmtDate(data.through)}
          </>
        )}
      </div>
      {isLoading && <div className="sb-quietline">Comparing the two editions…</div>}
      {error != null && <div className="sb-quietline">The comparison is unavailable right now.</div>}
      {data && !data.through && (
        <div className="sb-quietline">
          One of the two editions has no night yet, so there is nothing to compare.
        </div>
      )}
      {data && data.through && (
        <>
          <div className="wc-counts">
            {countsLines(data.counts, names).map((line) => (
              <div key={line}>{line}</div>
            ))}
          </div>
          <div className="wc-note">
            a night with no page reads that edition&apos;s last page before it · scores run arm → exit-by on
            realized closes through {fmtDate(data.through)} · {causeNote(data.causes, names)}
          </div>
          {data.theses.map((t) => {
            const outside = outsideLine(t, names);
            const companies = sortCompanies(t.companies);
            return (
              <section key={t.thesis_id} className="wc-thesis">
                <h3 className="wc-thesis-h">
                  {t.name}
                  {t.archived && <span className="wc-tag">archived</span>}
                </h3>
                <div className="wc-line">{thesisLine(t)}</div>
                {outside && <div className="wc-line wc-quiet">{outside}</div>}
                {t.error && <div className="wc-line wc-quiet">could not compare: {t.error}</div>}
                {!t.error && t.first_night && companies.length === 0 && t.n_state_nights === 0 && (
                  <div className="wc-line wc-quiet">no change</div>
                )}
                {companies.map((c) => (
                  <div key={c.security_id} className="wc-company">
                    <div className="wc-company-h">
                      <span className="tk">{c.ticker ?? "—"}</span>
                      {c.company_name && <span className="wc-name">{c.company_name}</span>}
                    </div>
                    <ul className="wc-list">
                      {(c.episodes ?? []).map((ep, i) => {
                        const why = causeText(ep.cause, names);
                        return (
                          <li key={`ep-${i}`}>
                            {episodeLine(ep, names, t.first_night)}
                            {why && <span className="wc-cause"> · {why}</span>}
                          </li>
                        );
                      })}
                      {(c.runs ?? []).map((r) => {
                        const why = causeText(r.cause, names);
                        return (
                          <li key={`run-${r.first}`} className="wc-run">
                            {nightSpan(r.first, r.last, r.n_nights)} — {nightChange(r.changed ?? [], r.before, r.after, names)}
                            {why && <span className="wc-cause"> · {why}</span>}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ))}
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}
