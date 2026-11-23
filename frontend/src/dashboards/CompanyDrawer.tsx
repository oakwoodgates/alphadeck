import { useMemo } from "react";

import { useMemberPanel } from "../api/hooks";
import { groupBasket } from "../cockpit/buckets";
import { NamePanel } from "../cockpit/NamePanel";
import { Drawer } from "../components/Drawer";

export interface CompanyDrawerSelection {
  thesisId: string;
  nameKey: string;
}

interface Props {
  selection: CompanyDrawerSelection | null;
  asof: string;
  onClose: () => void;
  onSwitchCard: (thesisId: string, nameKey: string) => void;
  onOpenCockpit: (thesisId: string, nameKey?: string | null) => void;
}

/** Slide-over company view for dashboards: mounts the Cockpit's full per-name `NamePanel` inside
 *  the reusable `Drawer` (default 600px, expandable to full width), backed by the single-member
 *  endpoint so opening a company on a large basket never computes the rest of the basket.
 *  When the company sits on 2+ active cards in the workspace, renders a quiet card switcher so
 *  the user can flip thesis contexts without leaving the dashboard (WB#1, WB#3). */
export function CompanyDrawer({
  selection,
  asof,
  onClose,
  onSwitchCard,
  onOpenCockpit,
}: Props) {
  const q = useMemberPanel(selection?.thesisId ?? null, selection?.nameKey ?? null, asof);
  const data = q.data ?? null;

  // Derive the exact same BucketRow + BucketDef the Cockpit uses, over the single resolved member.
  const resolved = useMemo(() => {
    if (!data) return null;
    const groups = groupBasket([data.member], data.card, data.scored ? [data.scored] : undefined);
    const firstGroup = groups[0];
    const firstRow = firstGroup?.rows[0];
    if (!firstGroup || !firstRow) return null;
    return { row: firstRow, def: firstGroup.def };
  }, [data]);

  const title = selection ? (
    data && resolved ? (
      <span className={`cd-title ${resolved.def.cls}`}>
        <span className="np-tk">{data.member.ticker}</span>
        <span className="np-bucket">
          <span className="rowdot" />
          {resolved.def.label}
        </span>
      </span>
    ) : (
      <span className="np-tk">{selection.nameKey}</span>
    )
  ) : undefined;

  const actions = selection ? (
    <button
      type="button"
      className="cd-cockpit"
      title="Open this company in the Cockpit"
      onClick={() => onOpenCockpit(selection.thesisId, data?.member.ticker ?? selection.nameKey)}
    >
      Open in Cockpit ↗
    </button>
  ) : undefined;

  const switching = Boolean(data && q.isPlaceholderData);

  return (
    <Drawer open={selection != null} onClose={onClose} title={title} actions={actions}>
      {selection && (
        <>
          {q.isLoading && !data ? (
            <div className="dash-note">Loading company view…</div>
          ) : q.isError && !data ? (
            <div className="cd-empty">
              <div className="dash-note">Could not load this company view.</div>
              <button
                type="button"
                className="cd-cockpit"
                onClick={() => onOpenCockpit(selection.thesisId, selection.nameKey)}
              >
                Open card in Cockpit ↗
              </button>
            </div>
          ) : !q.isPending && q.data === null ? (
            <div className="cd-empty">
              <div className="dash-note">
                {selection.nameKey} is not on this card&apos;s current basket.
              </div>
              <button
                type="button"
                className="cd-cockpit"
                onClick={() => onOpenCockpit(selection.thesisId, null)}
              >
                Open card in Cockpit ↗
              </button>
            </div>
          ) : data && resolved ? (
            <div className={`cd-body${switching ? " recomputing" : ""}`}>
              <div className="cd-cards">
                {data.cards.length > 1 ? (
                  <>
                    <span className="cd-cards-label">Cards ({data.cards.length})</span>
                    <div role="group" aria-label="Switch thesis card" style={{ display: "contents" }}>
                      {data.cards.map((c) => {
                        const active = c.thesis_id === data.thesis_id;
                        return (
                          <button
                            key={c.thesis_id}
                            type="button"
                            className={`cd-card-chip${active ? " active" : ""}`}
                            aria-pressed={active}
                            onClick={() => {
                              if (!active) {
                                onSwitchCard(c.thesis_id, data.member.security_id ?? c.ticker);
                              }
                            }}
                          >
                            {c.thesis_name}
                            {c.archived && <span className="cd-card-arch">archived</span>}
                          </button>
                        );
                      })}
                    </div>
                  </>
                ) : (
                  <>
                    <span className="cd-cards-label">Card</span>
                    <span className="cd-card-name">
                      {data.thesis_name}
                      {data.cards[0]?.archived && <span className="cd-card-arch">archived</span>}
                    </span>
                  </>
                )}
              </div>
              {switching && <div className="dash-note">Switching card…</div>}
              <NamePanel
                embedded
                row={resolved.row}
                def={resolved.def}
                card={data.card}
                thesisId={data.thesis_id}
                position={data.position}
                display={data.display ?? null}
                dataFlags={data.data_flags ?? null}
                sharedValues={data.shared_values ?? null}
                decisions={data.decisions}
                asof={asof}
                onClose={onClose}
              />
            </div>
          ) : null}
        </>
      )}
    </Drawer>
  );
}
