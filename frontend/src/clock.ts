import { createContext, useContext } from "react";

import type { ClockOut } from "./api/hooks";
import { isPastAsof, todayISO } from "./util/format";

// F1 (2026-10-30) — the app's "today" is the SERVER's, never the browser's.
//
// The default as-of is the CURRENT NIGHT: the night the nightly pass last recorded (or is recording), served by
// GET /clock. At 09:00 Tuesday that is Monday, so the morning Board shows the call Monday night recorded and alerted
// on, not a "Tuesday" recompute from Monday's facts. A decision or a ratified fact is dated with the server's
// `marketToday`. App.tsx reads the clock once (TanStack Query, `useClock`) and hands the derived days down through
// this context, so a component needs no QueryClient of its own to know them.

export interface AppClock {
  /** the app's default as-of: the night the pass last recorded (YYYY-MM-DD) */
  currentNight: string;
  /** today's date in the market's zone: what a decision or a ratified fact is dated with */
  marketToday: string;
  /** the first weekday after the current night: the day the operator acts on its call (null = unknown) */
  actionDay: string | null;
  /** true when the server's clock could not be read and the browser's date stands in (the quiet note) */
  failed: boolean;
}

/** The server's days, as the app uses them. */
export function clockFromServer(c: ClockOut): AppClock {
  return { currentNight: c.current_night, marketToday: c.market_today, actionDay: c.action_day, failed: false };
}

/** The browser's date standing in for every day. `failed` marks the fallback App.tsx uses when GET /clock failed
 *  (the header says so, quietly); without a provider (a component rendered on its own) it is false. */
export function browserClock(failed: boolean): AppClock {
  const today = todayISO();
  return { currentNight: today, marketToday: today, actionDay: null, failed };
}

const ClockContext = createContext<AppClock | null>(null);

export const ClockProvider = ClockContext.Provider;

/** The app's days: the server's when App.tsx provides them, else the browser's date (no provider). */
export function useAppClock(): AppClock {
  return useContext(ClockContext) ?? browserClock(false);
}

/** F2 — the day a COUNTDOWN to a future date counts from: the ACTION day on a live view, else the as-of itself.
 *  The as-of is the night, for the data; the action day (the session after it) is the day the operator acts, so it is
 *  the day the words count from: a catalyst dated Tuesday reads "today" on Tuesday morning's view of Monday's night,
 *  one dated Monday reads "passed", and a past view (an as-of before the current night) is unchanged. A date equal
 *  to the origin reads "today", a date before it "passed". The mirror of the backend's `countdown_origin`
 *  (domain/market_time.py): live = `!isPastAsof(asof, currentNight)`, the same test the server serves a live read by.
 *
 *  COUNTDOWNS only. The AGE of a past event (a filing's "Nd", "N days before the as-of", days in state, "filed N days
 *  after the trade") describes the record and still counts from the as-of; `sessions_left` is the call's own count.
 *  When the server's clock could not be read (`actionDay` null, the browser's fallback) the origin is the as-of. */
export function countdownOrigin(asof: string, clock: AppClock): string {
  return clock.actionDay !== null && !isPastAsof(asof, clock.currentNight) ? clock.actionDay : asof;
}

/** `countdownOrigin` for the app's clock (a component's one call; no provider = the as-of). */
export function useCountdownOrigin(asof: string): string {
  return countdownOrigin(asof, useAppClock());
}

export const CLOCK_FALLBACK_TITLE =
  "The server's clock could not be read, so the default as-of is this browser's date. The app normally opens on the night the nightly pass last recorded.";
