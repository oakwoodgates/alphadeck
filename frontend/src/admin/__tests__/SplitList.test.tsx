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
  confirmValues: {} as Record<string, unknown>,
  tape: {} as Record<string, unknown>,
  tapeCall: vi.fn(),
  reject: {} as Record<string, unknown>,
  revoke: {} as Record<string, unknown>,
  restore: {} as Record<string, unknown>,
  dismiss: {} as Record<string, unknown>,
  undoDismiss: {} as Record<string, unknown>,
}));

vi.mock("../../api/hooks", () => ({
  useSplitList: () => h.q,
  useConfirmSplit: () => h.confirm,
  useConfirmSplitValues: () => h.confirmValues,
  useSplitTapeStep: (versionId: string, args: unknown) => {
    h.tapeCall(versionId, args);
    return h.tape;
  },
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
      expect(h.tapeCall).toHaveBeenLastCalledWith(MI.version_id, null);
      expect(r.queryByTestId("adm-split-tape-step")).toBeNull();
      expect(r.getByRole("button", { name: SUBMIT })).toBeDisabled();

      await enter({ from: "10", to: "3", session: "2026-09-24" });
      expect(h.tapeCall).toHaveBeenLastCalledWith(MI.version_id, {
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
      expect(h.tapeCall).toHaveBeenLastCalledWith(MI.version_id, { firstSession: "2026-09-24" });
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
