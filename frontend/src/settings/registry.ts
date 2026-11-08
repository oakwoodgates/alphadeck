import { AgentsTab } from "./agents/AgentsTab";
import { SlackTab } from "./notifications/SlackTab";
import { ProfileTab } from "./profile/ProfileTab";

// The ONE list of Settings tabs (docs/AUTH.md §Settings page), in display order. The /settings/:tab route
// (App.tsx) and the page's tablist (SettingsPage.tsx) both read it, so a new settings page is one folder + one
// entry here: no routing line, no header change. `stewardOnly` renders the tab only for a super admin (the
// header's `steward` idiom); the API enforces every rule regardless.

/** What a tab component receives. Nothing yet: each tab reads the signed-in user and its own queries. */
export type TabProps = Record<string, never>;

export type SettingsTab = {
  /** The URL segment: /settings/<slug>. */
  slug: string;
  label: string;
  Component: (p: TabProps) => React.JSX.Element;
  stewardOnly?: boolean;
};

export const SETTINGS_TABS: readonly SettingsTab[] = [
  { slug: "profile", label: "Profile", Component: ProfileTab },
  { slug: "notifications", label: "Notifications", Component: SlackTab },
  { slug: "agents", label: "Agents", Component: AgentsTab },
];

export const SETTINGS_DEFAULT_TAB = "profile";

/** True when `raw` names a registry tab (absent and unknown are identical: the route sends both to the default). */
export function isSettingsTab(raw: string | null | undefined): raw is string {
  return !!raw && SETTINGS_TABS.some((t) => t.slug === raw);
}
