import { createContext, useContext } from "react";

import { apiErrorText } from "../api/errorText";
import type { MeOut } from "../api/hooks";

// The signed-in user, provided once by App.tsx once GET /auth/me resolves (docs/AUTH.md). Pages and the header
// read it here instead of each firing their own /auth/me.
//
// ABSENT (null) only outside the signed-in shell — a component test that renders a page or the header on its
// own. There everything renders as it did before the login door; the App ALWAYS provides the real user, and the
// backend enforces every role regardless (the frontend only hides what a role cannot use).
const MeContext = createContext<MeOut | null>(null);

export const MeProvider = MeContext.Provider;

export function useCurrentUser(): MeOut | null {
  return useContext(MeContext);
}

/** False only for a signed-in plain user: the steward surfaces (Admin, Backtest, the shared-master writes: the
 *  business-type re-tag and Surface an ETF) hide.
 *  True for a super admin, and outside the signed-in shell (see above). */
export function useIsSteward(): boolean {
  const me = useCurrentUser();
  return me === null || me.role === "super_admin";
}

/** The one line a plain user sees on a steward-only page they reached by typing its address. */
export function StewardsOnly({ header }: { header?: React.ReactNode }) {
  return (
    <div>
      {header}
      <div className="center-note">This page is for stewards only.</div>
    </div>
  );
}

/** The API's own words from an openapi-fetch error body (its `detail` sentence, or the first message of a
 *  validation list naming its field), else a fallback. */
export function errorDetail(error: unknown, fallback: string): string {
  return apiErrorText(error) ?? fallback;
}
