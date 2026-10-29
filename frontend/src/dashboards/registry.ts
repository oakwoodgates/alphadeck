/// <reference types="vite/client" />
import type { ComponentType, LazyExoticComponent } from "react";

// The ONE list of dashboards (docs/DASHBOARDS.md): self-contained cross-card views, the Board first. The
// top nav's Dashboards menu, the /dashboards index, the /dashboards/:slug route and the home page (`/`,
// HOME_DASHBOARD below) all read this list. It is SELF-REGISTERING: each dashboard's folder,
// frontend/src/dashboards/<slug>/, carries an `entry.ts` exporting its `entry`, and the list below is built
// from every one of them, so adding a dashboard is its folder (with its entry.ts) and cutting one is deleting
// the folder: no shared line to edit. Every entry's body is lazy-loaded, so a page loads only the dashboard it
// shows (the entries themselves are tiny and load eagerly).

/** What the shell hands a dashboard body. `asof` is the app's as-of date (YYYY-MM-DD) for an as-of
 *  aware dashboard and today's date for a "now" one. `onSelectThesis` opens the Cockpit (optionally on one
 *  member, by ticker or security_id), with Back returning to the dashboard. */
export type DashboardProps = {
  asof: string;
  onSelectThesis: (thesisId: string, nameKey?: string | null) => void;
};

export type DashboardEntry = {
  /** The URL segment: /dashboards/<slug>. Also the folder name, frontend/src/dashboards/<slug>/. */
  slug: string;
  /** Menu order, lowest first (ties by label): the Board 0, then steps of 10, so a new dashboard slots in
   *  anywhere without renumbering the rest. */
  order: number;
  label: string;
  /** One line, shown under the title and in the menu. */
  description: string;
  /** "core" = a standing page of the app (the Board); "experimental" = a brainstorm view that may be cut.
   *  Both show as the same quiet tag. */
  status: "core" | "experimental";
  /** true = the page follows the app's as-of dial (the dial shows); false = a "now" view (no dial). */
  asofAware: boolean;
  /** "page" (the default) = the title row over a padded body that grows with its content. "fill" = no title
   *  row, and the body takes the rest of the viewport with no padding: for a page that scrolls inside
   *  itself, like the Board's columns. */
  layout?: "page" | "fill";
  /** React.lazy(() => import("./<Component>")) — the component file default-exports its body. */
  component: LazyExoticComponent<ComponentType<DashboardProps>>;
};

/** The dashboard `/` renders (the top nav's Home tab), in place, never a redirect. Changing the home page
 *  is this one line: any slug a dashboard's entry.ts registers. */
export const HOME_DASHBOARD = "board";

/** The registry from the dashboards' own entry modules (`{ "./<slug>/entry.ts": { entry } }`), sorted by
 *  `order` then label. Fails loud, never quietly drops or shadows a page: a module with no `entry`, an entry
 *  whose slug is not its folder's name, or two entries with one slug. */
export function buildRegistry(modules: Record<string, unknown>): DashboardEntry[] {
  const bySlug = new Map<string, string>();
  const entries: DashboardEntry[] = [];
  for (const [path, mod] of Object.entries(modules)) {
    const entry = (mod as { entry?: DashboardEntry } | undefined)?.entry;
    if (!entry) throw new Error(`Dashboard registry: ${path} does not export \`entry\``);
    const folder = path.split("/").at(-2);
    if (entry.slug !== folder) {
      throw new Error(`Dashboard registry: ${path} registers slug "${entry.slug}", not its folder "${folder}"`);
    }
    const prior = bySlug.get(entry.slug);
    if (prior) throw new Error(`Dashboard registry: duplicate slug "${entry.slug}" (${prior} and ${path})`);
    bySlug.set(entry.slug, path);
    entries.push(entry);
  }
  return entries.sort(
    (a, b) => a.order - b.order || (a.label < b.label ? -1 : a.label > b.label ? 1 : 0),
  );
}

// Every <slug>/entry.ts, collected at build time (Vite; Vitest too). Order = menu order; see docs/DASHBOARDS.md.
export const DASHBOARDS: readonly DashboardEntry[] = buildRegistry(
  import.meta.glob("./*/entry.ts", { eager: true }),
);
