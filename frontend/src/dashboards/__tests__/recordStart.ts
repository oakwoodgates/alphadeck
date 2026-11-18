// Test helper for the "record starts" note (W8c). A dashboard test stubs `useRecordStart` with this and sets
// what the stub returns: `undefined` (the default) is a read still loading or failed, which renders no note, so
// a test that is not about the note never sees one. A dashboard test adds, at the top level:
//
//   vi.mock("../../../api/hooks", async (orig) =>
//     (await import("../../__tests__/recordStart")).withRecordStartStub(await orig<typeof import("../../../api/hooks")>()));
//
// and calls `setRecordStart("2026-12-03")` (or `null`) in the test, then `setRecordStart(undefined)` after.
import { afterEach } from "vitest";

let value: string | null | undefined;

export function setRecordStart(v: string | null | undefined) {
  value = v;
}

// every test starts and ends with the default (no note), so one test's date never leaks into the next
afterEach(() => setRecordStart(undefined));

export function withRecordStartStub<T extends object>(real: T): T & { useRecordStart: () => { data: string | null | undefined } } {
  return { ...real, useRecordStart: () => ({ data: value }) };
}
