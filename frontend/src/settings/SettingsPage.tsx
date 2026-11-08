import { useIsSteward } from "../auth/me";
import { SETTINGS_TABS } from "./registry";

// The Settings page shell (docs/AUTH.md §Settings page), router-free like every page: App.tsx's SettingsRoute
// reads the URL and hands in the active `tab` and `onSelectTab`. A quiet tablist from the registry, then the
// active tab's component. A steward-only entry renders only for a super admin.
export function SettingsPage({
  header,
  tab,
  onSelectTab,
}: {
  header: React.ReactNode;
  tab: string;
  onSelectTab: (slug: string) => void;
}) {
  const steward = useIsSteward();
  const tabs = SETTINGS_TABS.filter((t) => !t.stewardOnly || steward);
  const active = tabs.find((t) => t.slug === tab) ?? tabs[0];
  const Active = active.Component;
  return (
    <div>
      {header}
      <div className="account-page settings-page">
        <h2>Settings</h2>
        <nav role="tablist" aria-label="Settings" className="settings-tabs">
          {tabs.map((t) => (
            <button
              key={t.slug}
              type="button"
              role="tab"
              aria-selected={t.slug === active.slug}
              className={t.slug === active.slug ? "on" : undefined}
              onClick={() => {
                if (t.slug !== active.slug) onSelectTab(t.slug);
              }}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div role="tabpanel" aria-label={active.label} className="settings-panel">
          <Active />
        </div>
      </div>
    </div>
  );
}
