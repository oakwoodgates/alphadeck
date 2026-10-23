import { describe, expect, it } from "vitest";

import type { EditionEpisodeDiffOut, ScoreboardEditionOut } from "../../api/hooks";
import {
  causeNote,
  causeText,
  deltaText,
  episodeLine,
  nightChange,
  noticePair,
  outsideLine,
  previousEdition,
  scoreText,
  sortCompanies,
  thesisLine,
} from "../whatChanged";

// Editions S7 — the cause of each change, in plain English, and the cut notice's pair.
describe("what changed — the causes (S7)", () => {
  const N = { before: "Edition 1", after: "Edition 2" };

  it("says each server cause plainly, and nothing when there is none", () => {
    expect(causeText("rule_change", N)).toBe("cause: the declared rule change");
    expect(causeText("unexplained", N)).toBe("cause: a change in the rules no declared rule explains");
    expect(causeText("late_data", N)).toBe("cause: data that arrived after Edition 1 was cut");
    expect(causeText("data_repair", N)).toMatch(/data repair or data that arrived late \(the two cannot be separated\)/);
    expect(causeText("not_separated", N)).toMatch(/^cause: not separated .*not re-tested\)$/);
    expect(causeText(null, N)).toBeNull();
    expect(causeText(undefined, N)).toBeNull();
    expect(causeText("anything else", N)).toBeNull(); // never guessed
  });

  it("the note says whether causes are shown, and why not", () => {
    expect(causeNote(true, N)).toBe("each change names its cause, from the trial that cut Edition 2");
    expect(causeNote(false, N)).toMatch(/Edition 2 was not cut from Edition 1/);
  });

  it("the notice compares the new edition with the one it replaced, when both are listed", () => {
    const eds = [0, 1, 2].map((n) => ({ number: n, label: `e${n}`, causes: [] }) as ScoreboardEditionOut);
    const notice = {
      number: 2,
      previous: 1,
      label: "x",
      line: "x",
      published_at: "2026-10-24T03:00:00Z",
      n_company_nights_changed: 3,
      unexplained: false,
    };
    expect(noticePair(eds, notice)?.map((e) => e.number)).toEqual([1, 2]);
    expect(noticePair(eds, null)).toBeNull();
    expect(noticePair(eds.slice(0, 2), notice)).toBeNull();
  });
});

// Editions S6 — the "what changed" copy, as pure functions: which edition the viewed one is compared with,
// and the plain-English lines for a night run and an episode change (the arrow reads earlier → later).

const ed = (number: number): ScoreboardEditionOut => ({
  number,
  label: number === 0 ? "the original record" : `re-run ${number}`,
  causes: [],
});
const NAMES = { before: "Edition 0", after: "Edition 1" };

const side = (over: Partial<NonNullable<EditionEpisodeDiffOut["before"]>> = {}) => ({
  arm_date: "2026-07-13",
  last_armed_date: "2026-07-20",
  dearm_date: "2026-07-21" as string | null,
  close_reason: "arm_until_lapsed",
  exit_by: "2026-11-06" as string | null,
  verdict: "core_entry" as string | null,
  entry_grade: "core" as string | null,
  conviction_grade: "core" as string | null,
  began_mid_arm: false,
  forward_return: 0.05 as number | null,
  ...over,
});

