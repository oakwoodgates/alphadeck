import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SplitList } from "../SplitList";

// The Splits panel (split plan S1) over mocked hooks. MI, BTOC and LRHC are the rows GET /admin/splits
// returned for the REAL backfill the backend suite runs (backend/tests/app/test_admin_split_list_api.py, on
// the real Massive.com pages and filings in backend/tests/fixtures/splits): MI needs review (its window holds
// only the proxy's authorized RANGE), BTOC was auto-confirmed (SEC 8-K + Massive.com agree), LRHC 01-26 was
// rejected by the operator. DISAGREE is the matcher's direct-input contract case (BTOC's real filing
// against a Massive.com row carrying another ratio — backend/tests/domain/test_splits.py), the one shape that
// offers two Confirm buttons. The gates: nothing fires on mount, LOUD only for needs-review, set-aside rows
// stay listed and grayed, and every action has its inverse beside it.

const MI = {
  version_id: "575a1f15-3c17-4a32-9de0-c2adcd2888f6",
  security_id: "c47d4687-5c27-4dd5-b8c5-20e7f3c84b60",
  ticker: "MI",
  name: null,
  key_date: "2026-05-18",
  effective_date: "2026-05-18",
  ratio_from: "80",
  ratio_to: "1",
  price_factor: "80",
  direction: "reverse",
  label: "1-for-80 reverse split",
  status: "needs_review",
  confirmed_via: null,
  value_source: "polygon",
  author: "system:backfill_splits",
  action: "backfill",
  recorded_at: "2026-10-20T07:41:10.811647Z",
  decided_by: "the backfill",
  valid_from: "2026-05-18",
  note:
    "Massive.com only to confirm: SEC 6-K 0001213900-26-046153 (filed 2026-04-21) mentions a split but the extractor abstained — only an authorized range of ratios (one-for-two hundred, 1:200, one-for-five, 1:5) — no ratio set. 3 SEC filing(s) in the window could not be read.",
  sec: {
    accession: "0001213900-26-046153",
    form: "6-K",
    filed: "2026-04-21",
    url: "https://www.sec.gov/Archives/edgar/data/1958713/000121390026046153/0001213900-26-046153-index.htm",
    passage:
      "Share Consolidation Proposal As an ordinary resolution, subject to the approval and implementation of the Share Capital Increase, to authorize the Company’s board of directors (the “ Board of Directors ”, “ Board ” or “ Directors ”) to effect a share consolidation of the Company’s authorised and issued share capital (the “ Share Consolidation ”), at a ratio of up to one-for-two hundred (1:200), but in any case at a ratio of not less than one-for-five (1:5)",
    document: "6-K ea0286883-6k_nftlimited.htm",
    effective_date: null,
    date_kind: null,
    ratio_from: null,
    ratio_to: null,
    ratio_label: null,
    abstain_reason: "only an authorized range of ratios (one-for-two hundred, 1:200, one-for-five, 1:5) — no ratio set",
  },
  polygon: {
    id: "Ecf29c5790778cc129833171fe99752c46d272885f1c55981a3c3cc8118499dd2",
    ticker: "MI",
    execution_date: "2026-05-18",
    split_from: "80",
    split_to: "1",
    ratio_label: "1-for-80 reverse split",
  },
  confirm_options: [
    {
      source: "polygon",
      ratio_from: "80",
      ratio_to: "1",
      effective_date: "2026-05-18",
      label: "1-for-80 reverse split, first session 2026-05-18",
    },
  ],
  actions: ["confirm:polygon", "reject"],
  restore_to: null,
};

const BTOC_SEC = {
  accession: "0001213900-26-108468",
  form: "8-K",
  filed: "2026-10-09",
  url: "https://www.sec.gov/Archives/edgar/data/1972529/000121390026108468/0001213900-26-108468-index.htm",
  passage:
    "On September 21, 2026, the Board of Directors (the “ Board ”) of Armlogi Holding Corp., a Nevada corporation (the “ Company ”), approved a one-for-sixteen (1-for-16) reverse stock split of the Company’s issued and unissued shares of common stock, par value $0.00001 per share (the “ Common Stock ”) (the “ Reverse Stock Split ”). … Beginning with the opening of trading on October 15, 2026, the Company’s Common Stock will trade on a post-Reverse Stock Split basis on the Nasdaq Capital Market under the same symbol, “BTOC,” but under a new CUSIP number of 042255208.",
  document: "8-K ea0308207-8k_armlogi.htm",
  effective_date: "2026-10-15",
  date_kind: "trading",
  ratio_from: "16",
  ratio_to: "1",
  ratio_label: "1-for-16 reverse split",
  abstain_reason: null,
};

const BTOC = {
  ...MI,
  version_id: "a7e2b0a6-0363-4442-9a8a-f0ddbe292172",
  security_id: "3ea77e40-01b5-4710-a6e6-81f1bab4b889",
  ticker: "BTOC",
  key_date: "2026-10-15",
  effective_date: "2026-10-15",
  ratio_from: "16",
  price_factor: "16",
  label: "1-for-16 reverse split",
  status: "confirmed",
  confirmed_via: "auto:sec+polygon",
  value_source: "both",
  decided_by: "auto: SEC + Massive.com",
  valid_from: "2026-10-09",
  note: "SEC 8-K 0001213900-26-108468 (filed 2026-10-09): 1-for-16 reverse split, first session 2026-10-15 and Massive.com agree.",
  sec: BTOC_SEC,
  polygon: {
    id: "Eb0d633e7928bc00cc4d81c33249b70882b041aa3065f78b56abc32c72775dc2c",
    ticker: "BTOC",
    execution_date: "2026-10-15",
    split_from: "16",
    split_to: "1",
    ratio_label: "1-for-16 reverse split",
  },
  confirm_options: [],
  actions: ["revoke"],
};

const LRHC_REJECTED = {
  ...MI,
  version_id: "786a17f9-f200-4b93-bc0d-20a4591e9a9e",
  security_id: "2eeb1c00-097a-41a0-84ee-5d295d25720c",
  ticker: "LRHC",
  key_date: "2026-01-26",
  effective_date: "2026-01-26",
  ratio_from: "10",
  price_factor: "10",
  label: "1-for-10 reverse split",
  status: "rejected",
  author: "operator",
  action: "reject",
  decided_by: "rejected by you, 10-20",
  valid_from: "2026-01-26",
  note: "Massive.com only — no SEC filing read in the window states this split (0 filing(s) read).",
  sec: null,
  polygon: {
    id: "E11cfdd1b0c368c198cfac9c6e22b2e78c1de83567e22b906a2a4483afa65677b",
    ticker: "LRHC",
    execution_date: "2026-01-26",
    split_from: "10",
    split_to: "1",
    ratio_label: "1-for-10 reverse split",
  },
  confirm_options: [],
  actions: ["restore"],
  restore_to: "needs_review",
};

// D2 — SCCO's stock dividend as the nightly sets it aside (backend/tests/app/test_admin_split_list_api.py)
const SCCO_AUTO = {
  ...LRHC_REJECTED,
  version_id: "5cc0a51d-0000-4000-8000-00000000d200",
  security_id: "5cc0a51d-0000-4000-8000-00000000d201",
  ticker: "SCCO",
  key_date: "2026-11-03",
  effective_date: "2026-11-03",
  ratio_from: "1",
  ratio_to: "1.0106",
  price_factor: "0.989511",
  direction: "forward",
  label: "1.0106-for-1 forward split",
  author: "system:detect_splits",
  action: "detect",
  decided_by: "set aside automatically: a distribution",
  valid_from: "2026-11-03",
  note: "Set aside automatically: a 1.0106-for-1 forward 'split' listed by Massive.com only, under x1.5 — a stock dividend or distribution, not a split (the 2026-10-23/24 rule). Restore returns it to review. Massive.com only — no SEC filing read in the window states this split (0 filing(s) read).",
  polygon: {
    id: "E-scco-2026-11-03",
    ticker: "SCCO",
    execution_date: "2026-11-03",
    split_from: "1",
    split_to: "1.0106",
    ratio_label: "1.0106-for-1 forward split",
  },
};

const DISAGREE = {
  ...MI,
  version_id: "0b6f0c55-0000-4000-8000-0000000000d1",
  ticker: "BTOC",
  key_date: "2026-10-15",
  effective_date: "2026-10-15",
  ratio_from: "15",
  label: "1-for-15 reverse split",
  note: "SEC 8-K 0001213900-26-108468 (filed 2026-10-09): 1-for-16 reverse split, first session 2026-10-15; Massive.com: 1-for-15 reverse split, first session 2026-10-15 — the ratios differ.",
  sec: BTOC_SEC,
  polygon: {
    id: "Ex",
    ticker: "BTOC",
    execution_date: "2026-10-15",
    split_from: "15",
    split_to: "1",
    ratio_label: "1-for-15 reverse split",
  },
  confirm_options: [
    { source: "sec", ratio_from: "16", ratio_to: "1", effective_date: "2026-10-15", label: "1-for-16 reverse split, first session 2026-10-15" },
    { source: "polygon", ratio_from: "15", ratio_to: "1", effective_date: "2026-10-15", label: "1-for-15 reverse split, first session 2026-10-15" },
  ],
  actions: ["confirm:sec", "confirm:polygon", "reject"],
};

// S5 — GAME's real 08-24 step as GET /admin/splits returns it for 08-28 with GAME's 1-for-8 NOT on the list
// (backend/tests/app/test_admin_split_list_api.py, on the stored versions in backend/tests/fixtures/splits/s5)
const GAME_JUMP = {
  security_id: "0b5cccc4-8b48-4dd1-b05c-334994db1283",
  ticker: "GAME",
  prior_date: "2026-08-21",
  step_date: "2026-08-24",
  prior_close: 0.421999990940094,
  close: 3.16499996185303,
  prior_volume: 1486400,
  volume: 553700,
  step: 7.5,
  label: "GAME stepped x7.5 on 08-24",
  sessions_since: 4,
  after: [
    { d: "2026-08-25", close: 3.65000009536743, volume: 447300 },
    { d: "2026-08-26", close: 3.4300000667572, volume: 210800 },
    { d: "2026-08-27", close: 3.22000002861023, volume: 497800 },
    { d: "2026-08-28", close: 3.0, volume: 212900 },
  ],
};

// W2-8 — TREO's shape as GET /admin/splits states it (backend/tests/app/test_admin_split_list_api.py): its split's
// first session is its first stored bar, and the market cap's share count is dated before that session
const TREO = {
  ...MI,
  version_id: "7e0a0000-0000-4000-8000-0000000000f1",
  security_id: "7e0a0000-0000-4000-8000-0000000000f2",
  ticker: "TREO",
  key_date: "2026-08-04",
  effective_date: "2026-08-04",
  ratio_from: "4",
  price_factor: "4",
  label: "1-for-4 reverse split",
  note: null,
  sec: null,
  polygon: { ...MI.polygon, id: "E-treo", ticker: "TREO", execution_date: "2026-08-04", split_from: "4", ratio_label: "1-for-4 reverse split" },
  confirm_options: [
    { source: "polygon", ratio_from: "4", ratio_to: "1", effective_date: "2026-08-04", label: "1-for-4 reverse split, first session 2026-08-04" },
  ],
  first_bar: "2026-08-04",
  shares_rescaled: { count: 13524682, date: "2026-07-31" },
};

const h = vi.hoisted(() => ({
  q: {} as Record<string, unknown>,
  confirm: {} as Record<string, unknown>,
  confirmValues: {} as Record<string, unknown>,
  tape: {} as Record<string, unknown>,
  tapeCall: vi.fn(),
  reject: {} as Record<string, unknown>,
  revoke: {} as Record<string, unknown>,
  restore: {} as Record<string, unknown>,
  dismiss: {} as Record<string, unknown>,
  undoDismiss: {} as Record<string, unknown>,
  dismissUnplaced: {} as Record<string, unknown>,
  undoUnplaced: {} as Record<string, unknown>,
  addSplit: {} as Record<string, unknown>,
}));

vi.mock("../../api/hooks", () => ({
  useSplitList: () => h.q,
  useConfirmSplit: () => h.confirm,
  useConfirmSplitValues: () => h.confirmValues,
  // F2-2: the read names its target, a version ({ versionId }) or a security ({ securityId })
  useSplitTapeStep: (target: unknown, args: unknown) => {
    h.tapeCall(target, args);
    return h.tape;
  },
  useRejectSplit: () => h.reject,
  useRevokeSplit: () => h.revoke,
  useRestoreSplit: () => h.restore,
  useDismissJump: () => h.dismiss,
  useUndoJumpDismissal: () => h.undoDismiss,
  useDismissUnplaced: () => h.dismissUnplaced,
  useUndoUnplacedDismissal: () => h.undoUnplaced,
  useAddSplit: () => h.addSplit,
}));

const m = () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null });
const fn = (x: Record<string, unknown>) => (x as { mutate: ReturnType<typeof vi.fn> }).mutate;

function serve(data: Record<string, unknown>) {
  h.q = { data, isError: false, error: null };
}

/** GET /admin/splits/{id}/tape-step as the server answers it (AdminSplitTapeStepOut): a 1-for-80 reverse split's
 *  stored step at MI's first session, x80 on a x0.0125 volume. Tests spread overrides over it. */
const TAPE_STEP = {
  first_session: "2026-05-18",
  prior_date: "2026-05-15",
  prior_close: 0.42,
  close: 33.6,
  prior_volume: 1486400,
  volume: 18580,
  step: 80,
  volume_ratio: 0.0125,
  prior_served_before: false,
  price_factor: 80,
  tolerance: 0.5,
  step_low: 48.52,
  step_high: 131.9,
  within_tolerance: true,
  reason: null,
};

const tapeAnswers = (data: Record<string, unknown> | undefined) => {
  h.tape = { data, isFetching: false, isError: false, error: null };
};

beforeEach(() => {
  h.confirm = m();
  h.confirmValues = m();
  h.reject = m();
  h.revoke = m();
  h.restore = m();
  h.dismiss = m();
  h.undoDismiss = m();
  h.dismissUnplaced = m();
  h.undoUnplaced = m();
  h.addSplit = m();
  h.tapeCall.mockClear();
  tapeAnswers(undefined);
  serve({ needs_review: [MI], announced: [], confirmed: [BTOC], set_aside: [LRHC_REJECTED], total: 3 });
});

