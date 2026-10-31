import type { MemberDataFlagsOut } from "../api/hooks";
import { useDataFlags } from "../api/hooks";
import { useAppClock } from "../clock";
import { CELL_LABEL, LEVEL_WORD, rowLevel } from "../cockpit/dataFlags";
import { errText } from "../workbench/format";

/** One name's flags as Admin lists them: the ticker, the theses holding it, and each flag in full. */
function Row({ m }: { m: MemberDataFlagsOut }) {
  return (
    <li data-testid="adm-flag-row">
      <b>{m.ticker ?? m.security_id}</b>{" "}
      <span className="adm-sub">{(m.theses ?? []).map((t) => t.name).join(" · ")}</span>
      {(m.flags ?? []).map((f, i) => (
        <div key={i} className="adm-flag">
          <span className={`adm-flag-lvl ${f.level}`}>{LEVEL_WORD[f.level]}</span> {f.label}
          {f.since ? ` · since ${f.since}` : ""}
          {f.call_affected ? " · price triggers held on the call card" : ""}
          <div className="adm-sub">{f.reason}</div>
          <div className="adm-sub">
            {(f.columns ?? []).length > 0
              ? `affects ${(f.columns ?? []).map((c) => CELL_LABEL[c] ?? c).join(", ")}`
              : "no Cockpit number affected"}{" "}
            · {f.clears}
          </div>
        </div>
      ))}
      {(m.notes ?? []).map((n) => (
        <div key={n.kind} className="adm-sub adm-note">
          {n.reason}
        </div>
      ))}
    </li>
  );
}

/** The "Data flags" panel (split plan S3): the full current list of the shared "is this real?" flags across
 *  the active baskets, as of the current night — computed on read, never stored. The names whose flags reach a Cockpit
 *  number lead (each row there carries the quiet mark); the ones whose flags touch no number on the row
 *  (an old split, a stale tail outside every window) sit in a collapsed quiet list. Read-only: what clears
 *  each flag is named on it (the split review above, the price-feed repairs, the split repair). No alert,
 *  ever (#7). The names with only a quiet MARKER (a price history under a year — information, not a defect)
 *  sit last, in their own collapsed list, never counted as flagged. Renders nothing when nothing is flagged
 *  or marked. */
export function DataFlagList() {
  // the server's current night (F1, clock.ts): the date every other default view reads, read live
  const q = useDataFlags(useAppClock().currentNight);
  if (q.isError) {
    return (
      <section className="adm-card" data-testid="adm-flags">
        <div className="adm-h">Data flags</div>
        <div className="adm-err">{errText(q.error)}</div>
      </section>
    );
  }
  const all = q.data?.members ?? [];
  if (all.length === 0) return null;
  // a marker is not a flag: the names with flags are counted and listed; a marker-only name sits apart
  const members = all.filter((m) => (m.flags ?? []).length > 0);
  const notedOnly = all.filter((m) => (m.flags ?? []).length === 0 && (m.notes ?? []).length > 0);
  // the ONE row-mark rule the Cockpit and the Board use (a flag that reaches a cell), never a second flag
  const marked = members.filter((m) => rowLevel(m) != null);
  const quiet = members.filter((m) => rowLevel(m) == null);
  return (
    <section className="adm-card" data-testid="adm-flags">
      <div className="adm-h">Data flags</div>
      <div className="adm-line">
        <b>{marked.length}</b> of {q.data?.names ?? 0} names carry a flag that reaches a Cockpit number
        {quiet.length > 0 ? ` · ${quiet.length} more with flags no Cockpit number reads` : ""}
      </div>
      {marked.length > 0 && (
        <ul className="adm-flags" data-testid="adm-flags-marked">
          {marked.map((m) => (
            <Row key={m.security_id} m={m} />
          ))}
        </ul>
      )}
      {quiet.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-flags-quiet">
          <summary className="adm-line adm-quiet">
            {quiet.length} with flags no Cockpit number reads (an old split, a stale tail, fundamentals
            SEC's data has not caught up on)
          </summary>
          <ul className="adm-flags">
            {quiet.map((m) => (
              <Row key={m.security_id} m={m} />
            ))}
          </ul>
        </details>
      )}
      {notedOnly.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-flags-noted">
          <summary className="adm-line adm-quiet">
            {notedOnly.length} with only a quiet marker (
            {[...new Set(notedOnly.flatMap((m) => (m.notes ?? []).map((n) => n.label)))].join(", ")}):
            information, not a defect
          </summary>
          <ul className="adm-flags">
            {notedOnly.map((m) => (
              <Row key={m.security_id} m={m} />
            ))}
          </ul>
        </details>
      )}
      <div className="adm-sub">
        Computed on read for today, never stored, never a call input. In doubt = the number is shown grayed; known
        wrong = it is not shown. Each flag says what clears it.
      </div>
    </section>
  );
}
