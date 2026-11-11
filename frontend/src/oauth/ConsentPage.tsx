import { useState } from "react";
import { useNavigate } from "react-router";

import { useConsent, useDecideConsent, useLogout } from "../api/hooks";
import type { ConsentOut } from "../api/hooks";
import { useCurrentUser } from "../auth/me";
import { loginPath } from "../auth/next";

// /oauth/consent/:id — an app asks to read MY workspace (docs/CONNECTORS.md). The copy is the signed-off mockup's
// (docs/temp/mockups/consent-and-connected-apps-mockup-2026-11-08.html). A quiet page: one card, no banner, no
// spinner beyond "Loading…". Allow and Deny both send the browser back to the app (Deny with access_denied, so the
// app shows its own message). The page never shows a token: the code rides the redirect, straight to the app.

/** The browser's one side effect, a module export so a test can watch it (jsdom cannot navigate). */
export const browser = {
  assign: (url: string) => window.location.assign(url),
};

type Failure = { status?: number };

function isGone(error: unknown): boolean {
  const status = (error as Failure | null)?.status;
  return status === 404 || status === 409 || status === 410;
}

/** "Read the Admin readouts (the split list, …)" -> "Read the Admin readouts": the struck, short form. */
function shortLabel(label: string): string {
  return label.replace(/\s*\([^)]*\)\s*$/, "");
}

function Gone() {
  return (
    <div>
      <h2>This sign-in request has expired or was already answered.</h2>
      <p className="muted" style={{ marginTop: 8 }}>
        Start again from the app you were connecting. A request stays open for ten minutes.
      </p>
    </div>
  );
}

function Request({
  consentId,
  consent,
  onDecide,
  busy,
}: {
  consentId: string;
  consent: ConsentOut;
  onDecide: (d: "allow" | "deny") => void;
  busy: boolean;
}) {
  const me = useCurrentUser();
  const navigate = useNavigate();
  const logout = useLogout();
  const name = consent.client_known ? consent.client_name : `“${consent.client_name}”`;
  const signOut = () =>
    logout.mutate(undefined, {
      onSettled: () => navigate(loginPath(`/oauth/consent/${consentId}`), { replace: true }),
    });
  return (
    <>
      <div>
        <h2>{name} wants to read your Alpha Deck workspace.</h2>
        {consent.client_known ? (
          <p className="who">A known client.</p>
        ) : (
          <p className="who">
            This app registered itself under that name. Alpha Deck has not verified it.{" "}
            <span className="tag">unverified</span>
          </p>
        )}
      </div>
      <ul className="scopes" aria-label="What it can read">
        {consent.scopes.map((s) =>
          s.grantable ? (
            <li key={s.name}>{s.label}.</li>
          ) : (
            <li key={s.name} className="na">
              {shortLabel(s.label)} <small>(not available to your account)</small>
            </li>
          ),
        )}
      </ul>
      <p className="never">
        It will not be able to change anything, and you can disconnect it any time from Settings › Agents.
      </p>
      <p className="back">
        You will be sent back to <code>{consent.redirect_host}</code>.
      </p>
      <div className="actions">
        <button type="button" className="primary" disabled={busy} onClick={() => onDecide("allow")}>
          Allow
        </button>
        <button type="button" disabled={busy} onClick={() => onDecide("deny")}>
          Deny
        </button>
      </div>
      {me && (
        <p className="signed-as">
          Signed in as {me.display_name}
          {me.role === "super_admin" ? " (steward)" : ""}.{" "}
          <a onClick={signOut}>Not you? Sign out</a>
        </p>
      )}
    </>
  );
}

export function ConsentPage({ consentId, header }: { consentId: string; header?: React.ReactNode }) {
  const consent = useConsent(consentId);
  const decide = useDecideConsent(consentId);
  const [sentTo, setSentTo] = useState<string | null>(null);

  const onDecide = (d: "allow" | "deny") =>
    decide.mutate(d, {
      onSuccess: (reply) => {
        setSentTo(consent.data?.redirect_host ?? "");
        browser.assign(reply.redirect_to);
      },
    });

  let body: React.ReactNode;
  if (sentTo !== null) {
    body = <p className="muted">Sending you back to {sentTo}…</p>;
  } else if (consent.isPending) {
    body = <p className="muted">Loading…</p>;
  } else if (isGone(consent.error) || isGone(decide.error)) {
    body = <Gone />;
  } else if (consent.isError || !consent.data) {
    body = <p className="login-error">This sign-in request could not be read.</p>;
  } else {
    body = (
      <>
        <Request consentId={consentId} consent={consent.data} onDecide={onDecide} busy={decide.isPending} />
        {decide.isError && <p className="login-error">Your answer was not saved. Try again.</p>}
      </>
    );
  }
  return (
    <div>
      {header}
      <div className="consent-wrap">
        <div className="consent">{body}</div>
      </div>
    </div>
  );
}
