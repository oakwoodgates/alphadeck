// The login page's `?next=` (docs/CONNECTORS.md): after sign-in the login page returns to an OAuth consent page and
// NOWHERE else. Any other value is ignored (no open redirect): only an in-app path of exactly the consent page's
// shape is honored.

export const CONSENT_PREFIX = "/oauth/consent/";
const CONSENT_PATH = /^\/oauth\/consent\/[A-Za-z0-9-]{1,64}$/;

/** `raw` when it is a consent-page path, else null. */
export function safeNext(raw: string | null | undefined): string | null {
  return raw && CONSENT_PATH.test(raw) ? raw : null;
}

/** Where a signed-out visitor goes: /login, carrying `next` only for a consent page. */
export function loginPath(pathname: string): string {
  const next = safeNext(pathname);
  return next ? `/login?next=${encodeURIComponent(next)}` : "/login";
}
