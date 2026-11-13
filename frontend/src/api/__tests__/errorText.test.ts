import { describe, expect, it } from "vitest";

import { errorDetail } from "../../auth/me";
import { errText } from "../../workbench/format";
import { apiErrorText } from "../errorText";

// A request over one of the API's input limits (security hardening PR 3) is a 422 whose `detail` is FastAPI's LIST
// (`[{loc, msg, type}]`), not a sentence. The screens that show `detail` show its FIRST message plainly, naming the
// field, instead of a generic fallback.
const tooLong = {
  detail: [
    { type: "string_too_long", loc: ["body", "name"], msg: "String should have at most 200 characters", ctx: { max_length: 200 } },
    { type: "string_too_long", loc: ["body", "narrative"], msg: "String should have at most 20000 characters" },
  ],
};
const control = {
  detail: [
    {
      type: "value_error",
      loc: ["body", "basket", 0, "role"],
      msg: "Value error, must not contain a control character (a NUL, a tab, a line break or similar)",
    },
  ],
};

describe("apiErrorText", () => {
  it("returns a sentence detail as it is", () => {
    expect(apiErrorText({ detail: "that email is already in use" })).toBe("that email is already in use");
  });

  it("shows the first validation message plainly, naming the field", () => {
    expect(apiErrorText(tooLong)).toBe("name: String should have at most 200 characters.");
  });

  it("drops pydantic's 'Value error,' prefix and names the innermost field", () => {
    expect(apiErrorText(control)).toBe(
      "role: must not contain a control character (a NUL, a tab, a line break or similar).",
    );
  });

  it("names a query parameter and a whole-list body too", () => {
    expect(apiErrorText({ detail: [{ loc: ["query", "q"], msg: "String should have at most 100 characters" }] })).toBe(
      "q: String should have at most 100 characters.",
    );
    expect(apiErrorText({ detail: [{ loc: ["body"], msg: "List should have at most 500 items after validation, not 501" }] })).toBe(
      "List should have at most 500 items after validation, not 501.",
    );
  });

  it("is null when there is nothing readable", () => {
    expect(apiErrorText(null)).toBeNull();
    expect(apiErrorText(new Error("network"))).toBeNull();
    expect(apiErrorText({ detail: [] })).toBeNull();
    expect(apiErrorText({ detail: "" })).toBeNull();
    expect(apiErrorText({ detail: [{ nope: 1 }] })).toBeNull();
  });
});

describe("the screens' error lines read a limit 422", () => {
  it("errorDetail (Settings, sign-in) shows the first message instead of the fallback", () => {
    expect(errorDetail(tooLong, "The profile was not saved.")).toBe("name: String should have at most 200 characters.");
    expect(errorDetail({ detail: "plain" }, "fallback")).toBe("plain");
    expect(errorDetail(null, "fallback")).toBe("fallback");
  });

  it("errText (Workbench) shows the first message instead of 'the request was rejected'", () => {
    expect(errText(control)).toBe(
      "role: must not contain a control character (a NUL, a tab, a line break or similar).",
    );
    expect(errText({ detail: [] })).toBe("the request was rejected");
  });
});
