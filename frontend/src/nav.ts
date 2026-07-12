// The URL scheme, in one pure router-free module — the single place paths and params are spelled.
//
//   /                    the Board
//   /scoreboard          the Scoreboard (the API's own /scoreboard lives behind /api — see api/client.ts)
//   /workbench           the Workbench
//   /thesis/:thesisId    the Cockpit (singular, deliberately outside the API's /theses namespace)
//   ?asof=YYYY-MM-DD     any view; absent or malformed = today
//   ?name=<key>          Cockpit only; the NamePanel open on that member (ticker or security_id)
//
// App.tsx owns the router wiring; everything here is plain string-building so a router swap (or a
// test) never touches the scheme itself.

export const ASOF = "asof";
export const NAME = "name";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** The ?asof= value if well-formed, else null — absent and malformed are treated identically, so
 *  junk in the URL never reaches the API (the caller falls back to today) and drops off on the
 *  next navigation. Impossible calendar dates (e.g. 2026-02-31) are caught by round-tripping the
 *  components through Date.UTC — the constructor rolls them over, the comparison catches it
 *  (Date.parse can't be trusted here: V8 leniently rolls the day too). */
export function validAsof(raw: string | null): string | null {
  if (!raw || !ISO_DATE.test(raw)) return null;
  const [y, m, d] = raw.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  const real = dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
  return real ? raw : null;
}

function withParams(path: string, params: Record<string, string | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) sp.set(k, v);
  const qs = sp.toString();
  return qs ? `${path}?${qs}` : path;
}

export const boardPath = (asof: string | null) => withParams("/", { [ASOF]: asof });

export const scoreboardPath = (asof: string | null) => withParams("/scoreboard", { [ASOF]: asof });

export const workbenchPath = (asof: string | null) => withParams("/workbench", { [ASOF]: asof });

export const thesisPath = (
  id: string,
  opts: { asof?: string | null; name?: string | null } = {},
): string =>
  withParams(`/thesis/${encodeURIComponent(id)}`, {
    [ASOF]: opts.asof ?? null,
    [NAME]: opts.name ?? null,
  });
