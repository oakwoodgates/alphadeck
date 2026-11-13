import { describe, expect, it } from "vitest";

import { ALLOWANCE_PREFIX, NOT_ON_YOUR_CARDS, refusalText } from "../refusals";

// The API's two fixed refusals on a user-triggered fetch (security hardening PR 1b): the own-cards 404 and the
// daily-allowance 429. They are answers, not breakages, so the screens show them as the API words them.
describe("refusalText", () => {
  it("returns the own-cards 404 message as it is", () => {
    expect(refusalText({ detail: NOT_ON_YOUR_CARDS })).toBe(NOT_ON_YOUR_CARDS);
  });

  it("returns a daily-allowance 429 message as it is", () => {
    const msg = `${ALLOWANCE_PREFIX} 5,000 extracts a day. It resets at midnight America/New_York time.`;
    expect(refusalText({ detail: msg })).toBe(msg);
  });

  it("is null for any other error", () => {
    expect(refusalText({ detail: "extraction failed: boom" })).toBeNull();
    expect(refusalText({ detail: [{ msg: "x" }] })).toBeNull();
    expect(refusalText(null)).toBeNull();
    expect(refusalText(new Error("network"))).toBeNull();
  });
});