describe("what changed — which edition, and the words", () => {
  it("compares the viewed edition with the newest one before it; never on the record or a lone edition", () => {
    expect(previousEdition([ed(0)], 0)).toBeNull();
    expect(previousEdition([ed(0), ed(1)], 0)).toBeNull();
    expect(previousEdition([ed(0), ed(1)], 1)?.number).toBe(0);
    expect(previousEdition([ed(0), ed(1), ed(2)], 2)?.number).toBe(1);
    expect(previousEdition([ed(0), ed(1), ed(2)], 1)?.number).toBe(0);
  });

  it("a night run reads 'armed in X only', or each field earlier → later", () => {
    const off = { page: "2026-07-13", armed: false };
    const on = { page: "2026-07-13", armed: true, verdict: "core_entry", entry_grade: "core", exit_by: "2026-11-06" };
    expect(nightChange(["armed"], off, on, NAMES)).toBe("armed in Edition 1 only");
    expect(nightChange(["armed"], on, off, NAMES)).toBe("armed in Edition 0 only");
    const flip = { ...on, entry_grade: "flip", exit_by: "2026-11-09" };
    expect(nightChange(["entry_grade", "exit_by"], on, flip, NAMES)).toBe(
      "entry grade core → flip · exit-by Nov 6 → Nov 9",
    );
  });

  it("an episode change, in a sentence, with each side's score and the difference", () => {
    const moved: EditionEpisodeDiffOut = {
      change: "moved",
      before: side({ arm_date: "2026-07-16", forward_return: -0.01 }),
      after: side({ arm_date: "2026-07-13", forward_return: 0.03 }),
      arm_shift_days: -3,
      differs: [],
      return_delta: 0.04,
    };
    expect(episodeLine(moved, NAMES)).toBe(
      "armed Jul 16 in Edition 0, Jul 13 in Edition 1 (3 days earlier) · score -1.0% → +3.0% (+4.0 pts)",
    );
    const midArm = { ...moved, before: side({ arm_date: "2026-07-16", began_mid_arm: true }) };
    expect(episodeLine(midArm, NAMES)).toMatch(/Edition 0 began mid-arm on Jul 16, its first night/);
    const vanished: EditionEpisodeDiffOut = { change: "vanished", before: side({ dearm_date: null }) };
    expect(episodeLine(vanished, NAMES)).toBe(
      "Edition 0 armed it Jul 13 (still armed); Edition 1 does not · score +5.0%",
    );
    const matched: EditionEpisodeDiffOut = {
      change: "matched",
      before: side(),
      after: side({ dearm_date: "2026-07-28", exit_by: "2026-11-09", forward_return: 0.06 }),
      arm_shift_days: 0,
      differs: ["dearm_date", "exit_by", "forward_return"],
      return_delta: 0.01,
    };
    expect(episodeLine(matched, NAMES)).toBe(
      "same arm Jul 13 · de-armed Jul 21 → Jul 28 · exit-by Nov 6 → Nov 9 · score +5.0% → +6.0% (+1.0 pts)",
    );
    // a horizon change that both scores still run past reads as an unchanged score, not "0.0 pts"
    const sameScore = { ...matched, after: side({ exit_by: "2026-11-09" }), differs: ["exit_by"], return_delta: 0 };
    expect(episodeLine(sameScore, NAMES)).toBe("same arm Jul 13 · exit-by Nov 6 → Nov 9 · score unchanged at +5.0%");
    // an unpaired run that armed before the compared nights says so, with the first compared night
    const early: EditionEpisodeDiffOut = { change: "appeared", after: side({ arm_date: "2026-07-09" }), armed_before_window: true };
    expect(episodeLine(early, NAMES, "2026-07-13")).toBe(
      "Edition 1 arms it Jul 9 (de-armed Jul 21); Edition 0 did not · armed before the nights both editions " +
        "cover (from Jul 13) · score +5.0%",
    );
  });

  it("scores read plainly and an unscored side says so", () => {
    expect(scoreText(null)).toBe("not scored yet");
    expect(scoreText(-0.123)).toBe("-12.3%");
    expect(deltaText(null)).toBeNull();
    expect(deltaText(-0.004)).toBe("-0.4 pts");
  });

  it("the thesis line and the nights only one edition covers", () => {
    const t = {
      thesis_id: "t",
      name: "T",
      first_night: "2026-07-10",
      last_night: "2026-10-21",
      n_nights: 70,
      n_state_nights: 1,
      before_only: [{ first: "2026-10-20", last: "2026-10-21", n_nights: 2 }],
      after_only: [{ first: "2026-06-10", last: "2026-07-09", n_nights: 20 }],
      n_outside: 0,
      n_matched: 2,
      n_companies_unchanged: 3,
      companies: [],
    };
    expect(thesisLine(t)).toBe(
      "compared Jul 10 – Oct 21 · 70 nights · thesis state differs on 1 night · 2 episodes on the same arm " +
        "night · 3 other armed companies unchanged",
    );
    expect(outsideLine(t, NAMES)).toBe(
      "Edition 0 alone covers Oct 20 – Oct 21 · 2 nights · Edition 1 alone covers Jun 10 – Jul 9 · 20 nights",
    );
    expect(thesisLine({ ...t, first_night: null, last_night: null })).toBe("no night both editions cover");
    expect(outsideLine({ ...t, before_only: [], after_only: [] }, NAMES)).toBeNull();
  });

  it("companies sort by ticker, never by how much they moved", () => {
    const c = (ticker: string | null, name: string | null = null) => ({ security_id: `s-${ticker}`, ticker, company_name: name });
    expect(sortCompanies([c("ZZ"), c("aa"), c(null, "Mid Co")]).map((x) => x.ticker ?? x.company_name)).toEqual([
      "aa",
      "Mid Co",
      "ZZ",
    ]);
  });
});
