import { render, screen, within } from "@testing-library/react";
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

const h = vi.hoisted(() => ({
  q: {} as Record<string, unknown>,
  confirm: {} as Record<string, unknown>,
  reject: {} as Record<string, unknown>,
  revoke: {} as Record<string, unknown>,
  restore: {} as Record<string, unknown>,
  dismiss: {} as Record<string, unknown>,
  undoDismiss: {} as Record<string, unknown>,
}));

vi.mock("../../api/hooks", () => ({
  useSplitList: () => h.q,
  useConfirmSplit: () => h.confirm,
  useRejectSplit: () => h.reject,
  useRevokeSplit: () => h.revoke,
  useRestoreSplit: () => h.restore,
  useDismissJump: () => h.dismiss,
  useUndoJumpDismissal: () => h.undoDismiss,
}));

const m = () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null });
const fn = (x: Record<string, unknown>) => (x as { mutate: ReturnType<typeof vi.fn> }).mutate;

function serve(data: Record<string, unknown>) {
  h.q = { data, isError: false, error: null };
}

beforeEach(() => {
  h.confirm = m();
  h.reject = m();
  h.revoke = m();
  h.restore = m();
  h.dismiss = m();
  h.undoDismiss = m();
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

  it("shows a failed jump check instead of hiding it", () => {
    serve({ needs_review: [], announced: [], confirmed: [], set_aside: [], total: 0, unlisted_jumps_error: "OperationalError: x" });
    render(<SplitList />);
    expect(screen.getByTestId("adm-splits-jumps-error")).toHaveTextContent("The split-shaped jump check failed: OperationalError: x");
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
