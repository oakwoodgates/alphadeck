import raw from "./server-payload.json";
import type { TrackCapOut, TrackEntryOut, TrackRecordOut, TrackRowOut, TrackSpanOut } from "../useTrackRecord";

// Track Record payloads shared by the view-helper and component tests.
//
// SERVER is a response PRODUCED BY THE BACKEND (the real route's wire model over rows run through
// dashboards.track_record.assemble), saved as server-payload.json. Twenty episodes on two cards: twelve small-cap
// names whose 20-session excess against IWM is the hand-computed vector below (the same twelve numbers the
// backend's test pins), three micro names (too thin to read), one unpinned cap, one mid name whose 20-session path
// ended, and one episode each left out as a flagged price history, a censored start and an ingest flag. Its
// `opening` table is the server's own reading at the opening choices (IWM, arm-night entry, by cap tier, every armed
// member): recordView.tableRows must reproduce it from `rows`, so the two implementations cannot drift apart.

export const SERVER = raw as unknown as TrackRecordOut;

/** The excess vector (the backend test's EXCESS_12): n = 12, median 0.025, quartiles -0.005 and 0.0525, 8 held. */
export const EXCESS_12 = [-0.1, -0.04, -0.02, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.12];

export const payload = (over: Partial<TrackRecordOut> = {}): TrackRecordOut => ({ ...SERVER, ...over });

export const span = (over: Partial<TrackSpanOut> = {}): TrackSpanOut => ({
  status: "counted",
  end_date: "2026-07-22",
  ret: 0.1,
  iwm: 0.04,
  spy: 0.01,
  ...over,
});

export const maturing = (): TrackSpanOut => ({ status: "maturing", end_date: null, ret: null, iwm: null, spy: null });

export const entry = (spans: Partial<Record<string, TrackSpanOut>> = {}): TrackEntryOut => ({
  entry_date: "2026-06-03",
  entry_close: 10,
  spans: { h5: maturing(), h10: maturing(), h20: maturing(), h60: maturing(), exit_by: maturing(), ...spans } as Record<
    string,
    TrackSpanOut
  >,
});

export const cap = (over: Partial<TrackCapOut> = {}): TrackCapOut => ({
  value: 576_000_000,
  tier: "small",
  gap: null,
  note: null,
  close: 14.4,
  close_date: "2026-06-02",
  shares: 40_000_000,
  shares_date: "2026-05-12",
  ads_ratio: null,
  split_factor: null,
  ...over,
});

let n = 0;
/** One counted episode row: a small-cap name whose 20-session span is counted by default. */
export const row = (over: Partial<TrackRowOut> = {}): TrackRowOut => {
  n += 1;
  const s = span();
  return {
    thesis_id: "00000000-0000-0000-0000-0000000000a1",
    thesis_name: "Card A",
    security_id: `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`,
    ticker: `T${String(n).padStart(3, "0")}`,
    arm_date: "2026-06-03",
    exit_by: "2026-07-31",
    arm_until: "2026-07-31",
    close_reason: "window_end",
    is_headline: true,
    key1_source: "insider",
    entry_grade: "core",
    confirmation_grade: "core",
    co_arm_bucket: "alone",
    left_out: null,
    left_out_note: null,
    cap: cap(),
    adv_usd: 2_500_000,
    liquidity: "1m_10m",
    arm_night: entry({ h20: s }),
    next_session: entry({ h20: s }),
    ...over,
  };
};
