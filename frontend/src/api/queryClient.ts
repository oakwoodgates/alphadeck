import { QueryClient } from "@tanstack/react-query";

// The app's cache policy, in one place (PR-1a — docs/temp/board-cockpit-perf-2026-09-02.md §4.C).
//
// The measured problem: with TanStack's defaults every return to the Board and every window focus
// re-fired all eleven `/call`s (~27 s of backend work each time), and entering the Cockpit re-fired
// `/call` although the Board had just computed it. Two dials fix that — this file holds both.

/** How long a MONITOR read (theses, thesis, call, display-signals, scored, decisions) stays FRESH:
 *  within this window a remount reuses the cache instead of re-firing the request.
 *
 *  The operator can tune this — shorter = livelier and more backend work, longer = quieter. It is
 *  honest at any value because every write invalidates the keys it can change (see the invalidation
 *  audit on the mutations in hooks.ts), so an operator action always shows immediately; only a
 *  change made OUTSIDE this tab (the nightly cron, a second browser tab) waits out the window. The
 *  as-of is a DATE and part of every key, so tomorrow is a new key regardless. */
export const MONITOR_STALE_MS = 10 * 60 * 1000; // 10 minutes

/** The app's QueryClient. A factory (not a module-level singleton) so tests exercise these REAL
 *  defaults rather than a hand-rolled client that could drift from what main.tsx ships.
 *
 *  `refetchOnWindowFocus: false` is a CLIENT-WIDE default: alt-tabbing back to the app must not
 *  recompute the Board. Deliberately the only default set here — `staleTime` stays per-hook,
 *  because the Workbench's triage-session restore and the job-poll hooks depend on mount-refetch
 *  semantics that a global staleTime would silently break. */
export function createQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });
}
