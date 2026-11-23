import type { DashboardEntry } from "./registry";

// Navigation groups for the 31 dashboards (Dashboards ▾ menu and /dashboards index).
// Each dashboard's own `<slug>/entry.ts` declares its `group`; cutting a folder removes it cleanly.

export type DashboardGroup = "daily" | "rotation" | "chain" | "fundamentals" | "filings";

export const DASHBOARD_GROUPS: readonly { key: DashboardGroup; label: string }[] = [
  { key: "daily", label: "Daily & Calls" },
  { key: "rotation", label: "Rotation & Tape" },
  { key: "chain", label: "Chain & Overlap" },
  { key: "fundamentals", label: "Fundamentals & Peers" },
  { key: "filings", label: "Filings & Events" },
];

export type GroupedDashboards<T extends DashboardEntry> = {
  key: DashboardGroup | "other";
  label: string;
  items: T[];
};

/** Partition dashboards into the ordered navigation groups, preserving registry order within each group.
 *  Entries without a `group` fall into a trailing "Other" bucket; empty groups are omitted. */
export function groupDashboards<T extends DashboardEntry>(items: readonly T[]): GroupedDashboards<T>[] {
  const buckets = new Map<DashboardGroup | "other", T[]>();
  for (const item of items) {
    const key: DashboardGroup | "other" = item.group ?? "other";
    const list = buckets.get(key);
    if (list) list.push(item);
    else buckets.set(key, [item]);
  }
  const out: GroupedDashboards<T>[] = [];
  for (const g of DASHBOARD_GROUPS) {
    const list = buckets.get(g.key);
    if (list && list.length > 0) out.push({ key: g.key, label: g.label, items: list });
  }
  const other = buckets.get("other");
  if (other && other.length > 0) out.push({ key: "other", label: "Other", items: other });
  return out;
}

/** Warm a dashboard's lazy JS chunk on pointer hover or keyboard focus so clicking the tile or menu item
 *  renders synchronously without flashing the Suspense fallback. Pure bundle preload: makes no API request
 *  (docs/DASHBOARDS.md: the index makes no requests). */
export function preloadDashboard(entry: DashboardEntry): void {
  const lazy = entry.component as unknown as {
    _init?: (payload: unknown) => unknown;
    _payload?: { _status?: number; _result?: unknown };
  };
  const payload = lazy._payload;
  if (!payload || payload._status !== -1 || typeof lazy._init !== "function") return;
  const ctor = payload._result;
  try {
    lazy._init(payload);
  } catch (thrown) {
    if (thrown && typeof (thrown as { catch?: unknown }).catch === "function") {
      (thrown as Promise<unknown>).catch(() => {
        if (payload._status === 2) {
          payload._status = -1;
          payload._result = ctor;
        }
      });
    }
  }
}
