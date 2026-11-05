import { businessTypeLabel, supersectorLabel } from "../../util/format";

// The Sectors tab's grid of the Overlap Map (docs/dashboards/overlap-map.md): rows are the counted cards, columns the
// supersectors (or business types), a bubble per cell sized by the card's companies in that sector. PURE: the
// payload's cards and companies in, the grid out. A company is in exactly one column (its sector today), so in a
// cell it counts once; a company with no sector is "Unclassified", never dropped.

export type SectorMode = "supersector" | "business_type";

export type GridCompany = {
  supersector: string | null;
  business_type: string | null;
  rows: readonly { card: number }[];
};

export type GridColumn = {
  /** The sector value; "" = Unclassified. */
  key: string;
  label: string;
  /** Cards with at least one company here. */
  cards: number;
  /** Companies here, each once across cards. */
  companies: number;
};

export type GridRow = {
  card: number;
  /** The card's companies in view (each once). */
  total: number;
  /** Columns the card has a company in. */
  sectors: number;
  /** Company indexes (into the payload's `companies`) by column key. */
  cells: Record<string, number[]>;
};

export type SectorGrid = { columns: GridColumn[]; rows: GridRow[] };

const UNCLASSIFIED = "";
const OTHER = "other";

export const sectorKey = (c: GridCompany, mode: SectorMode): string =>
  (mode === "supersector" ? c.supersector : c.business_type) ?? UNCLASSIFIED;

export function sectorLabel(key: string, mode: SectorMode): string {
  if (key === UNCLASSIFIED) return "Unclassified";
  return mode === "supersector" ? supersectorLabel(key) : businessTypeLabel(key);
}

/** The column order: by the number of cards each appears on (most first), ties by label; "Other" then
 *  "Unclassified" always last. */
function columnRank(c: GridColumn): number {
  if (c.key === UNCLASSIFIED) return 2;
  if (c.key === OTHER) return 1;
  return 0;
}

/**
 * The grid for `cards` (their names, in the payload's order) and `companies`.
 *
 * Row order, stated: a card's LEAD column is the column (in the column order above) holding most of its
 * companies, the earlier column on a tie. Rows sort by the lead column's place, then by that column's share of the
 * card, descending, then by name. Cards with no companies come last, A to Z. So cards with a similar mix sit next
 * to each other.
 */
export function buildSectorGrid(
  cardNames: readonly string[],
  companies: readonly GridCompany[],
  mode: SectorMode,
): SectorGrid {
  const cellsByCard = new Map<number, Map<string, number[]>>();
  const cardsIn = new Map<string, Set<number>>();
  const companiesIn = new Map<string, number>();
  companies.forEach((co, idx) => {
    const key = sectorKey(co, mode);
    companiesIn.set(key, (companiesIn.get(key) ?? 0) + 1);
    for (const row of co.rows) {
      const byKey = cellsByCard.get(row.card) ?? new Map<string, number[]>();
      byKey.set(key, [...(byKey.get(key) ?? []), idx]);
      cellsByCard.set(row.card, byKey);
      const s = cardsIn.get(key) ?? new Set<number>();
      s.add(row.card);
      cardsIn.set(key, s);
    }
  });

  const columns: GridColumn[] = [...companiesIn.keys()].map((key) => ({
    key,
    label: sectorLabel(key, mode),
    cards: cardsIn.get(key)?.size ?? 0,
    companies: companiesIn.get(key) ?? 0,
  }));
  columns.sort(
    (a, b) =>
      columnRank(a) - columnRank(b) ||
      b.cards - a.cards ||
      a.label.localeCompare(b.label, "en") ||
      a.key.localeCompare(b.key, "en"),
  );
  const place = new Map(columns.map((c, i) => [c.key, i]));

  const rows: (GridRow & { lead: number; share: number })[] = cardNames.map((_, card) => {
    const byKey = cellsByCard.get(card) ?? new Map<string, number[]>();
    const total = [...byKey.values()].reduce((s, v) => s + v.length, 0);
    let lead = Number.POSITIVE_INFINITY;
    let best = 0;
    for (const [key, ids] of byKey) {
      const p = place.get(key) ?? Number.POSITIVE_INFINITY;
      if (ids.length > best || (ids.length === best && p < lead)) {
        best = ids.length;
        lead = p;
      }
    }
    return {
      card,
      total,
      sectors: byKey.size,
      cells: Object.fromEntries(byKey),
      lead,
      share: total > 0 ? best / total : 0,
    };
  });
  rows.sort(
    (a, b) =>
      a.lead - b.lead ||
      b.share - a.share ||
      cardNames[a.card].localeCompare(cardNames[b.card], "en", { sensitivity: "base" }) ||
      a.card - b.card,
  );
  return {
    columns,
    rows: rows.map(({ card, total, sectors, cells }) => ({ card, total, sectors, cells })),
  };
}

/** A cell bubble's smallest radius, so a one-company cell stays visible. */
export const CELL_MIN_R = 3;

/** A cell bubble's radius: area proportional to the companies, scaled so the grid's biggest cell is `max`, with a
 *  floor of `CELL_MIN_R`. */
export function cellRadius(count: number, biggest: number, max: number): number {
  if (count <= 0 || biggest <= 0) return 0;
  return Math.max(CELL_MIN_R, max * Math.sqrt(count / biggest));
}
