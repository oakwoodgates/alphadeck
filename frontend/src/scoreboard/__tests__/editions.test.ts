import { describe, expect, it } from "vitest";

import { editionFrom } from "../../nav";
import { cutDay, editionLabel, editionNote, editionTitle, isRerun } from "../editions";

// Editions S4 — the switcher's copy and the ?edition= parse, as pure functions.

const RECORD = {
  number: 0,
  label: "the original record",
  causes: [],
  cut: null,
  first_night: "2026-07-10",
  last_night: "2026-10-21",
  n_pages: null,
  n_entries: null,
  n_entries_estimated: null,
  engine_fingerprint: null,
  config_hash: null,
  code_sha: null,
  published_at: null,
};

const ONE = {
  ...RECORD,
  number: 1,
  label: "first re-run",
  causes: ["initial"],
  cut: "2026-10-22T21:59:07.956978+00:00",
  first_night: "2026-06-10",
  n_pages: 659,
  n_entries: 727,
  n_entries_estimated: 727,
};

describe("edition labels", () => {
  it("Edition 0 is the original record; a re-run names its cut day", () => {
    expect(editionLabel(RECORD)).toBe("Edition 0 · the original record");
    expect(editionLabel(ONE)).toBe("Edition 1 · first re-run · cut Oct 22");
  });

  it("the cut is dated on the MARKET calendar (a 02:45 UTC cut is the previous ET evening)", () => {
    expect(cutDay("2026-10-22T02:45:00+00:00")).toBe("Oct 21");
    expect(cutDay(null)).toBeNull();
    expect(cutDay("not a date")).toBeNull();
  });

  it("the hover says what each edition IS", () => {
    expect(editionTitle(RECORD)).toMatch(/^the calls log: what the platform actually said each night/);
    expect(editionTitle(ONE)).toMatch(/re-run from each company's join date, judged on the public clock/);
    expect(editionTitle(ONE)).toMatch(/727 of 727 join dates estimated · cut for: initial/);
  });

  it("the quiet note renders for a re-run only", () => {
    expect(editionNote(RECORD)).toBeNull();
    expect(editionNote(null)).toBeNull();
    expect(editionNote(ONE)).toBe(
      "nights Jun 10 – Oct 21 · 727 of 727 join dates estimated · no reconstructed, annotated or late nights in a re-run",
    );
  });

  it("only N >= 1 is a re-run", () => {
    expect([isRerun(0), isRerun(null), isRerun(undefined), isRerun(1), isRerun(2)]).toEqual([
      false,
      false,
      false,
      true,
      true,
    ]);
  });
});

describe("?edition= parse", () => {
  it("a whole number reads as itself (0 included); absent and junk read as the server default", () => {
    expect(editionFrom("0")).toBe(0);
    expect(editionFrom("1")).toBe(1);
    expect(editionFrom("12")).toBe(12);
    for (const junk of [null, "", "-1", "1.5", "one", "1e3", "99999"]) expect(editionFrom(junk)).toBeNull();
  });
});
