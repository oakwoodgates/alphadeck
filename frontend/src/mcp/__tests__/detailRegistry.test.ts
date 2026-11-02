import { lazy } from "react";
import { describe, expect, it } from "vitest";
import { buildDetailRegistry, DETAIL_CAPTURES } from "../detailRegistry";

const example = (slug: string) => ({
  capability: { slug, label: slug, description: "Synthetic detail", surface_kind: "detail", capabilities: { observe: true } },
  component: lazy(async () => ({ default: () => null })),
});

describe("discoverable detail captures", () => {
  it("registers the real Cockpit outside the dashboard menu", () => {
    expect(DETAIL_CAPTURES.map((entry) => entry.capability.slug)).toEqual(["cockpit"]);
  });
  it("adds and removes a second domain-owned detail without a harness edit", () => {
    const first = example("first"), second = example("second");
    const modules = { "../first/captureEntry.ts": { captureEntry: first }, "../second/captureEntry.ts": { captureEntry: second } };
    const metadata = { "../first/capability.json": first.capability, "../second/capability.json": second.capability };
    expect(buildDetailRegistry(modules, metadata).map((entry) => entry.capability.slug)).toEqual(["first", "second"]);
    expect(buildDetailRegistry({ "../first/captureEntry.ts": modules["../first/captureEntry.ts"] }, { "../first/capability.json": first.capability })).toHaveLength(1);
  });
  it("fails on orphan metadata, missing metadata, and a mismatched entry", () => {
    const first = example("first");
    expect(() => buildDetailRegistry({}, { "../first/capability.json": first.capability })).toThrow("no captureEntry");
    expect(() => buildDetailRegistry({ "../first/captureEntry.ts": { captureEntry: first } }, {})).toThrow("requires captureEntry");
    expect(() => buildDetailRegistry({ "../first/captureEntry.ts": { captureEntry: first } }, { "../first/capability.json": example("second").capability })).toThrow("mismatched");
  });
});
