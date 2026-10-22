import type { MemberDataFlagsOut } from "../api/hooks";
import { useDataFlags } from "../api/hooks";
import { CELL_LABEL, LEVEL_WORD, rowLevel } from "../cockpit/dataFlags";
import { todayISO } from "../util/format";
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
    </li>
  );
}

/** The "Data flags" panel (split plan S3): the full current list of the shared "is this real?" flags across
 *  the active baskets, as of today — computed on read, never stored. The names whose flags reach a Cockpit
 *  number lead (each row there carries the quiet mark); the ones whose flags touch no number on the row
 *  (an old split, a stale tail outside every window) sit in a collapsed quiet list. Read-only: what clears
 *  each flag is named on it (the split review above, the price-feed repairs, the split repair). No alert,
 *  ever (#7). Renders nothing when nothing is flagged. */
export function DataFlagList() {
  const q = useDataFlags(todayISO());
  if (q.isError) {
    return (
      <section className="adm-card" data-testid="adm-flags">
        <div className="adm-h">Data flags</div>
        <div className="adm-err">{errText(q.error)}</div>
      </section>
    );
  }
  const members = q.data?.members ?? [];
  if (members.length === 0) return null;
  // the ONE row-mark rule the Cockpit and the Board use (a flag that reaches a cell), never a second flag
  const marked = members.filter((m) => rowLevel(m) != null);
  const quiet = members.filter((m) => rowLevel(m) == null);
  return (
    <section className="adm-card" data-testid="adm-flags">
      <div className="adm-h">Data flags</div>
      <div className="adm-line">
        <b>{marked.length}</b> of {q.data?.names ?? 0} names carry a flag that reaches a Cockpit number
        {quiet.length > 0 ? ` · ${quiet.length} more with history-only flags` : ""}
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
            {quiet.length} with flags no Cockpit number reads (history only)
          </summary>
          <ul className="adm-flags">
            {quiet.map((m) => (
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