describe("SplitList", () => {
  it("renders nothing when the list is empty", () => {
    serve({ needs_review: [], announced: [], confirmed: [], set_aside: [], total: 0 });
    const { container } = render(<SplitList />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is LOUD only for what needs review, and fires nothing on mount", () => {
    render(<SplitList />);
    const review = screen.getByTestId("adm-splits-review");
    expect(review.querySelector(".adm-loud")).toHaveTextContent("1 split(s) need your review");
    expect(within(review).getAllByTestId("adm-split-review-row")).toHaveLength(1);
    // the confirmed and set-aside lists are collapsed and quiet, still present (#9 / WB#2)
    const confirmed = screen.getByTestId("adm-splits-confirmed");
    expect(confirmed).not.toHaveAttribute("open");
    expect(confirmed.querySelector(".adm-loud")).toBeNull();
    expect(confirmed).toHaveTextContent("1 confirmed");
    expect(confirmed).toHaveTextContent("BTOC — 1-for-16 reverse split · first session 2026-10-15");
    expect(confirmed).toHaveTextContent("auto: SEC + Massive.com");
    expect(screen.getByTestId("adm-splits-set-aside")).toHaveTextContent("1 rejected or revoked (kept, grayed)");
    for (const mm of [h.confirm, h.reject, h.revoke, h.restore]) expect(fn(mm)).not.toHaveBeenCalled();
  });

  it("shows the SEC passage and Massive.com's row side by side, with one Confirm when one source states both", async () => {
    render(<SplitList />);
    const row = screen.getByTestId("adm-split-review-row");
    const sec = within(row).getByTestId("adm-split-sec");
    expect(sec).toHaveTextContent("the extractor abstained — only an authorized range of ratios");
    expect(within(sec).getByTestId("adm-split-passage")).toHaveTextContent(
      "at a ratio of up to one-for-two hundred (1:200)",
    );
    expect(within(sec).getByRole("link", { name: "0001213900-26-046153" })).toHaveAttribute("href", MI.sec.url);
    const poly = within(row).getByTestId("adm-split-polygon");
    expect(poly).toHaveTextContent("MI · 1-for-80 reverse split");
    expect(poly).toHaveTextContent("first session 2026-05-18");
    expect(within(row).queryByRole("button", { name: /Confirm SEC values/ })).toBeNull();
    await userEvent.click(within(row).getByRole("button", { name: "Confirm" }));
    expect(fn(h.confirm)).toHaveBeenCalledWith({ versionId: MI.version_id, source: "polygon" });
    await userEvent.click(within(row).getByRole("button", { name: "Reject" }));
    expect(fn(h.reject)).toHaveBeenCalledWith({ versionId: MI.version_id });
  });

  it("offers both sources' values when they differ", async () => {
    serve({ needs_review: [DISAGREE], announced: [], confirmed: [], set_aside: [], total: 1 });
    render(<SplitList />);
    const row = screen.getByTestId("adm-split-review-row");
    expect(within(row).getByTestId("adm-split-sec")).toHaveTextContent("1-for-16 reverse split");
    expect(within(row).getByTestId("adm-split-sec")).toHaveTextContent("first session 2026-10-15");
    expect(within(row).getByTestId("adm-split-polygon")).toHaveTextContent("1-for-15 reverse split");
    await userEvent.click(within(row).getByRole("button", { name: "Confirm SEC values" }));
    expect(fn(h.confirm)).toHaveBeenCalledWith({ versionId: DISAGREE.version_id, source: "sec" });
    await userEvent.click(within(row).getByRole("button", { name: "Confirm Massive.com values" }));
    expect(fn(h.confirm)).toHaveBeenCalledWith({ versionId: DISAGREE.version_id, source: "polygon" });
  });

  it("keeps the inverse beside every decision: Revoke on a confirmed split, Restore on a set-aside one", async () => {
    render(<SplitList />);
    await userEvent.click(within(screen.getByTestId("adm-splits-confirmed")).getByRole("button", { name: "Revoke" }));
    expect(fn(h.revoke)).toHaveBeenCalledWith({ versionId: BTOC.version_id });
    const setAside = screen.getByTestId("adm-split-set-aside-row");
    expect(setAside).toHaveClass("adm-grayed");
    expect(setAside).toHaveTextContent("rejected · rejected by you, 10-20");
    const restore = within(setAside).getByRole("button", { name: "Restore" });
    expect(restore).toHaveAttribute("title", "back to needs review");
    await userEvent.click(restore);
    expect(fn(h.restore)).toHaveBeenCalledWith({ versionId: LRHC_REJECTED.version_id });
  });

  it("names a distribution the system set aside as such, with Restore back to review", async () => {
    serve({ needs_review: [], announced: [], confirmed: [], set_aside: [SCCO_AUTO, LRHC_REJECTED], total: 2 });
    render(<SplitList />);
    const [auto, mine] = screen.getAllByTestId("adm-split-set-aside-row");
    expect(auto).toHaveTextContent("SCCO");
    expect(auto).toHaveTextContent("set aside automatically: a distribution");
    expect(auto).not.toHaveTextContent("rejected ·");
    expect(mine).toHaveTextContent("rejected · rejected by you, 10-20");
    const restore = within(auto).getByRole("button", { name: "Restore" });
    expect(restore).toHaveAttribute("title", "back to needs review");
    await userEvent.click(restore);
    expect(fn(h.restore)).toHaveBeenCalledWith({ versionId: SCCO_AUTO.version_id });
  });

  it("asks 'is this real?' about a released split-shaped jump no source listed — loud, one Dismiss", async () => {
    serve({ needs_review: [], announced: [], confirmed: [], set_aside: [], total: 0, unlisted_jumps: [GAME_JUMP] });
    render(<SplitList />);
    const box = screen.getByTestId("adm-splits-jumps");
    expect(box.querySelector(".adm-loud")).toHaveTextContent("1 split-shaped jump(s) with no split listed — is this real?");
    const row = within(box).getByTestId("adm-split-jump-row");
    expect(row).toHaveTextContent("GAME — stepped x7.5 on 08-24 · 4 sessions ago");
    expect(row).toHaveTextContent("08-21 close 0.422 · vol 1,486,400 → 08-24 close 3.165 · vol 553,700");
    expect(row).toHaveTextContent("then 08-25 3.65 · 08-26 3.43 · 08-27 3.22 · 08-28 3");
    for (const mm of [h.confirm, h.reject, h.revoke, h.restore, h.dismiss]) expect(fn(mm)).not.toHaveBeenCalled();
    // its one action says what it does — and that no call changes
    expect(within(row).getByTestId("adm-split-jump-effect")).toHaveTextContent(
      "This was a real move: the card leaves this list (it stays under “dismissed”, with an Undo). No call changes",
    );
    await userEvent.click(within(row).getByRole("button", { name: "Dismiss" }));
    expect(fn(h.dismiss)).toHaveBeenCalledWith({ securityId: GAME_JUMP.security_id, stepDate: "2026-08-24" });
    expect(screen.queryByTestId("adm-splits-jumps-dismissed")).toBeNull(); // nothing dismissed yet
  });

  it("keeps a dismissed jump behind the 'dismissed' filter, with its Undo — hidden, never gone", async () => {
    const DISMISSED = {
      ...GAME_JUMP,
      dismissal_id: "5d1b2c3e-0000-4000-8000-0000000000aa",
      dismissed_at: "2026-08-28T15:02:00Z",
      dismissed_reason: "a real squeeze",
    };
    serve({ needs_review: [], announced: [], confirmed: [], set_aside: [], total: 0, dismissed_jumps: [DISMISSED] });
    render(<SplitList />);
    expect(screen.queryByTestId("adm-splits-jumps")).toBeNull(); // off the loud list
    const filter = screen.getByTestId("adm-splits-jumps-dismissed");
    expect(within(filter).queryByTestId("adm-split-jump-dismissed-row")).toBeNull(); // the filter starts closed
    await userEvent.click(within(filter).getByRole("button", { name: "Show dismissed (1)" }));
    const row = within(filter).getByTestId("adm-split-jump-dismissed-row");
    expect(row).toHaveTextContent("dismissed by you as a real move, 08-28 — “a real squeeze”");
    await userEvent.click(within(row).getByRole("button", { name: "Undo" }));
    expect(fn(h.undoDismiss)).toHaveBeenCalledWith({ dismissalId: DISMISSED.dismissal_id });
    await userEvent.click(within(filter).getByRole("button", { name: "Hide dismissed (1)" }));
    expect(within(filter).queryByTestId("adm-split-jump-dismissed-row")).toBeNull();
  });

  it("says in plain words what the split is, what each source says and what each button does", () => {
    render(<SplitList />);
    const row = screen.getByTestId("adm-split-review-row");
    expect(within(row).getByTestId("adm-split-what")).toHaveTextContent(
      "1-for-80 reverse split: every 80 shares became 1; first traded on the new basis 2026-05-18.",
    );
    expect(within(row).getByTestId("adm-split-sec-says")).toHaveTextContent(
      "The SEC 6-K filed 2026-04-21 was found, but no single ratio could be read from it (only an authorized range of ratios",
    );
    expect(within(row).getByTestId("adm-split-polygon-says")).toHaveTextContent(
      "Massive.com lists 1-for-80 reverse split, first trading 2026-05-18.",
    );
    const [confirmLine, rejectLine] = within(row).getAllByTestId("adm-split-effect");
    expect(confirmLine).toHaveTextContent(
      "Confirm 1-for-80 reverse split, first session 2026-05-18: prices before 05-18 are put on the new scale",
    );
    expect(rejectLine).toHaveTextContent("Reject: no split; prices stay as stored.");
  });

  it("words each source's Confirm when the SEC filing and Massive.com differ", () => {
    serve({ needs_review: [DISAGREE], announced: [], confirmed: [], set_aside: [], total: 1 });
    render(<SplitList />);
    const row = screen.getByTestId("adm-split-review-row");
    expect(within(row).getByTestId("adm-split-sec-says")).toHaveTextContent(
      "The SEC 8-K filed 2026-10-09 says 1-for-16 reverse split, first trading on the new basis 2026-10-15.",
    );
    const lines = within(row).getAllByTestId("adm-split-effect").map((x) => x.textContent);
    expect(lines[0]).toContain("Confirm the SEC filing's values (1-for-16 reverse split, first session 2026-10-15)");
    expect(lines[1]).toContain("Confirm Massive.com's values (1-for-15 reverse split, first session 2026-10-15)");
  });

  // D3 (W2-2) — "Confirm with other values": the steward's own ratio, first session and cited document, for a split
  // the two sources could not settle. The server (W2-1) checks the stored tape's step at that session and answers
  // 409 outside the tolerance unless the body says override_tape; the form reads the same check from GET
  // .../tape-step and shows it beside the entered factor. The write's body is exactly AdminSplitConfirmValuesIn.
  describe("Confirm with other values", () => {
    const CITED = "https://www.sec.gov/Archives/edgar/data/1/000121390026099999/ex99-1.htm";
    const OPEN = "Confirm with other values";
    const SUBMIT = "Confirm these values";
    // POM's shape (the plan's D3 case): 10 ADSs -> 3 shares, the tape stepped x4.53, inside the tolerance of x3.33
    const POM_TAPE = {
      ...TAPE_STEP,
      first_session: "2026-09-24",
      prior_date: "2026-09-23",
      prior_close: 1.2,
      close: 5.43,
      step: 4.53,
      volume_ratio: 0.22,
      price_factor: 3.3333,
      step_low: 2.02,
      step_high: 5.5,
    };

    const row = () => screen.getByTestId("adm-split-review-row");
    const openForm = async () => userEvent.click(within(row()).getByRole("button", { name: OPEN }));
    async function enter(f: { from?: string; to?: string; session?: string; url?: string; note?: string }) {
      const r = within(row());
      if (f.from) await userEvent.type(r.getByLabelText("Old shares"), f.from);
      if (f.to) await userEvent.type(r.getByLabelText("New shares"), f.to);
      if (f.session) await userEvent.type(r.getByLabelText("First session"), f.session);
      if (f.url) await userEvent.type(r.getByLabelText("Cited document"), f.url);
      if (f.note) await userEvent.type(r.getByLabelText("Note"), f.note);
    }

    it("is offered on every needs-review split and closed until asked: nothing is read or sent", () => {
      serve({ needs_review: [MI, DISAGREE], announced: [], confirmed: [BTOC], set_aside: [LRHC_REJECTED], total: 4 });
      render(<SplitList />);
      const rows = screen.getAllByTestId("adm-split-review-row");
      expect(rows).toHaveLength(2);
      for (const r of rows) {
        expect(within(r).getByRole("button", { name: OPEN })).toBeEnabled();
        expect(within(r).queryByLabelText("Old shares")).toBeNull();
        expect(within(r).getByTestId("adm-split-values-effect")).toHaveTextContent(
          "Confirm with other values: you type the ratio and first session from a document you cite",
        );
      }
      expect(screen.getAllByRole("button", { name: OPEN })).toHaveLength(2); // not on the confirmed or set-aside rows
      expect(h.tapeCall).not.toHaveBeenCalled();
      expect(fn(h.confirmValues)).not.toHaveBeenCalled();
    });

    it("posts the confirm-values body: ratio, first session, cited https URL, note, no override inside the tolerance", async () => {
      tapeAnswers(POM_TAPE);
      render(<SplitList />);
      await openForm();
      const r = within(row());
      // the first session is what the tape is read at: nothing is read, and nothing shown, before it is entered
      expect(h.tapeCall).toHaveBeenLastCalledWith({ versionId: MI.version_id }, null);
      expect(r.queryByTestId("adm-split-tape-step")).toBeNull();
      expect(r.getByRole("button", { name: SUBMIT })).toBeDisabled();

      await enter({ from: "10", to: "3", session: "2026-09-24" });
      expect(h.tapeCall).toHaveBeenLastCalledWith({ versionId: MI.version_id }, {
        firstSession: "2026-09-24",
        ratioFrom: "10",
        ratioTo: "3",
      });
      const step = r.getByTestId("adm-split-tape-step");
      expect(step).toHaveTextContent("The stored tape stepped x4.53 on 09-24: close 1.2 on 09-23 → 5.43, volume x0.22.");
      expect(step).toHaveTextContent("Your values: every 10 shares became 3, a price factor of x3.33; the tape accepts x2.02 to x5.5.");
      expect(step).toHaveTextContent("The tape supports your values.");
      expect(r.queryByRole("checkbox")).toBeNull(); // inside the tolerance: no override to offer
      expect(r.getByRole("button", { name: SUBMIT })).toBeDisabled(); // the cited document is still missing
      expect(r.getByTestId("adm-split-values-hint")).toHaveTextContent("Cite the document");

      await enter({ url: CITED, note: "6-K and Nasdaq alert: 10 ADSs for 3 shares" });
      await userEvent.click(r.getByRole("button", { name: SUBMIT }));
      expect(fn(h.confirmValues)).toHaveBeenCalledTimes(1);
      expect(fn(h.confirmValues)).toHaveBeenCalledWith({
        versionId: MI.version_id,
        body: {
          ratio_from: "10",
          ratio_to: "3",
          first_session: "2026-09-24",
          cited_url: CITED,
          note: "6-K and Nasdaq alert: 10 ADSs for 3 shares",
          override_tape: false,
        },
      });
      expect(fn(h.confirm)).not.toHaveBeenCalled();
    });

    it("leaves the note out of the body when none is typed", async () => {
      tapeAnswers(POM_TAPE);
      render(<SplitList />);
      await openForm();
      await enter({ from: "10", to: "3", session: "2026-09-24", url: CITED });
      await userEvent.click(within(row()).getByRole("button", { name: SUBMIT }));
      expect(fn(h.confirmValues)).toHaveBeenCalledWith({
        versionId: MI.version_id,
        body: { ratio_from: "10", ratio_to: "3", first_session: "2026-09-24", cited_url: CITED, override_tape: false },
      });
    });

    it("reads the stored step as soon as a first session is entered, before any ratio, and gives no verdict", async () => {
      tapeAnswers({
        ...POM_TAPE,
        price_factor: null,
        step_low: null,
        step_high: null,
        within_tolerance: null,
        reason: null,
      });
      render(<SplitList />);
      await openForm();
      await enter({ session: "2026-09-24" });
      expect(h.tapeCall).toHaveBeenLastCalledWith({ versionId: MI.version_id }, { firstSession: "2026-09-24" });
      const step = within(row()).getByTestId("adm-split-tape-step");
      expect(step).toHaveTextContent("The stored tape stepped x4.53 on 09-24");
      expect(step).not.toHaveTextContent("Your values");
      expect(within(row()).queryByRole("checkbox")).toBeNull();
    });

    it("keeps Confirm off until the cited document is an https address", async () => {
      tapeAnswers(POM_TAPE);
      render(<SplitList />);
      await openForm();
      const r = within(row());
      await enter({ from: "10", to: "3", session: "2026-09-24" });
      const submit = r.getByRole("button", { name: SUBMIT });
      expect(submit).toBeDisabled();
      await enter({ url: "http://example.com/notice" });
      expect(submit).toBeDisabled();
      expect(r.getByTestId("adm-split-values-hint")).toHaveTextContent("https://");
      await userEvent.clear(r.getByLabelText("Cited document"));
      await enter({ url: "https://example.com/notice" });
      expect(submit).toBeEnabled();
      expect(r.queryByTestId("adm-split-values-hint")).toBeNull();
    });

    it("holds Confirm for a ratio that is not a split: a word, an equal pair, zero", async () => {
      tapeAnswers(POM_TAPE);
      render(<SplitList />);
      await openForm();
      const r = within(row());
      await enter({ to: "3", session: "2026-09-24", url: CITED });
      expect(r.getByRole("button", { name: SUBMIT })).toBeDisabled();
      expect(r.getByTestId("adm-split-values-hint")).toHaveTextContent("old and the new share counts");
      await enter({ from: "3" });
      expect(r.getByRole("button", { name: SUBMIT })).toBeDisabled();
      expect(r.getByTestId("adm-split-values-hint")).toHaveTextContent("not a split");
      await userEvent.clear(r.getByLabelText("Old shares"));
      await enter({ from: "0" });
      expect(r.getByRole("button", { name: SUBMIT })).toBeDisabled();
      await userEvent.clear(r.getByLabelText("Old shares"));
      await enter({ from: "ten" });
      expect(r.getByRole("button", { name: SUBMIT })).toBeDisabled();
      await userEvent.clear(r.getByLabelText("Old shares"));
      await enter({ from: "10" });
      expect(r.getByRole("button", { name: SUBMIT })).toBeEnabled();
    });

    it("outside the tolerance: shows the stored step beside the factor and the override box, and Confirm waits for it", async () => {
      tapeAnswers({
        ...POM_TAPE,
        price_factor: 80,
        step_low: 48.52,
        step_high: 131.9,
        within_tolerance: false,
        reason: "the tape steps x4.53 on 2026-09-24, not within x1.65 of the entered x80 (1-for-80 reverse split)",
      });
      render(<SplitList />);
      await openForm();
      const r = within(row());
      await enter({ from: "80", to: "1", session: "2026-09-24", url: CITED });
      const step = r.getByTestId("adm-split-tape-step");
      expect(step).toHaveTextContent("The stored tape stepped x4.53 on 09-24");
      expect(step).toHaveTextContent("a price factor of x80; the tape accepts x48.5 to x132.");
      expect(step).toHaveTextContent("The tape does not support your values.");
      const box = r.getByRole("checkbox", { name: /confirm anyway/i });
      expect(box).not.toBeChecked();
      expect(r.getByRole("button", { name: SUBMIT })).toBeDisabled();
      expect(fn(h.confirmValues)).not.toHaveBeenCalled();

      await userEvent.click(box);
      await userEvent.click(r.getByRole("button", { name: SUBMIT }));
      expect(fn(h.confirmValues)).toHaveBeenCalledWith({
        versionId: MI.version_id,
        body: { ratio_from: "80", ratio_to: "1", first_session: "2026-09-24", cited_url: CITED, override_tape: true },
      });
    });

    it("offers the override when the tape has no bar to read, and drops it again when the values fit", async () => {
      tapeAnswers({
        first_session: "2026-09-24",
        step: null,
        price_factor: 3.3333,
        step_low: 2.02,
        step_high: 5.5,
        tolerance: 0.5,
        within_tolerance: false,
        reason: "no stored bar on 2026-09-24 (with one in the 14 days before it) to read",
      });
      const { rerender } = render(<SplitList />);
      await openForm();
      await enter({ from: "10", to: "3", session: "2026-09-24", url: CITED });
      const r = within(row());
      expect(r.getByTestId("adm-split-tape-step")).toHaveTextContent("The stored tape has no bar to read on 2026-09-24");
      expect(r.getByRole("checkbox", { name: /confirm anyway/i })).toBeInTheDocument();
      await userEvent.click(r.getByRole("checkbox", { name: /confirm anyway/i }));

      // the values are changed and now fit: the box leaves, and a ticked box is NOT sent as an override
      tapeAnswers(POM_TAPE);
      rerender(<SplitList />);
      expect(within(row()).queryByRole("checkbox")).toBeNull();
      await userEvent.click(within(row()).getByRole("button", { name: SUBMIT }));
      expect(fn(h.confirmValues)).toHaveBeenCalledWith(
        expect.objectContaining({ body: expect.objectContaining({ override_tape: false }) }),
      );
    });

    it("shows the server's sentence for a refused confirm (409) in the form, once", async () => {
      const sentence =
        "the tape steps x4.53 on 2026-09-24, not within x1.65 of the entered x80 (1-for-80 reverse split)";
      h.confirmValues = {
        ...m(),
        isError: true,
        error: { detail: sentence },
        variables: { versionId: MI.version_id, body: {} },
      };
      render(<SplitList />);
      expect(screen.queryByTestId("adm-split-values-error")).toBeNull(); // the form is closed
      await openForm();
      expect(within(row()).getByTestId("adm-split-values-error")).toHaveTextContent(sentence);
      expect(screen.getAllByText(sentence)).toHaveLength(1); // not repeated at the foot of the panel
    });

    it("shows another split's refusal only in that split's form", async () => {
      serve({ needs_review: [MI, DISAGREE], announced: [], confirmed: [], set_aside: [], total: 2 });
      h.confirmValues = {
        ...m(),
        isError: true,
        error: { detail: "this split changed since you looked — reload the panel" },
        variables: { versionId: DISAGREE.version_id, body: {} },
      };
      render(<SplitList />);
      const [first, second] = screen.getAllByTestId("adm-split-review-row");
      await userEvent.click(within(first).getByRole("button", { name: OPEN }));
      await userEvent.click(within(second).getByRole("button", { name: OPEN }));
      expect(within(first).queryByTestId("adm-split-values-error")).toBeNull();
      expect(within(second).getByTestId("adm-split-values-error")).toHaveTextContent("this split changed since you looked");
    });

    it("disables every decision while the values are being sent", async () => {
      h.confirmValues = { ...m(), isPending: true };
      render(<SplitList />);
      await openForm();
      const r = within(row());
      expect(r.getByRole("button", { name: "Confirm" })).toBeDisabled();
      expect(r.getByRole("button", { name: "Reject" })).toBeDisabled();
      expect(r.getByRole("button", { name: SUBMIT })).toBeDisabled();
    });

    it("closes with Cancel, and opens again empty", async () => {
      render(<SplitList />);
      await openForm();
      await enter({ from: "10" });
      await userEvent.click(within(row()).getByRole("button", { name: "Cancel" }));
      expect(within(row()).queryByLabelText("Old shares")).toBeNull();
      await openForm();
      expect(within(row()).getByLabelText("Old shares")).toHaveValue(""); // a fresh form, nothing carried over
      expect(fn(h.confirmValues)).not.toHaveBeenCalled();
    });

    it("lists a steward's cited values with the document and says when the tape was overridden", () => {
      const STEWARD = {
        ...BTOC,
        version_id: "0b6f0c55-0000-4000-8000-0000000000e1",
        ticker: "POM",
        label: "3-for-10 reverse split",
        value_source: "steward",
        confirmed_via: "operator",
        decided_by: "you (cited values), 11-25",
        note: "6-K and Nasdaq alert",
        cited_url: "https://example.com/pom-notice",
        tape_override: true,
        sec: null,
        polygon: null,
      };
      serve({ needs_review: [], announced: [], confirmed: [BTOC, STEWARD], set_aside: [], total: 2 });
      render(<SplitList />);
      const rows = screen.getAllByTestId("adm-split-confirmed-row");
      expect(within(rows[0]).queryByRole("link", { name: /cited document/ })).toBeNull();
      expect(within(rows[1]).getByRole("link", { name: /cited document/ })).toHaveAttribute(
        "href",
        "https://example.com/pom-notice",
      );
      expect(rows[1]).toHaveTextContent("you (cited values), 11-25");
      expect(rows[1]).toHaveTextContent("confirmed past the tape");
      expect(rows[0]).not.toHaveTextContent("past the tape");
    });
  });

  // W2-8 — two hints from the API, said once per open split: a first session on or before the first stored bar adjusts
  // nothing on the tape, and the share count a confirm rescales (and by how much) is named so its basis can be checked
  it("says when a split adjusts no stored bar, and names the share count a confirm rescales", () => {
    const LATER = { ...MI, first_bar: "2026-01-02", shares_rescaled: null }; // bars before it, no count to rescale
    const AHEAD = {
      ...BTOC,
      version_id: "0b6f0c55-0000-4000-8000-0000000000f3",
      ticker: "NVA",
      status: "announced",
      ratio_from: "1",
      ratio_to: "2",
      price_factor: "0.5",
      label: "2-for-1 forward split",
      effective_date: "2026-12-01",
      first_bar: "2024-05-01",
      shares_rescaled: { count: 1000000, date: "2026-09-30" },
    };
    serve({ needs_review: [TREO, LATER], announced: [AHEAD], confirmed: [BTOC], set_aside: [], total: 4 });
    render(<SplitList />);
    const [treo, mi] = screen.getAllByTestId("adm-split-review-row");
    expect(within(treo).getByTestId("adm-split-hint")).toHaveTextContent(
      "TREO's first session is its first bar: nothing on the tape to adjust; confirming divides 13,524,682 shares dated 2026-07-31 by 4 — check that count's basis.",
    );
    expect(within(mi).queryByTestId("adm-split-hint")).toBeNull();
    expect(within(screen.getByTestId("adm-splits-announced")).getByTestId("adm-split-hint")).toHaveTextContent(
      "Once in force it multiplies 1,000,000 shares dated 2026-09-30 by 2 — check that count's basis.",
    );
    expect(within(screen.getByTestId("adm-splits-confirmed")).queryByTestId("adm-split-hint")).toBeNull();
    // display only: the decisions on the card are the same
    expect(within(treo).getByRole("button", { name: "Confirm" })).toBeEnabled();
    expect(fn(h.confirm)).not.toHaveBeenCalled();
  });

  it("shows a failed jump check instead of hiding it", () => {
    serve({ needs_review: [], announced: [], confirmed: [], set_aside: [], total: 0, unlisted_jumps_error: "OperationalError: x" });
    render(<SplitList />);
    expect(screen.getByTestId("adm-splits-jumps-error")).toHaveTextContent("The split-shaped jump check failed: OperationalError: x");
  });

  it("names Massive.com's own label beside its ratio when the row carries one (W6-1)", () => {
    const labeled = { ...MI, polygon: { ...MI.polygon, adjustment_type: "stock_dividend" } };
    serve({ needs_review: [labeled], announced: [], confirmed: [], set_aside: [], total: 1 });
    render(<SplitList />);
    const row = screen.getByTestId("adm-split-review-row");
    expect(within(row).getByTestId("adm-split-polygon-says")).toHaveTextContent(
      "Massive.com lists 1-for-80 reverse split, first trading 2026-05-18; it labels it a stock dividend.",
    );
  });

  it("shows a failed read and a refused action", () => {
    h.q = { data: undefined, isError: true, error: { detail: "boom" } };
    const { unmount } = render(<SplitList />);
    expect(screen.getByTestId("adm-splits")).toHaveTextContent("Splits");
    unmount();
    serve({ needs_review: [MI], announced: [], confirmed: [], set_aside: [], total: 1 });
    h.confirm = { ...m(), isError: true, error: { detail: "this split changed since you looked — reload the panel" } };
    render(<SplitList />);
    expect(screen.getByTestId("adm-splits")).toHaveTextContent("this split changed since you looked");
  });
});

// F8-3 — APUS's shape as GET /admin/splits states it (backend/tests/app/test_admin_split_list_api.py): a needs-review
// 1-for-10 on 2025-03-26 and a confirmed 1-for-10 on 2025-07-24 of the same company. An open split names the other
// splits on the list of its exact ratio within 180 days (same_ratio_listed, nearest first, at most two); a settled one
// carries none. Display only: nothing merges the two, the steward settles them (D-F8c).
const APUS_SID = "a9050000-0000-4000-8000-0000000000a0";
const APUS_MARCH_ID = "a9050000-0000-4000-8000-0000000000b1";
const APUS_JULY_ID = "a9050000-0000-4000-8000-0000000000b2";
const apusPolygon = (d: string) => ({
  ...MI.polygon,
  id: `E-apus-${d}`,
  ticker: "APUS",
  execution_date: d,
  split_from: "10",
  ratio_label: "1-for-10 reverse split",
});
const sameNear = (over: Record<string, unknown>) => ({
  version_id: APUS_JULY_ID,
  key_date: "2025-07-24",
  effective_date: "2025-07-24",
  label: "1-for-10 reverse split",
  status: "confirmed",
  same_ratio: true,
  cites_filing: false,
  days_from: 120,
  ...over,
});
const APUS_MARCH = {
  ...TREO,
  version_id: APUS_MARCH_ID,
  security_id: APUS_SID,
  ticker: "APUS",
  name: "Apus Corp",
  key_date: "2025-03-26",
  effective_date: "2025-03-26",
  ratio_from: "10",
  price_factor: "10",
  label: "1-for-10 reverse split",
  valid_from: "2025-03-26",
  polygon: apusPolygon("2025-03-26"),
  confirm_options: [
    { source: "polygon", ratio_from: "10", ratio_to: "1", effective_date: "2025-03-26", label: "1-for-10 reverse split, first session 2025-03-26" },
  ],
  first_bar: null,
  shares_rescaled: null,
  same_ratio_listed: [sameNear({})],
};
const APUS_JULY = {
  ...BTOC,
  version_id: APUS_JULY_ID,
  security_id: APUS_SID,
  ticker: "APUS",
  name: "Apus Corp",
  key_date: "2025-07-24",
  effective_date: "2025-07-24",
  ratio_from: "10",
  price_factor: "10",
  label: "1-for-10 reverse split",
  valid_from: "2025-07-24",
  note: null,
  sec: null,
  polygon: apusPolygon("2025-07-24"),
  same_ratio_listed: null,
};

describe("SplitList: a listed split of the same ratio nearby (F8-3)", () => {
  const sameLine = (row: HTMLElement) => within(row).queryByTestId("adm-split-same-ratio");

  it("names the confirmed split of its ratio 120 days later on the open APUS row, and nothing on the settled one", () => {
    serve({ needs_review: [APUS_MARCH], announced: [], confirmed: [APUS_JULY], set_aside: [], total: 2 });
    render(<SplitList />);
    expect(sameLine(screen.getByTestId("adm-split-review-row"))).toHaveTextContent(
      "The list also holds a confirmed 1-for-10 reverse split on 07-24, 120 days later: check this one went ahead.",
    );
    expect(sameLine(screen.getByTestId("adm-split-confirmed-row"))).toBeNull();
    expect(screen.getAllByTestId("adm-split-same-ratio")).toHaveLength(1);
    // display only: the decisions on the card are today's, and nothing is sent
    expect(within(screen.getByTestId("adm-split-review-row")).getByRole("button", { name: "Confirm" })).toBeEnabled();
    expect(fn(h.confirm)).not.toHaveBeenCalled();
    expect(fn(h.reject)).not.toHaveBeenCalled();
  });

  it("words the other way round: the open July row names the confirmed March split, 120 days earlier", () => {
    const july = {
      ...APUS_MARCH,
      version_id: APUS_JULY_ID,
      key_date: "2025-07-24",
      effective_date: "2025-07-24",
      same_ratio_listed: [sameNear({ version_id: APUS_MARCH_ID, key_date: "2025-03-26", effective_date: "2025-03-26", days_from: -120 })],
    };
    const march = { ...APUS_JULY, version_id: APUS_MARCH_ID, key_date: "2025-03-26", effective_date: "2025-03-26" };
    serve({ needs_review: [july], announced: [], confirmed: [march], set_aside: [], total: 2 });
    render(<SplitList />);
    expect(sameLine(screen.getByTestId("adm-split-review-row"))).toHaveTextContent(
      "The list also holds a confirmed 1-for-10 reverse split on 03-26, 120 days earlier: check this one went ahead.",
    );
  });

  it("names two in the server's order on an announced row: each status, one day, the same day, and a shared filing", () => {
    const ahead = {
      ...APUS_JULY,
      status: "announced",
      same_ratio_listed: [
        sameNear({ status: "needs_review", effective_date: "2025-07-23", days_from: -1, cites_filing: true }),
        sameNear({ status: "rejected", effective_date: "2025-03-26", days_from: -120 }),
      ],
    };
    const sameDay = {
      ...APUS_MARCH,
      same_ratio_listed: [sameNear({ status: "announced", effective_date: "2025-03-26", days_from: 0 })],
    };
    serve({ needs_review: [sameDay], announced: [ahead], confirmed: [], set_aside: [], total: 2 });
    render(<SplitList />);
    expect(sameLine(screen.getByTestId("adm-splits-announced"))).toHaveTextContent(
      "The list also holds a needs-review 1-for-10 reverse split on 07-23, 1 day earlier, citing the same SEC filing, and a rejected one on 03-26, 120 days earlier: check this one went ahead.",
    );
    expect(sameLine(screen.getByTestId("adm-split-review-row"))).toHaveTextContent(
      "The list also holds an announced 1-for-10 reverse split on 03-26, the same day: check this one went ahead.",
    );
  });

  it("says nothing for an empty list, and an older server's payload (no key) renders today's panel", () => {
    const { same_ratio_listed: _drop, ...marchOld } = APUS_MARCH;
    const { same_ratio_listed: _gone, ...julyOld } = APUS_JULY;
    serve({ needs_review: [marchOld], announced: [], confirmed: [julyOld], set_aside: [], total: 2 });
    const old = render(<SplitList />);
    const html = old.container.innerHTML;
    old.unmount();
    serve({
      needs_review: [{ ...marchOld, same_ratio_listed: null }],
      announced: [],
      confirmed: [{ ...julyOld, same_ratio_listed: null }],
      set_aside: [],
      total: 2,
    });
    const none = render(<SplitList />);
    expect(none.container.innerHTML).toBe(html);
    none.unmount();
    serve({ needs_review: [{ ...marchOld, same_ratio_listed: [] }], announced: [], confirmed: [julyOld], set_aside: [], total: 2 });
    const empty = render(<SplitList />);
    expect(empty.container.innerHTML).toBe(html);
    expect(html).not.toContain("adm-split-same-ratio");
    expect(html).toContain("adm-split-review-row");
  });
});

// F10 P6 — a filing that may call a listed split off, as GET /admin/splits carries it (called_off, any status; backend
// tests/app/test_admin_split_list_api.py): AIRI's confirmed 1-for-5, first session 2026-11-20, and the 8-K it filed the
// day before ("would not be effecting the reverse stock split"; the passage is the 8-K's own sentence, copied from the
// backend fixture). ATCH's failed vote and the other shapes are SYNTHETIC. Display only: the one control is the
// existing Revoke (or Reject, while announced); nothing here sends.
const AIRI_PASSAGE =
  "On November 19, 2026, Air Industries Group, announced that it would not be effecting the reverse stock " +
  "split (the \u201cReverse Stock Split\u201d) of its authorized, issued and outstanding shares of common stock, " +
  "par value $0.001 per share, at a ratio of 1-for-5 (the \u201cReverse Stock Split\u201d) in connection with the " +
  "Company\u2019s proposed merger with Tenax Aerospace Acquisition, LLC (\u201cTenax\u201d) that had been announced " +
  "on November 10, 2026.";
// the passage cut at the closing parenthesis after the ratio (a clause end past the halfway), not mid-clause
const AIRI_CUT =
  "On November 19, 2026, Air Industries Group, announced that it would not be effecting the reverse stock " +
  "split (the \u201cReverse Stock Split\u201d) of its authorized, issued and outstanding shares of common stock, " +
  "par value $0.001 per share, at a ratio of 1-for-5 (the \u201cReverse Stock Split\u201d)\u2026";
const AIRI_8K = "0001213900-26-122096";
const AIRI_URL = `https://www.sec.gov/Archives/edgar/data/1009891/000121390026122096/${AIRI_8K}-index.htm`;
const calledOff = (over: Record<string, unknown> = {}) => ({
  accession: AIRI_8K,
  form: "8-K",
  filed: "2026-11-19",
  url: AIRI_URL,
  kind: "decision",
  ratio_label: "1-for-5 reverse split",
  same_ratio: true,
  days_from: -1,
  document: "ea0312829-8k_airindustries.htm",
  passage: AIRI_PASSAGE,
  source: "nightly",
  seen_on: "2026-11-25",
  ...over,
});
const AIRI = {
  ...BTOC,
  version_id: "a1210000-0000-4000-8000-0000000000a1",
  security_id: "a1210000-0000-4000-8000-0000000000a2",
  ticker: "AIRI",
  name: "Air Industries Group",
  key_date: "2026-11-20",
  effective_date: "2026-11-20",
  ratio_from: "5",
  price_factor: "5",
  label: "1-for-5 reverse split",
  valid_from: "2026-11-10",
  note: null,
  sec: null,
  polygon: { ...BTOC.polygon, id: "E-airi", ticker: "AIRI", execution_date: "2026-11-20", split_from: "5", ratio_label: "1-for-5 reverse split" },
  called_off: [calledOff()],
};
const ATCH = {
  ...AIRI,
  version_id: "a7c40000-0000-4000-8000-0000000000a1",
  security_id: "a7c40000-0000-4000-8000-0000000000a2",
  ticker: "ATCH",
  name: null,
  effective_date: "2026-11-12",
  key_date: "2026-11-12",
  polygon: { ...AIRI.polygon, id: "E-atch", ticker: "ATCH", execution_date: "2026-11-12" },
  called_off: [
    calledOff({
      accession: "0000000000-26-000011",
      form: "6-K",
      filed: "2026-11-12",
      kind: "failed_vote",
      ratio_label: null,
      same_ratio: null,
      days_from: 0,
      passage: "The Reverse Stock Split Proposal was not approved.",
    }),
  ],
};

describe("SplitList: a filing that may call a listed split off (F10)", () => {
  const lines = (row: HTMLElement) => within(row).queryAllByTestId("adm-split-called-off");
  const open = (ui: Record<string, unknown>) => {
    serve({ needs_review: [], announced: [], confirmed: [], set_aside: [], total: 1, ...ui });
    return render(<SplitList />);
  };

  it("names the 8-K that called AIRI's confirmed split off, the day before its first session, with its link", () => {
    open({ confirmed: [AIRI] });
    const row = screen.getByTestId("adm-split-confirmed-row");
    expect(lines(row)).toHaveLength(1);
    expect(lines(row)[0]).toHaveTextContent(
      `Called off? 8-K filed 2026-11-19 (1 day before the first session; states this split's ratio): \u201c${AIRI_CUT}\u201d`,
    );
    const link = within(lines(row)[0]).getByRole("link", { name: "8-K filed 2026-11-19" });
    expect(link).toHaveAttribute("href", AIRI_URL);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    // display only: the one control is the existing Revoke, enabled, and nothing was sent
    expect(within(row).getByRole("button", { name: "Revoke" })).toBeEnabled();
    for (const mm of [h.confirm, h.reject, h.revoke, h.restore]) expect(fn(mm)).not.toHaveBeenCalled();
  });

  it("says each fact once: the filing, its date, the distance and the sentence appear once in the entry", () => {
    open({ confirmed: [AIRI] });
    const text = screen.getByTestId("adm-split-confirmed-row").textContent ?? "";
    for (const once of ["2026-11-19", "8-K", "1 day before", "would not be effecting", "1-for-5 reverse split"]) {
      expect(text.split(once).length - 1, once).toBe(1);
    }
  });

  it("shows it on an announced entry, and on a needs-review one, in the same words", () => {
    const other = calledOff({ days_from: -3, same_ratio: false, ratio_label: "1-for-3 reverse split" });
    open({
      announced: [{ ...AIRI, status: "announced", decided_by: "auto: SEC + Massive.com" }],
      needs_review: [{ ...MI, called_off: [other] }],
      total: 2,
    });
    expect(lines(screen.getByTestId("adm-splits-announced"))[0]).toHaveTextContent(
      `Called off? 8-K filed 2026-11-19 (1 day before the first session; states this split's ratio): \u201c${AIRI_CUT}\u201d`,
    );
    expect(lines(screen.getByTestId("adm-split-review-row"))[0]).toHaveTextContent(
      "Called off? 8-K filed 2026-11-19 (3 days before the first session; states a different ratio, 1-for-3 reverse split): \u201cOn November 19",
    );
    // the announced entry keeps its own control, Reject, and the call-off adds none
    expect(within(screen.getByTestId("adm-splits-announced")).getByRole("button", { name: "Reject" })).toBeEnabled();
  });

  it("shows it on a rejected or revoked entry too, grayed with its row: Restore reads it before bringing it back", () => {
    open({ set_aside: [{ ...LRHC_REJECTED, called_off: [calledOff()] }] });
    const row = screen.getByTestId("adm-split-set-aside-row");
    expect(row).toHaveClass("adm-grayed");
    expect(lines(row)).toHaveLength(1);
    expect(within(row).getByRole("button", { name: "Restore" })).toBeEnabled();
  });

  it("is absent when called_off is null or empty, and an older server's payload (no key) renders today's panel", () => {
    const { called_off: _drop, ...airiOld } = AIRI;
    serve({ needs_review: [MI], announced: [], confirmed: [airiOld], set_aside: [], total: 2 });
    const old = render(<SplitList />);
    const html = old.container.innerHTML;
    old.unmount();
    for (const none of [null, []]) {
      serve({ needs_review: [MI], announced: [], confirmed: [{ ...airiOld, called_off: none }], set_aside: [], total: 2 });
      const r = render(<SplitList />);
      expect(r.container.innerHTML).toBe(html);
      r.unmount();
    }
    expect(html).not.toContain("adm-split-called-off");
    expect(html).not.toContain("Called off");
    expect(html).not.toContain("may call");
    expect(html).toContain("adm-split-confirmed-row");
  });

  describe("the count at the top", () => {
    const count = () => screen.queryByTestId("adm-splits-called-off-count");

    it("counts the listed splits with a filing that may call them off, naming them: one", () => {
      open({ confirmed: [AIRI, { ...BTOC, called_off: null }], total: 2 });
      expect(count()).toHaveTextContent("1 listed split has a filing that may call it off: AIRI.");
      expect(count()).toHaveClass("adm-loud");
    });

    it("counts two across the lists, and counts splits, not filings", () => {
      const twice = { ...ATCH, called_off: [...ATCH.called_off, calledOff({ accession: "0000000000-26-000099" })] };
      open({ confirmed: [AIRI], announced: [{ ...twice, status: "announced" }], needs_review: [{ ...MI, called_off: [] }], total: 3 });
      expect(count()).toHaveTextContent("2 listed splits have a filing that may call them off: ATCH, AIRI.");
      expect(within(screen.getByTestId("adm-splits-announced")).getAllByTestId("adm-split-called-off")).toHaveLength(2);
    });

    it("does not count a rejected or revoked split: revoking clears it from the count, its line stays", () => {
      open({ confirmed: [], set_aside: [{ ...AIRI, status: "revoked", author: "operator" }], total: 1 });
      expect(count()).toBeNull();
      expect(screen.getByTestId("adm-splits-set-aside")).toHaveTextContent("1 rejected or revoked");
      expect(screen.getAllByTestId("adm-split-called-off")).toHaveLength(1);
    });

    it("sits at the top of the panel, above every list, and names at most six", () => {
      const many = Array.from({ length: 8 }, (_, i) => ({
        ...AIRI,
        version_id: `a1210000-0000-4000-8000-00000000010${i}`,
        security_id: `a1210000-0000-4000-8000-00000000020${i}`,
        ticker: `TK${i}`,
      }));
      open({ confirmed: many, needs_review: [MI], total: 9 });
      expect(count()).toHaveTextContent(
        "8 listed splits have a filing that may call them off: TK0, TK1, TK2, TK3, TK4, TK5 and 2 more.",
      );
      const panel = screen.getByTestId("adm-splits");
      for (const below of ["adm-splits-review", "adm-splits-confirmed"]) {
        expect(count()!.compareDocumentPosition(screen.getByTestId(below)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      }
      expect(panel.firstElementChild).toHaveTextContent("Splits");
      expect(panel.children[1]).toBe(count());
    });

    it("falls back to the name, then the id, for a split with no ticker", () => {
      open({ confirmed: [{ ...AIRI, ticker: null }, { ...ATCH, ticker: null, name: null }], total: 2 });
      expect(count()).toHaveTextContent(`Air Industries Group, ${ATCH.security_id}.`);
    });
  });

  describe("the words", () => {
    const line = (c: Record<string, unknown>) => {
      open({ confirmed: [{ ...AIRI, called_off: [calledOff(c)] }] });
      return screen.getByTestId("adm-split-called-off");
    };

    it("says how far from the first session the filing came: before, the day, after, one day and many", () => {
      expect(line({ days_from: -1 })).toHaveTextContent("(1 day before the first session;");
      cleanup();
      expect(line({ days_from: -12 })).toHaveTextContent("(12 days before the first session;");
      cleanup();
      expect(line({ days_from: 0 })).toHaveTextContent("(the day of the first session;");
      cleanup();
      expect(line({ days_from: 1 })).toHaveTextContent("(1 day after the first session;");
      cleanup();
      expect(line({ days_from: 30 })).toHaveTextContent("(30 days after the first session;");
    });

    it("says which ratio the filing states: this split's, another one, or none", () => {
      expect(line({ same_ratio: true })).toHaveTextContent("; states this split's ratio)");
      cleanup();
      expect(line({ same_ratio: false, ratio_label: "1-for-3 reverse split" })).toHaveTextContent(
        "; states a different ratio, 1-for-3 reverse split)",
      );
      cleanup();
      expect(line({ same_ratio: null, ratio_label: null })).toHaveTextContent("; states no ratio)");
      cleanup();
      // a different ratio with no label to name (not a shape the server sends): still said, never blank
      expect(line({ same_ratio: false, ratio_label: null })).toHaveTextContent("; states a different ratio)");
    });

    it("words ATCH's failed vote with no ratio on the day of its first session", () => {
      open({ confirmed: [ATCH] });
      expect(screen.getByTestId("adm-split-called-off")).toHaveTextContent(
        "Called off? 6-K filed 2026-11-12 (the day of the first session; states no ratio): \u201cThe Reverse Stock Split Proposal was not approved.\u201d",
      );
    });

    it("says what the filing says when it carries no passage, by its kind, and nothing more for a kind it does not know", () => {
      expect(line({ passage: null })).toHaveTextContent(
        "states this split's ratio): the filing says the split will not go ahead.",
      );
      cleanup();
      expect(line({ passage: null, kind: "failed_vote" })).toHaveTextContent(
        "states this split's ratio): the filing says the vote on the split failed.",
      );
      cleanup();
      const bare = line({ passage: null, kind: "something_new" });
      expect(bare).toHaveTextContent("Called off? 8-K filed 2026-11-19 (1 day before the first session; states this split's ratio)");
      expect(bare.textContent).not.toContain(":");
    });

    it("leaves the filing as plain text when it has no safe link, and says the form when it is the only thing known", () => {
      expect(within(line({ url: null })).queryByRole("link")).toBeNull();
      cleanup();
      const plain = line({ url: "javascript:alert(1)" });
      expect(within(plain).queryByRole("link")).toBeNull();
      expect(plain).toHaveTextContent("Called off? 8-K filed 2026-11-19 (1 day before");
    });
  });

  describe("a long passage", () => {
    const quoted = (passage: string) => {
      open({ confirmed: [{ ...AIRI, called_off: [calledOff({ passage })] }] });
      const text = screen.getByTestId("adm-split-called-off").textContent ?? "";
      return text.slice(text.indexOf("\u201c") + 1, text.lastIndexOf("\u201d"));
    };

    it("is shown whole when it is short", () => {
      expect(quoted("It would not effect the split.")).toBe("It would not effect the split.");
      cleanup();
      expect(quoted("w".repeat(280))).toBe("w".repeat(280));
    });

    it("is cut where a clause ends, with an ellipsis, so the sentence shows the call-off and the ratio", () => {
      expect(quoted(AIRI_PASSAGE)).toBe(AIRI_CUT);
    });

    it("is cut at a whole word when no clause ends past the halfway, and never ends on a dangling mark", () => {
      const words = Array.from({ length: 80 }, (_, i) => `word${i}`).join(" ");
      const got = quoted(words);
      expect(got.endsWith("\u2026")).toBe(true);
      const body = got.slice(0, -1);
      expect(body.length).toBeLessThanOrEqual(280);
      expect(words.startsWith(body)).toBe(true);
      expect(words[body.length]).toBe(" ");
      cleanup();
      // the word cut lands after a dash: the dash is dropped, the ellipsis follows the last word
      const dashes = Array.from({ length: 60 }, () => "alpha \u2013").join(" ");
      expect(quoted(dashes)).toMatch(/alpha\u2026$/);
    });

    it("is cut hard at the limit when it has no space to cut at, and its line breaks and runs of spaces read as one space", () => {
      expect(quoted("x".repeat(400))).toBe(`${"x".repeat(280)}\u2026`);
      cleanup();
      expect(quoted("It would not\n  effect\tthe split.")).toBe("It would not effect the split.");
    });
  });
});

// F1-5 — the "Split filings not placed" section: SEC filings about a split the list could not place, as GET
// /admin/splits carries them (unplaced, unplaced_dismissed, unplaced_runs, unplaced_error; backend F1-3). The
// tickers and accessions of PBK, WLDS and META are the ones the framework-completion plan names from the
// 2026-11-25 nightly artifact; every reason and passage here is a SYNTHETIC sentence in the shape of its class
// (ratio but no date, an authorized range only, no direction, a past date only), never a quote of a filing. TSTX
// and the other accessions are invented.
const NEAR_PBK = {
  version_id: "9b1d0000-0000-4000-8000-0000000000a1",
  key_date: "2026-11-30",
  effective_date: "2026-11-30",
  label: "1-for-20 reverse split",
  status: "needs_review",
  same_ratio: true,
  cites_filing: false,
  days_from: 13,
};
const UNPLACED_BASE = {
  name: null,
  url: "https://www.sec.gov/Archives/edgar/data/1/000000000026000000/0000000000-26-000000-index.htm",
  document: null,
  passage: null,
  ratio_from: null,
  ratio_to: null,
  ratio_label: null,
  source: "nightly",
  seen_on: "2026-11-25",
  listed_near: null,
  dismissal_id: null,
  dismissed_at: null,
  dismissed_reason: null,
};
// a ratio stated, no first session; a listed split of that ratio sits 13 days after the filing
const PBK_UNPLACED = {
  ...UNPLACED_BASE,
  security_id: "9b1d0000-0000-4000-8000-0000000000b1",
  ticker: "PBK",
  name: "PBK Holdings Ltd",
  accession: "0001493152-26-052687",
  form: "6-K",
  filed: "2026-11-17",
  url: "https://www.sec.gov/Archives/edgar/data/1/000149315226052687/0001493152-26-052687-index.htm",
  reason: "1-for-20 reverse split stated, but no first session stated",
  document: "6-K ea0289999-6k_pbk.htm",
  passage: "The Board approved a one-for-twenty (1-for-20) reverse share split of the Company's ordinary shares.",
  ratio_from: "20",
  ratio_to: "1",
  ratio_label: "1-for-20 reverse split",
  listed_near: NEAR_PBK,
};
// an authorized range only: no ratio set, nothing on the list near it, no link; found by the long-window check
const WLDS_UNPLACED = {
  ...UNPLACED_BASE,
  security_id: "9b1d0000-0000-4000-8000-0000000000b2",
  ticker: "WLDS",
  accession: "0001213900-26-120001",
  form: "8-K",
  filed: "2026-11-21",
  url: null,
  reason: "only an authorized range of ratios (one-for-ten, 1:10, one-for-twenty, 1:20) — no ratio set",
  document: "8-K ea0290001-8k_wlds.htm",
  passage: "authorize the Board to effect a reverse split at a ratio of not less than one-for-ten and not more than one-for-twenty",
  source: "check",
  seen_on: "2026-11-27",
};
// no direction stated, nothing near it
const META_UNPLACED = {
  ...UNPLACED_BASE,
  security_id: "9b1d0000-0000-4000-8000-0000000000b3",
  ticker: "META",
  accession: "0001628280-26-066700",
  form: "8-K",
  filed: "2026-11-12",
  url: "https://www.sec.gov/Archives/edgar/data/1/000162828026066700/0001628280-26-066700-index.htm",
  reason: "no direction stated",
  document: "8-K meta-20261112.htm",
  passage: "The Company announced a change to its share capital, effective in the coming weeks.",
};
// a past date only; a listed split cites this very filing
const AVX_UNPLACED = {
  ...UNPLACED_BASE,
  security_id: "9b1d0000-0000-4000-8000-0000000000b4",
  ticker: "AVX",
  accession: "0001213900-26-120002",
  form: "8-K",
  filed: "2026-11-24",
  reason: "only a past date stated (2026-10-08)",
  passage: "The reverse share split became effective on October 8, 2026.",
  listed_near: {
    version_id: "9b1d0000-0000-4000-8000-0000000000a2",
    key_date: "2026-10-08",
    effective_date: "2026-10-08",
    label: "1-for-10 reverse split",
    status: "confirmed",
    same_ratio: null,
    cites_filing: true,
    days_from: -47,
  },
};
// a listed split of ANOTHER ratio 30 days before the filing
const TSTX_UNPLACED = {
  ...UNPLACED_BASE,
  security_id: "9b1d0000-0000-4000-8000-0000000000b5",
  ticker: "TSTX",
  accession: "0001213900-26-120003",
  form: "6-K",
  filed: "2026-11-22",
  reason: "several ratios stated (1-for-2, 1-for-5)",
  passage: "at a ratio of one-for-two or one-for-five, to be set by the Board",
  listed_near: {
    version_id: "9b1d0000-0000-4000-8000-0000000000a3",
    key_date: "2026-10-23",
    effective_date: "2026-10-23",
    label: "1-for-10 reverse split",
    status: "confirmed",
    same_ratio: null,
    cites_filing: false,
    days_from: -30,
  },
};
const DISMISSAL_ID = "9b1d0000-0000-4000-8000-0000000000d1";
const META_DISMISSED = {
  ...META_UNPLACED,
  dismissal_id: DISMISSAL_ID,
  dismissed_at: "2026-11-27T15:02:00Z",
  dismissed_reason: "a share-capital notice, not a split",
};
const RUNS = [
  { source: "nightly", asof: "2026-11-25", started_at: "2026-11-25T22:30:00Z", sec_days: null, rows: 3 },
  { source: "check", asof: "2026-11-27", started_at: "2026-11-27T13:00:00Z", sec_days: 800, rows: 2 },
];

describe("SplitList: split filings not placed (F1-5)", () => {
  const onlyUnplaced = (extra: Record<string, unknown>) =>
    serve({ needs_review: [], announced: [], confirmed: [], set_aside: [], total: 0, ...extra });
  const section = () => screen.getByTestId("adm-splits-unplaced");
  const rows = () => within(section()).getAllByTestId("adm-split-unplaced-row");
  const countOf = (text: string, part: string) => text.split(part).length - 1;

  it("lists a filing once: ticker, form and filed date as the link, the reason, the source and the passage", () => {
    onlyUnplaced({ unplaced: [PBK_UNPLACED], unplaced_runs: RUNS });
    render(<SplitList />);
    const [row] = rows();
    expect(row).toHaveTextContent("PBK (PBK Holdings Ltd)");
    const link = within(row).getByRole("link", { name: "6-K filed 2026-11-17" });
    expect(link).toHaveAttribute("href", PBK_UNPLACED.url);
    expect(within(row).getByTestId("adm-split-unplaced-why")).toHaveTextContent(
      "1-for-20 reverse split stated, but no first session stated",
    );
    expect(within(row).getByTestId("adm-split-unplaced-source")).toHaveTextContent("nightly 11-25");
    const passage = within(row).getByTestId("adm-split-unplaced-passage");
    expect(passage).toHaveTextContent("a one-for-twenty (1-for-20) reverse share split");
    expect(passage).toHaveTextContent("6-K ea0289999-6k_pbk.htm");
    // each thing once: the ratio the reason names is not said again by the hint or a separate field
    expect(countOf(row.textContent ?? "", "1-for-20 reverse split")).toBe(1);
    expect(countOf(row.textContent ?? "", "0001493152-26-052687")).toBe(0); // the link carries it, in its href
  });

  it("names the ratio a reason does not, once", () => {
    const quiet = { ...PBK_UNPLACED, reason: "no first session stated", listed_near: null };
    onlyUnplaced({ unplaced: [quiet] });
    render(<SplitList />);
    const [row] = rows();
    expect(within(row).getByTestId("adm-split-unplaced-why")).toHaveTextContent(
      "No first session stated — the filing states 1-for-20 reverse split",
    );
    expect(countOf(row.textContent ?? "", "1-for-20 reverse split")).toBe(1);
  });

  it("says no ratio for a filing with none, and shows the form as plain text when it has no link", () => {
    onlyUnplaced({ unplaced: [WLDS_UNPLACED] });
    render(<SplitList />);
    const [row] = rows();
    expect(within(row).queryByRole("link")).toBeNull();
    expect(row).toHaveTextContent("WLDS — 8-K filed 2026-11-21");
    expect(within(row).getByTestId("adm-split-unplaced-why")).toHaveTextContent(
      "Only an authorized range of ratios (one-for-ten, 1:10, one-for-twenty, 1:20) — no ratio set",
    );
    expect(within(row).getByTestId("adm-split-unplaced-source")).toHaveTextContent("long-window check 11-27");
    expect(row).not.toHaveTextContent("states");
  });

  it("is quiet: closed, never loud, and fires nothing on mount", () => {
    onlyUnplaced({ unplaced: [PBK_UNPLACED, META_UNPLACED] });
    render(<SplitList />);
    const box = section();
    expect(box.tagName).toBe("DETAILS");
    expect(box).not.toHaveAttribute("open");
    expect(box.querySelector(".adm-loud")).toBeNull();
    expect(box.querySelector("summary")).toHaveTextContent("Split filings not placed (2)");
    for (const mm of [h.dismissUnplaced, h.undoUnplaced, h.dismiss, h.confirm]) expect(fn(mm)).not.toHaveBeenCalled();
  });

  it("counts its own rows in the heading and lists the ones no listed split covers first", () => {
    // the server's order is newest filing first: AVX (cited) 11-24, TSTX 11-22, WLDS 11-21, PBK (same ratio) 11-17, META 11-12
    onlyUnplaced({ unplaced: [AVX_UNPLACED, TSTX_UNPLACED, WLDS_UNPLACED, PBK_UNPLACED, META_UNPLACED] });
    render(<SplitList />);
    expect(section().querySelector("summary")).toHaveTextContent("Split filings not placed (5) · 2 covered by a listed split");
    const order = rows().map((r) => within(r).getByText(/^(AVX|TSTX|WLDS|PBK|META)$/).textContent);
    expect(order).toEqual(["TSTX", "WLDS", "META", "AVX", "PBK"]); // uncovered first; the server's order inside each group
  });

  it("leaves the covered note out when no listed split covers any filing", () => {
    onlyUnplaced({ unplaced: [WLDS_UNPLACED, TSTX_UNPLACED] });
    render(<SplitList />);
    expect(section().querySelector("summary")).toHaveTextContent("Split filings not placed (2)");
    expect(section().querySelector("summary")).not.toHaveTextContent("covered");
  });

  it.each([
    [AVX_UNPLACED, "A listed split cites this filing: 1-for-10 reverse split, confirmed, first session 10-08."],
    [PBK_UNPLACED, "The list holds this ratio: needs review, first session 11-30, 13 days after this filing."],
    [TSTX_UNPLACED, "Nearest listed split: 1-for-10 reverse split, confirmed, first session 10-23, 30 days before this filing."],
  ])("words the listed-split hint for %#", (u, line) => {
    onlyUnplaced({ unplaced: [u] });
    render(<SplitList />);
    expect(within(rows()[0]).getByTestId("adm-split-unplaced-near").textContent).toBe(line);
  });

  it("words the distance for one day and for the day of the filing, and says nothing when no split is near", () => {
    const near = (days: number, n: number) => ({
      ...TSTX_UNPLACED,
      accession: `0001213900-26-12010${n}`,
      listed_near: { ...TSTX_UNPLACED.listed_near, days_from: days },
    });
    onlyUnplaced({ unplaced: [near(1, 1), near(-1, 2), near(0, 3), META_UNPLACED] });
    render(<SplitList />);
    // the covered-first order does not matter here: no row is covered, so the server's order stands
    const lines = rows().map((r) => within(r).queryByTestId("adm-split-unplaced-near")?.textContent ?? null);
    expect(lines[0]).toContain("1 day after this filing");
    expect(lines[1]).toContain("1 day before this filing");
    expect(lines[2]).toContain("the same day as this filing");
    expect(lines[3]).toBeNull(); // META: nothing near it
  });

  it("states the runs it read, and that Dismiss changes no split or call", () => {
    onlyUnplaced({ unplaced: [PBK_UNPLACED], unplaced_runs: RUNS });
    render(<SplitList />);
    expect(within(section()).getByTestId("adm-split-unplaced-runs")).toHaveTextContent(
      "Read from nightly 11-25: 3 filings; long-window check 11-27, 800 days: 2 filings.",
    );
    expect(within(section()).getByTestId("adm-split-unplaced-help")).toHaveTextContent(
      "Dismiss means this is not a split to list",
    );
    expect(within(section()).getByTestId("adm-split-unplaced-help")).toHaveTextContent("changes no split, hold or call");
  });

  it("Dismiss sends the typed reason, trimmed", async () => {
    onlyUnplaced({ unplaced: [PBK_UNPLACED, META_UNPLACED] });
    render(<SplitList />);
    // META is not covered by a listed split, so it comes first
    const pbk = rows()[1];
    await userEvent.type(within(pbk).getByLabelText("Reason for dismissing PBK (optional)"), "  covered by the 11-30 split ");
    await userEvent.click(within(pbk).getByRole("button", { name: "Dismiss" }));
    expect(fn(h.dismissUnplaced)).toHaveBeenCalledTimes(1);
    expect(fn(h.dismissUnplaced)).toHaveBeenCalledWith({
      securityId: PBK_UNPLACED.security_id,
      accession: PBK_UNPLACED.accession,
      reason: "covered by the 11-30 split",
    });
  });

  it("Dismiss without a reason sends none, and a typed reason stays on its own row", async () => {
    onlyUnplaced({ unplaced: [WLDS_UNPLACED, META_UNPLACED] });
    render(<SplitList />);
    const [wlds, meta] = rows();
    await userEvent.type(within(wlds).getByLabelText("Reason for dismissing WLDS (optional)"), "typed here");
    expect(within(meta).getByLabelText("Reason for dismissing META (optional)")).toHaveValue("");
    await userEvent.click(within(meta).getByRole("button", { name: "Dismiss" }));
    expect(fn(h.dismissUnplaced)).toHaveBeenCalledWith({ securityId: META_UNPLACED.security_id, accession: META_UNPLACED.accession });
    expect(fn(h.dismissUnplaced).mock.calls[0][0]).not.toHaveProperty("reason");
  });

  it("enables Dismiss and the reason box, and disables every decision while a write is sending", () => {
    onlyUnplaced({ unplaced: [PBK_UNPLACED], unplaced_dismissed: [META_DISMISSED] });
    const { unmount } = render(<SplitList />);
    expect(within(rows()[0]).getByRole("button", { name: "Dismiss" })).toBeEnabled();
    expect(within(rows()[0]).getByLabelText("Reason for dismissing PBK (optional)")).toBeEnabled();
    unmount();
    for (const key of ["dismissUnplaced", "undoUnplaced", "confirm"] as const) {
      h[key] = { ...m(), isPending: true };
      onlyUnplaced({ unplaced: [PBK_UNPLACED], unplaced_dismissed: [META_DISMISSED] });
      const r = render(<SplitList />);
      expect(within(rows()[0]).getByRole("button", { name: "Dismiss" })).toBeDisabled();
      r.unmount();
      h[key] = m();
    }
  });

  it("keeps a dismissed filing behind a 'dismissed' filter, grayed, with its reason and an Undo", async () => {
    onlyUnplaced({ unplaced: [PBK_UNPLACED], unplaced_dismissed: [META_DISMISSED] });
    render(<SplitList />);
    const filter = within(section()).getByTestId("adm-splits-unplaced-dismissed");
    expect(within(filter).queryByTestId("adm-split-unplaced-dismissed-row")).toBeNull(); // closed to start
    expect(rows()).toHaveLength(1); // the dismissed one is not on the list
    await userEvent.click(within(filter).getByRole("button", { name: "Show dismissed (1)" }));
    const row = within(filter).getByTestId("adm-split-unplaced-dismissed-row");
    expect(row).toHaveTextContent("META — 8-K filed 2026-11-12");
    expect(row).toHaveTextContent("dismissed by you, 11-27 — “a share-capital notice, not a split”");
    expect(within(row).getByTestId("adm-split-unplaced-why")).toHaveTextContent("No direction stated");
    expect(within(row).queryByRole("button", { name: "Dismiss" })).toBeNull();
    expect(row.querySelector(".adm-grayed")).not.toBeNull();
    // the Undo itself is not grayed: it must read as enabled
    const undo = within(row).getByRole("button", { name: "Undo" });
    expect(undo).toBeEnabled();
    expect(undo.closest(".adm-grayed")).toBeNull();
    await userEvent.click(undo);
    expect(fn(h.undoUnplaced)).toHaveBeenCalledWith({ dismissalId: DISMISSAL_ID });
    expect(fn(h.dismissUnplaced)).not.toHaveBeenCalled();
    await userEvent.click(within(filter).getByRole("button", { name: "Hide dismissed (1)" }));
    expect(within(filter).queryByTestId("adm-split-unplaced-dismissed-row")).toBeNull();
  });

  it("disables Undo while a write is sending", async () => {
    h.undoUnplaced = { ...m(), isPending: true };
    onlyUnplaced({ unplaced: [], unplaced_dismissed: [META_DISMISSED] });
    render(<SplitList />);
    await userEvent.click(within(section()).getByRole("button", { name: "Show dismissed (1)" }));
    expect(within(section()).getByRole("button", { name: "Undo" })).toBeDisabled();
  });

  it("shows the section when only dismissed filings remain, its heading counting none", () => {
    onlyUnplaced({ unplaced: [], unplaced_dismissed: [META_DISMISSED] });
    render(<SplitList />);
    expect(section().querySelector("summary")).toHaveTextContent("Split filings not placed (0)");
    expect(within(section()).getByRole("button", { name: "Show dismissed (1)" })).toBeInTheDocument();
  });

  it("shows the server's sentence for a refused Dismiss in that filing's row only, once", () => {
    const sentence = "filing 0001493152-26-052687 is already dismissed";
    h.dismissUnplaced = {
      ...m(),
      isError: true,
      error: { detail: sentence },
      variables: { securityId: PBK_UNPLACED.security_id, accession: PBK_UNPLACED.accession },
    };
    onlyUnplaced({ unplaced: [PBK_UNPLACED, META_UNPLACED] });
    render(<SplitList />);
    const [meta, pbk] = rows(); // META is not covered by a listed split, so it comes first
    expect(within(pbk).getByTestId("adm-split-unplaced-row-error")).toHaveTextContent(sentence);
    expect(within(meta).queryByTestId("adm-split-unplaced-row-error")).toBeNull();
    expect(screen.getAllByText(sentence)).toHaveLength(1); // not repeated at the foot of the panel
  });

  it("shows the server's sentence for a refused Undo in the dismissed row, once", async () => {
    const sentence = "filing 0001628280-26-066700 is no longer on the unplaced list";
    h.undoUnplaced = { ...m(), isError: true, error: { detail: sentence }, variables: { dismissalId: DISMISSAL_ID } };
    onlyUnplaced({ unplaced: [], unplaced_dismissed: [META_DISMISSED] });
    render(<SplitList />);
    await userEvent.click(within(section()).getByRole("button", { name: "Show dismissed (1)" }));
    expect(within(section()).getByTestId("adm-split-unplaced-row-error")).toHaveTextContent(sentence);
    expect(screen.getAllByText(sentence)).toHaveLength(1);
  });

  it("shows a failed unplaced read beside the rest of the panel instead of hiding either", () => {
    serve({
      needs_review: [MI],
      announced: [],
      confirmed: [BTOC],
      set_aside: [],
      total: 2,
      unplaced_error: "OSError: the nightly's run file is unreadable",
    });
    render(<SplitList />);
    expect(screen.getByTestId("adm-splits-unplaced-error")).toHaveTextContent(
      "The split filings not placed could not be fully read: OSError: the nightly's run file is unreadable",
    );
    expect(screen.queryByTestId("adm-splits-unplaced")).toBeNull(); // no rows to list
    expect(screen.getAllByTestId("adm-split-review-row")).toHaveLength(1);
    expect(screen.getByTestId("adm-splits-confirmed")).toBeInTheDocument();
  });

  it("renders the panel for an unplaced read that failed alone, and keeps the rows an error left readable", () => {
    onlyUnplaced({ unplaced_error: "ValueError: bad run file" });
    const { unmount } = render(<SplitList />);
    expect(screen.getByTestId("adm-splits-unplaced-error")).toHaveTextContent("ValueError: bad run file");
    unmount();
    onlyUnplaced({ unplaced: [PBK_UNPLACED], unplaced_error: "ValueError: bad run file" });
    render(<SplitList />);
    expect(screen.getByTestId("adm-splits-unplaced-error")).toBeInTheDocument();
    expect(rows()).toHaveLength(1);
  });

  it("renders the panel for unplaced filings alone, with no split on the list", () => {
    onlyUnplaced({ unplaced: [META_UNPLACED] });
    render(<SplitList />);
    expect(screen.getByTestId("adm-splits")).toBeInTheDocument();
    expect(rows()).toHaveLength(1);
  });

  describe("an older server's payload (the new keys absent)", () => {
    const OLD = {
      needs_review: [MI],
      announced: [],
      confirmed: [BTOC],
      set_aside: [LRHC_REJECTED],
      total: 3,
      unlisted_jumps: [GAME_JUMP],
    };

    it("renders exactly today's panel: the same markup as empty new keys, and none of the new section", () => {
      serve(OLD);
      const old = render(<SplitList />);
      const html = old.container.innerHTML;
      old.unmount();
      serve({ ...OLD, unplaced: [], unplaced_dismissed: [], unplaced_runs: [], unplaced_error: null });
      const empty = render(<SplitList />);
      expect(empty.container.innerHTML).toBe(html);
      expect(html).not.toContain("adm-splits-unplaced");
      expect(html).not.toContain("not placed");
      expect(html).toContain("adm-splits-review");
    });

    it("renders nothing for an older server's empty list", () => {
      serve({ needs_review: [], announced: [], confirmed: [], set_aside: [], total: 0 });
      const { container } = render(<SplitList />);
      expect(container).toBeEmptyDOMElement();
    });

    it("keeps today's decisions working (a Dismiss on a jump is still the jump's)", async () => {
      serve(OLD);
      render(<SplitList />);
      await userEvent.click(within(screen.getByTestId("adm-split-jump-row")).getByRole("button", { name: "Dismiss" }));
      expect(fn(h.dismiss)).toHaveBeenCalledWith({ securityId: GAME_JUMP.security_id, stepDate: "2026-08-24" });
      expect(fn(h.dismissUnplaced)).not.toHaveBeenCalled();
    });
  });
});

// F2-2 — "Add this split...": the steward lists a split NO source lists, from exactly one signal on the panel: a split
// filing not placed, or a split-shaped jump nobody dismissed (D-F2a). The form is the D3 form's sibling (ratio old -> new,
// first session, the cited https document, an optional note, the stored tape's step beside the entered factor, the
// override past the tolerance) and posts POST /admin/splits (backend F2-1). PBK, WLDS and GAME are the rows above (a
// ratio but no date; an authorized range only; GAME's real 08-24 step); every tape answer below is a SYNTHETIC reading
// in the shape of the server's AdminSplitTapeStepOut, never a stored bar.
describe("SplitList: Add this split... (F2-2)", () => {
  const ADD = "Add this split...";
  const SUBMIT = "Add these values";
  const PBK_TAPE = {
    first_session: "2026-11-30",
    prior_date: "2026-11-27",
    prior_close: 0.5,
    close: 9.8,
    prior_volume: 2000000,
    volume: 100000,
    step: 19.6,
    volume_ratio: 0.05,
    prior_served_before: false,
    price_factor: 20,
    tolerance: 0.5,
    step_low: 12.13,
    step_high: 32.97,
    within_tolerance: true,
    reason: null,
  };
  // GAME's 08-24 step is x7.5: a 1-for-4 entered against it sits outside the band
  const GAME_TAPE = {
    ...PBK_TAPE,
    first_session: "2026-08-24",
    prior_date: "2026-08-21",
    prior_close: 0.42,
    close: 3.16,
    step: 7.5,
    volume_ratio: 0.37,
    price_factor: 4,
    step_low: 2.43,
    step_high: 6.59,
    within_tolerance: false,
    reason: "the tape steps x7.5 on 2026-08-24, not within x1.65 of the entered x4 (1-for-4 reverse split)",
  };
  const NO_BAR = {
    first_session: "2026-11-30",
    step: null,
    price_factor: 20,
    tolerance: 0.5,
    step_low: 12.13,
    step_high: 32.97,
    within_tolerance: false,
    reason: "no stored bar on 2026-11-30 (with one in the 14 days before it) to read",
  };
  const GAME_DISMISSED = {
    ...GAME_JUMP,
    dismissal_id: "9b1d0000-0000-4000-8000-0000000000d2",
    dismissed_at: "2026-11-26T15:00:00Z",
    dismissed_reason: "a real move",
  };
  const PANEL = {
    needs_review: [],
    announced: [],
    confirmed: [],
    set_aside: [],
    total: 0,
    unlisted_jumps: [GAME_JUMP],
    unplaced: [PBK_UNPLACED, WLDS_UNPLACED],
  };
  const CITED = "https://example.com/pbk-notice";

  const filingRow = (ticker: string) =>
    screen.getAllByTestId("adm-split-unplaced-row").find((r) => within(r).queryByText(ticker) !== null)!;
  const jumpRow = () => screen.getByTestId("adm-split-jump-row");
  const openIn = async (row: HTMLElement) => userEvent.click(within(row).getByRole("button", { name: ADD }));
  const form = (row: HTMLElement) => within(within(row).getByTestId("adm-split-add-form"));
  const keys = (call: number) => Object.keys(fn(h.addSplit).mock.calls[call][0]).sort();

  beforeEach(() => serve(PANEL));

  it("is offered on every open filing and every undismissed jump, closed until asked: nothing is read or sent", async () => {
    serve({ ...PANEL, dismissed_jumps: [GAME_DISMISSED], unplaced_dismissed: [META_DISMISSED] });
    render(<SplitList />);
    expect(screen.getAllByRole("button", { name: ADD })).toHaveLength(3); // PBK, WLDS and the jump
    for (const row of [filingRow("PBK"), filingRow("WLDS"), jumpRow()]) {
      expect(within(row).getByRole("button", { name: ADD })).toBeEnabled();
      expect(within(row).queryByTestId("adm-split-add-form")).toBeNull();
    }
    // a dismissed jump and a dismissed filing are off the list: no Add on them (the server refuses it too)
    await userEvent.click(within(screen.getByTestId("adm-splits-jumps-dismissed")).getByRole("button", { name: /Show dismissed/ }));
    await userEvent.click(within(screen.getByTestId("adm-splits-unplaced-dismissed")).getByRole("button", { name: /Show dismissed/ }));
    expect(screen.getByTestId("adm-split-jump-dismissed-row")).toBeInTheDocument();
    expect(screen.getByTestId("adm-split-unplaced-dismissed-row")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: ADD })).toHaveLength(3);
    expect(h.tapeCall).not.toHaveBeenCalled();
    expect(fn(h.addSplit)).not.toHaveBeenCalled();
  });

  it("opens on a filing with its ratio filled in, the cited document empty, and the source filing linked as a reference", async () => {
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    const f = form(row);
    expect(f.getByLabelText("Old shares")).toHaveValue("20");
    expect(f.getByLabelText("New shares")).toHaveValue("1");
    // the cited document must state the values: the filing's address is NOT put in the box (it is usually unplaced
    // because it does not state the date)
    expect(f.getByLabelText("Cited document")).toHaveValue("");
    expect(f.getByLabelText("First session")).toHaveValue(""); // no date is known: the steward reads it off the document
    expect(f.getByLabelText("Note")).toHaveValue("");
    // the filing is shown beside the form as a reference, once, as a link
    const source = f.getByTestId("adm-split-add-source");
    expect(source).toHaveTextContent("Source filing: 6-K filed 2026-11-17");
    expect(within(source).getByRole("link", { name: "6-K filed 2026-11-17" })).toHaveAttribute("href", PBK_UNPLACED.url);
    // the tape is asked for the security (no version exists yet) and not before a first session is entered
    expect(h.tapeCall).toHaveBeenLastCalledWith({ securityId: PBK_UNPLACED.security_id }, null);
    expect(f.queryByTestId("adm-split-tape-step")).toBeNull();
    expect(f.getByRole("button", { name: SUBMIT })).toBeDisabled();
    expect(f.getByTestId("adm-split-add-hint")).toHaveTextContent("Enter the first session");
    expect(fn(h.addSplit)).not.toHaveBeenCalled();
  });

  it("says once what an add does: the tape is checked, the split is listed, prices before it are rescaled, Revoke undoes it", async () => {
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    const intro = within(row).getByTestId("adm-split-add-intro");
    expect(intro).toHaveTextContent("No source lists this split");
    expect(intro).toHaveTextContent("The ratio it states is filled in from the filing");
    expect(intro).not.toHaveTextContent("address"); // nothing is cited for the steward
    expect(intro).toHaveTextContent("stored tape is checked against the values you enter first");
    expect(intro).toHaveTextContent("prices before that session are put on the new scale");
    expect(intro).toHaveTextContent("Revoke undoes it");
    expect(screen.getAllByText(/stored tape is checked against the values you enter first/)).toHaveLength(1);
  });

  it("opens on a filing that states no ratio with nothing filled in, says so, and shows its source as plain text when it has no link", async () => {
    render(<SplitList />);
    const row = filingRow("WLDS");
    await openIn(row);
    const f = form(row);
    for (const label of ["Old shares", "New shares", "First session", "Cited document", "Note"]) {
      expect(f.getByLabelText(label)).toHaveValue("");
    }
    expect(within(row).getByTestId("adm-split-add-intro")).toHaveTextContent("states no single ratio");
    const source = f.getByTestId("adm-split-add-source");
    expect(source).toHaveTextContent("Source filing: 8-K filed 2026-11-21");
    expect(within(source).queryByRole("link")).toBeNull();
    expect(f.getByRole("button", { name: SUBMIT })).toBeDisabled();
  });

  it("shows no source filing on a jump: it has none to name", async () => {
    render(<SplitList />);
    await openIn(jumpRow());
    expect(within(jumpRow()).queryByTestId("adm-split-add-source")).toBeNull();
  });

  it("opens on a jump with its step date filled in and no ratio: the nearest whole ratio is named, never entered", async () => {
    render(<SplitList />);
    await openIn(jumpRow());
    const f = form(jumpRow());
    expect(f.getByLabelText("First session")).toHaveValue("2026-08-24");
    for (const label of ["Old shares", "New shares", "Cited document", "Note"]) {
      expect(f.getByLabelText(label)).toHaveValue("");
    }
    // the date is known, so the stored tape is read at once (the step is the jump's own)
    expect(h.tapeCall).toHaveBeenLastCalledWith({ securityId: GAME_JUMP.security_id }, { firstSession: "2026-08-24" });
    expect(within(jumpRow()).getByTestId("adm-split-add-intro")).toHaveTextContent("No ratio is filled in");
    const scale = f.getByTestId("adm-split-add-scale");
    expect(scale).toHaveTextContent("Nearest whole-number ratio to that step: 1-for-8 reverse split (x8).");
    expect(f.getByRole("button", { name: SUBMIT })).toBeDisabled();
    // once a ratio is typed the tape's own factor line takes over: said once
    await userEvent.type(f.getByLabelText("Old shares"), "8");
    expect(f.queryByTestId("adm-split-add-scale")).toBeNull();
  });

  it("words the nearest whole ratio of a drop as a forward split, and says nothing for a step near x1", async () => {
    const down = { ...GAME_JUMP, step: 0.5 };
    const flat = { ...GAME_JUMP, security_id: "9b1d0000-0000-4000-8000-0000000000c9", ticker: "FLAT", step: 1.2 };
    serve({ ...PANEL, unlisted_jumps: [down, flat], unplaced: [] });
    render(<SplitList />);
    const [first, second] = screen.getAllByTestId("adm-split-jump-row");
    await openIn(first);
    expect(form(first).getByTestId("adm-split-add-scale")).toHaveTextContent(
      "Nearest whole-number ratio to that step: 2-for-1 forward split (x0.5).",
    );
    await openIn(second);
    expect(form(second).queryByTestId("adm-split-add-scale")).toBeNull();
  });

  it("reads the stored tape for the security once a first session is entered, and shows its step beside the factor", async () => {
    tapeAnswers(PBK_TAPE);
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    await userEvent.type(form(row).getByLabelText("First session"), "2026-11-30");
    // the ratio the filing stated joins the read at once
    expect(h.tapeCall).toHaveBeenLastCalledWith(
      { securityId: PBK_UNPLACED.security_id },
      { firstSession: "2026-11-30", ratioFrom: "20", ratioTo: "1" },
    );
    const step = form(row).getByTestId("adm-split-tape-step");
    expect(step).toHaveTextContent("The stored tape stepped x19.6 on 11-30: close 0.5 on 11-27 → 9.8, volume x0.05.");
    expect(step).toHaveTextContent("Your values: every 20 shares became 1, a price factor of x20; the tape accepts x12.1 to x33.");
    expect(step).toHaveTextContent("The tape supports your values.");
    expect(form(row).queryByRole("checkbox")).toBeNull(); // inside the tolerance: no override to offer
    // nothing is cited for the steward: Add waits for the document the steward cites
    expect(form(row).getByRole("button", { name: SUBMIT })).toBeDisabled();
    expect(form(row).getByTestId("adm-split-add-hint")).toHaveTextContent("Cite the document");
    await userEvent.type(form(row).getByLabelText("Cited document"), CITED);
    expect(form(row).getByRole("button", { name: SUBMIT })).toBeEnabled();
    expect(form(row).queryByTestId("adm-split-add-hint")).toBeNull();
  });

  it("says it is reading the tape and holds Add until it is read", async () => {
    h.tape = { data: undefined, isFetching: true, isError: false, error: null };
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    await userEvent.type(form(row).getByLabelText("Cited document"), CITED); // so only the reading holds Add
    await userEvent.type(form(row).getByLabelText("First session"), "2026-11-30");
    expect(form(row).getByTestId("adm-split-tape-step")).toHaveTextContent("Reading the stored tape…");
    expect(form(row).getByRole("button", { name: SUBMIT })).toBeDisabled();
  });

  it("says so when the tape cannot be read, and leaves the decision to the server's own check", async () => {
    h.tape = { data: undefined, isFetching: false, isError: true, error: { detail: "no security 9b1d" } };
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    await userEvent.type(form(row).getByLabelText("First session"), "2026-11-30");
    expect(form(row).getByTestId("adm-split-tape-step")).toHaveTextContent("The stored tape could not be read: no security 9b1d");
    expect(form(row).queryByRole("checkbox")).toBeNull();
  });

  it("posts the pinned body from a filing: its accession as the one provenance, no override, no note unless typed", async () => {
    tapeAnswers(PBK_TAPE);
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    await userEvent.type(form(row).getByLabelText("First session"), "2026-11-30");
    // the cited document is the steward's to enter: the filing's address is not in the box
    await userEvent.type(form(row).getByLabelText("Cited document"), CITED);
    await userEvent.click(form(row).getByRole("button", { name: SUBMIT }));
    expect(fn(h.addSplit)).toHaveBeenCalledTimes(1);
    expect(fn(h.addSplit)).toHaveBeenCalledWith({
      security_id: PBK_UNPLACED.security_id,
      ratio_from: "20",
      ratio_to: "1",
      first_session: "2026-11-30",
      cited_url: CITED,
      from_accession: PBK_UNPLACED.accession,
    });
    // toHaveBeenCalledWith ignores keys holding undefined: the keys themselves are pinned too
    expect(keys(0)).toEqual(["cited_url", "first_session", "from_accession", "ratio_from", "ratio_to", "security_id"]);
    expect(fn(h.addSplit).mock.calls[0][0].cited_url).not.toBe(PBK_UNPLACED.url);
    expect(fn(h.dismissUnplaced)).not.toHaveBeenCalled();
    expect(fn(h.confirmValues)).not.toHaveBeenCalled();
  });

  it("sends what was changed: the steward's ratio, document and trimmed note replace the filled-in ratio", async () => {
    tapeAnswers({ ...PBK_TAPE, price_factor: 10, step_low: 6.07, step_high: 16.49, step: 9.9 });
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    const f = form(row);
    await userEvent.clear(f.getByLabelText("Old shares"));
    await userEvent.type(f.getByLabelText("Old shares"), " 10 ");
    await userEvent.type(f.getByLabelText("Cited document"), ` ${CITED} `);
    await userEvent.type(f.getByLabelText("First session"), "2026-11-30");
    await userEvent.type(f.getByLabelText("Note"), "  corrected from the exchange notice ");
    await userEvent.click(f.getByRole("button", { name: SUBMIT }));
    expect(fn(h.addSplit)).toHaveBeenCalledWith({
      security_id: PBK_UNPLACED.security_id,
      ratio_from: "10",
      ratio_to: "1",
      first_session: "2026-11-30",
      cited_url: CITED,
      note: "corrected from the exchange notice",
      from_accession: PBK_UNPLACED.accession,
    });
  });

  it("posts the pinned body from a jump: its step date as the one provenance, never an accession", async () => {
    tapeAnswers({ ...GAME_TAPE, price_factor: 8, step_low: 4.85, step_high: 13.19, within_tolerance: true, reason: null });
    render(<SplitList />);
    await openIn(jumpRow());
    const f = form(jumpRow());
    await userEvent.type(f.getByLabelText("Old shares"), "8");
    await userEvent.type(f.getByLabelText("New shares"), "1");
    await userEvent.type(f.getByLabelText("Cited document"), "https://example.com/game-8k");
    expect(h.tapeCall).toHaveBeenLastCalledWith(
      { securityId: GAME_JUMP.security_id },
      { firstSession: "2026-08-24", ratioFrom: "8", ratioTo: "1" },
    );
    await userEvent.click(f.getByRole("button", { name: SUBMIT }));
    expect(fn(h.addSplit)).toHaveBeenCalledTimes(1);
    expect(fn(h.addSplit)).toHaveBeenCalledWith({
      security_id: GAME_JUMP.security_id,
      ratio_from: "8",
      ratio_to: "1",
      first_session: "2026-08-24",
      cited_url: "https://example.com/game-8k",
      from_jump: "2026-08-24",
    });
    expect(keys(0)).toEqual(["cited_url", "first_session", "from_jump", "ratio_from", "ratio_to", "security_id"]);
    expect(fn(h.dismiss)).not.toHaveBeenCalled();
  });

  it("keeps Add off until the ratio, the session and an https document are all there", async () => {
    tapeAnswers(PBK_TAPE);
    render(<SplitList />);
    const row = filingRow("WLDS");
    await openIn(row);
    const f = form(row);
    const submit = f.getByRole("button", { name: SUBMIT });
    expect(f.getByTestId("adm-split-add-hint")).toHaveTextContent("old and the new share counts");
    await userEvent.type(f.getByLabelText("Old shares"), "20");
    await userEvent.type(f.getByLabelText("New shares"), "1");
    expect(f.getByTestId("adm-split-add-hint")).toHaveTextContent("Enter the first session");
    await userEvent.type(f.getByLabelText("First session"), "2026-11-30");
    expect(f.getByTestId("adm-split-add-hint")).toHaveTextContent("Cite the document");
    await userEvent.type(f.getByLabelText("Cited document"), "http://example.com/notice");
    expect(submit).toBeDisabled();
    expect(f.getByTestId("adm-split-add-hint")).toHaveTextContent("https://");
    await userEvent.clear(f.getByLabelText("Cited document"));
    await userEvent.type(f.getByLabelText("Cited document"), CITED);
    expect(submit).toBeEnabled();
    expect(f.queryByTestId("adm-split-add-hint")).toBeNull();
  });

  it("outside the tolerance: shows the stored step beside the factor and the override box; Add waits for it, and sends it only when ticked", async () => {
    tapeAnswers(GAME_TAPE);
    render(<SplitList />);
    await openIn(jumpRow());
    const f = form(jumpRow());
    await userEvent.type(f.getByLabelText("Old shares"), "4");
    await userEvent.type(f.getByLabelText("New shares"), "1");
    await userEvent.type(f.getByLabelText("Cited document"), CITED);
    const step = f.getByTestId("adm-split-tape-step");
    expect(step).toHaveTextContent("The stored tape stepped x7.5 on 08-24: close 0.42 on 08-21 → 3.16, volume x0.37.");
    expect(step).toHaveTextContent("a price factor of x4; the tape accepts x2.43 to x6.59.");
    expect(step).toHaveTextContent("The tape does not support your values.");
    const box = f.getByRole("checkbox", { name: /add anyway/i });
    expect(box).not.toBeChecked();
    expect(f.getByRole("button", { name: SUBMIT })).toBeDisabled();
    expect(f.getByTestId("adm-split-add-hint")).toHaveTextContent("Tick the box above to add past the tape.");
    expect(fn(h.addSplit)).not.toHaveBeenCalled();

    await userEvent.click(box);
    expect(f.getByRole("button", { name: SUBMIT })).toBeEnabled();
    await userEvent.click(f.getByRole("button", { name: SUBMIT }));
    expect(fn(h.addSplit)).toHaveBeenCalledWith({
      security_id: GAME_JUMP.security_id,
      ratio_from: "4",
      ratio_to: "1",
      first_session: "2026-08-24",
      cited_url: CITED,
      override_tape: true,
      from_jump: "2026-08-24",
    });
  });

  it("offers the override when the tape has no bar to read, and drops it, and its flag, when the values fit", async () => {
    tapeAnswers(NO_BAR);
    const { rerender } = render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    await userEvent.type(form(row).getByLabelText("First session"), "2026-11-30");
    expect(form(row).getByTestId("adm-split-tape-step")).toHaveTextContent("The stored tape has no bar to read on 2026-11-30");
    const box = form(row).getByRole("checkbox", { name: /add anyway/i });
    expect(box.closest("label")).toHaveTextContent("Add anyway: the split is then marked “past the tape”.");
    expect(box.closest("label")).not.toHaveTextContent("The tape has no bar"); // the tape line above says it, once
    await userEvent.click(box);
    // the values now fit: the box leaves, and a ticked box is NOT sent (the key is not there at all)
    tapeAnswers(PBK_TAPE);
    rerender(<SplitList />);
    expect(form(filingRow("PBK")).queryByRole("checkbox")).toBeNull();
    await userEvent.type(form(filingRow("PBK")).getByLabelText("Cited document"), CITED);
    await userEvent.click(form(filingRow("PBK")).getByRole("button", { name: SUBMIT }));
    expect(keys(0)).not.toContain("override_tape");
  });

  it("shows the server's 409 sentence in that filing's form, once, and keeps the form and what was typed", async () => {
    const sentence = "a live 1-for-20 reverse split is already listed within 45 days of 2026-11-30 — revoke it first";
    h.addSplit = {
      ...m(),
      isError: true,
      error: { detail: sentence },
      variables: { security_id: PBK_UNPLACED.security_id, from_accession: PBK_UNPLACED.accession },
    };
    render(<SplitList />);
    const row = filingRow("PBK");
    expect(within(row).queryByTestId("adm-split-add-error")).toBeNull(); // the form is closed
    await openIn(row);
    expect(form(row).getByTestId("adm-split-add-error")).toHaveTextContent(sentence);
    expect(screen.getAllByText(sentence)).toHaveLength(1); // not repeated at the foot of the panel
    expect(form(row).getByLabelText("Old shares")).toHaveValue("20");
    // the same refusal is not shown on the other filing or on the jump
    await openIn(filingRow("WLDS"));
    await openIn(jumpRow());
    expect(within(filingRow("WLDS")).queryByTestId("adm-split-add-error")).toBeNull();
    expect(within(jumpRow()).queryByTestId("adm-split-add-error")).toBeNull();
  });

  it("shows a 422's first message with the field it names, in the jump's form", async () => {
    h.addSplit = {
      ...m(),
      isError: true,
      error: { detail: [{ loc: ["body", "cited_url"], msg: "Value error, the cited document must be an https:// address with a host", type: "value_error" }] },
      variables: { security_id: GAME_JUMP.security_id, from_jump: GAME_JUMP.step_date },
    };
    render(<SplitList />);
    await openIn(jumpRow());
    expect(form(jumpRow()).getByTestId("adm-split-add-error")).toHaveTextContent(
      "cited_url: the cited document must be an https:// address with a host.",
    );
  });

  it("disables every decision while the split is being sent, and Cancel stays", async () => {
    h.addSplit = { ...m(), isPending: true };
    tapeAnswers(PBK_TAPE);
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    // everything the form needs is entered: only the write in flight holds Add
    await userEvent.type(form(row).getByLabelText("First session"), "2026-11-30");
    await userEvent.type(form(row).getByLabelText("Cited document"), CITED);
    expect(form(row).queryByTestId("adm-split-add-hint")).toBeNull();
    expect(form(row).getByRole("button", { name: SUBMIT })).toBeDisabled();
    expect(within(row).getByRole("button", { name: "Dismiss" })).toBeDisabled();
    expect(form(row).getByRole("button", { name: "Cancel" })).toBeEnabled();
  });

  it("disables Add while another decision is being sent", async () => {
    h.dismissUnplaced = { ...m(), isPending: true };
    tapeAnswers(PBK_TAPE);
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    await userEvent.type(form(row).getByLabelText("First session"), "2026-11-30");
    await userEvent.type(form(row).getByLabelText("Cited document"), CITED);
    expect(form(row).queryByTestId("adm-split-add-hint")).toBeNull();
    expect(form(row).getByRole("button", { name: SUBMIT })).toBeDisabled();
  });

  it("closes with Cancel and opens again filled the same way, nothing carried over", async () => {
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    await userEvent.type(form(row).getByLabelText("Note"), "typed");
    await userEvent.type(form(row).getByLabelText("Cited document"), CITED);
    await userEvent.clear(form(row).getByLabelText("Old shares"));
    await userEvent.click(form(row).getByRole("button", { name: "Cancel" }));
    expect(within(row).queryByTestId("adm-split-add-form")).toBeNull();
    await openIn(row);
    expect(form(row).getByLabelText("Old shares")).toHaveValue("20");
    expect(form(row).getByLabelText("Note")).toHaveValue("");
    expect(form(row).getByLabelText("Cited document")).toHaveValue("");
    expect(fn(h.addSplit)).not.toHaveBeenCalled();
  });

  it("keeps a filing's Dismiss, with its reason, working beside the form", async () => {
    render(<SplitList />);
    const row = filingRow("PBK");
    await openIn(row);
    await userEvent.type(within(row).getByLabelText("Reason for dismissing PBK (optional)"), "not one");
    await userEvent.click(within(row).getByRole("button", { name: "Dismiss" }));
    expect(fn(h.dismissUnplaced)).toHaveBeenCalledWith({
      securityId: PBK_UNPLACED.security_id,
      accession: PBK_UNPLACED.accession,
      reason: "not one",
    });
    expect(fn(h.addSplit)).not.toHaveBeenCalled();
  });

  describe("after an add", () => {
    const ADDED = {
      ...BTOC,
      ticker: "PBK",
      security_id: PBK_UNPLACED.security_id,
      value_source: "steward",
      action: "add",
      effective_date: "2026-11-20",
      status: "confirmed",
    };
    const line = () => screen.getByTestId("adm-split-added");

    it("says, for a confirmed add with a past first session, that the nightly does not rescale a stored tape, and what the operator runs", () => {
      h.addSplit = { ...m(), isSuccess: true, data: ADDED };
      render(<SplitList />);
      expect(line()).toHaveTextContent("PBK's split is added and confirmed (first session 11-20)");
      expect(line()).toHaveTextContent("The nightly rescales a stored tape only on a first night");
      expect(line()).toHaveTextContent(
        "the operator runs python -m pipeline.repair_split_basis --ticker PBK (dry run, then --apply)",
      );
      expect(screen.getAllByText(/repair_split_basis/)).toHaveLength(1);
    });

    it("says nothing for an announced add (its first session is ahead), and nothing before an add", () => {
      h.addSplit = { ...m(), isSuccess: true, data: { ...ADDED, status: "announced", effective_date: "2026-12-14" } };
      const { unmount } = render(<SplitList />);
      expect(screen.queryByTestId("adm-split-added")).toBeNull();
      unmount();
      h.addSplit = m();
      render(<SplitList />);
      expect(screen.queryByTestId("adm-split-added")).toBeNull();
    });

    it("closes the form that sent the add once it is listed, and no other, and opens again", async () => {
      const { rerender } = render(<SplitList />);
      await openIn(filingRow("PBK"));
      await openIn(filingRow("WLDS"));
      await openIn(jumpRow());
      h.addSplit = {
        ...m(),
        isSuccess: true,
        data: { ...ADDED, status: "announced", effective_date: "2026-12-14" },
        variables: {
          security_id: PBK_UNPLACED.security_id,
          ratio_from: "20",
          ratio_to: "1",
          first_session: "2026-12-14",
          cited_url: CITED,
          from_accession: PBK_UNPLACED.accession,
        },
      };
      rerender(<SplitList />);
      expect(within(filingRow("PBK")).queryByTestId("adm-split-add-form")).toBeNull(); // the one that sent it
      expect(within(filingRow("WLDS")).getByTestId("adm-split-add-form")).toBeInTheDocument();
      expect(within(jumpRow()).getByTestId("adm-split-add-form")).toBeInTheDocument();
      await openIn(filingRow("PBK")); // still a button, and the form opens again filled the same way
      expect(form(filingRow("PBK")).getByLabelText("Old shares")).toHaveValue("20");
    });

    it("falls back to the security id for a split with no ticker, and closes with its own button", async () => {
      h.addSplit = { ...m(), isSuccess: true, data: { ...ADDED, ticker: null } };
      render(<SplitList />);
      expect(line()).toHaveTextContent(`${PBK_UNPLACED.security_id}'s split is added and confirmed`);
      expect(line()).toHaveTextContent(`--ticker ${PBK_UNPLACED.security_id}`);
      await userEvent.click(within(line()).getByRole("button", { name: "Close" }));
      expect((h.addSplit as { reset: ReturnType<typeof vi.fn> }).reset).toHaveBeenCalledTimes(1);
    });
  });

  it("lists a steward's add past the tape as announced or confirmed, by its own status", () => {
    const STEWARD = {
      ...BTOC,
      version_id: "0b6f0c55-0000-4000-8000-0000000000e2",
      ticker: "PBK",
      status: "announced",
      effective_date: "2026-12-14",
      value_source: "steward",
      action: "add",
      decided_by: "added by you (cited values), 11-27",
      cited_url: CITED,
      tape_override: true,
      sec: null,
      polygon: null,
    };
    serve({ needs_review: [], announced: [STEWARD], confirmed: [{ ...STEWARD, status: "confirmed", effective_date: "2026-11-20" }], set_aside: [], total: 2 });
    render(<SplitList />);
    expect(within(screen.getByTestId("adm-splits-announced")).getByRole("link", { name: /cited document/ })).toHaveAttribute("href", CITED);
    expect(screen.getByTestId("adm-splits-announced")).toHaveTextContent("announced past the tape");
    expect(screen.getByTestId("adm-splits-announced")).not.toHaveTextContent("confirmed past the tape");
    expect(screen.getByTestId("adm-splits-confirmed")).toHaveTextContent("confirmed past the tape");
  });

  describe("an older server's payload (no unplaced keys)", () => {
    const OLD = {
      needs_review: [MI],
      announced: [],
      confirmed: [BTOC],
      set_aside: [LRHC_REJECTED],
      total: 3,
      unlisted_jumps: [GAME_JUMP],
    };

    it("renders today's panel: no section for filings, no Add on the review row, and the form on the jump alone", async () => {
      serve(OLD);
      render(<SplitList />);
      expect(screen.queryByTestId("adm-splits-unplaced")).toBeNull();
      expect(screen.queryByTestId("adm-split-unplaced-row")).toBeNull();
      expect(within(screen.getByTestId("adm-split-review-row")).queryByRole("button", { name: ADD })).toBeNull();
      expect(screen.getAllByRole("button", { name: ADD })).toHaveLength(1);
      await openIn(jumpRow());
      expect(form(jumpRow()).getByLabelText("First session")).toHaveValue("2026-08-24");
      expect(screen.queryByTestId("adm-split-added")).toBeNull();
    });

    it("renders no Add at all when it lists no jump either", () => {
      serve({ needs_review: [MI], announced: [], confirmed: [BTOC], set_aside: [], total: 2 });
      render(<SplitList />);
      expect(screen.queryByRole("button", { name: ADD })).toBeNull();
      expect(h.tapeCall).not.toHaveBeenCalled();
    });
  });
});
