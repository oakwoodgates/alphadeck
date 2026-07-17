import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  downloadJson,
  exportFilename,
  exportKeptNames,
  exportSegmentedNames,
  slugForFilename,
  sortByTicker,
  toExportedName,
} from "../exportNames";

describe("exportNames", () => {
  describe("toExportedName", () => {
    it("normalizes ticker and name", () => {
      expect(toExportedName({ ticker: "OKLO", name: "Oklo Inc." })).toEqual({
        ticker: "OKLO",
        name: "Oklo Inc.",
      });
      expect(toExportedName({ ticker: null, name: undefined })).toEqual({
        ticker: "",
        name: null,
      });
    });
  });

  describe("slugForFilename", () => {
    it("slugifies thesis names for safe filenames", () => {
      expect(slugForFilename("Uranium & Nuclear")).toBe("Uranium-Nuclear");
      expect(slugForFilename("  DRAM / HBM  ")).toBe("DRAM-HBM");
      expect(slugForFilename("!!!")).toBe("thesis");
    });
  });

  describe("exportFilename", () => {
    it("builds an identifiable stage-dated filename", () => {
      expect(exportFilename("Uranium", "shortlist", "2026-06-08")).toBe(
        "Uranium-shortlist-2026-06-08.json",
      );
    });
  });

  describe("downloadJson", () => {
    let createObjectURL: ReturnType<typeof vi.fn>;
    let revokeObjectURL: ReturnType<typeof vi.fn>;
    let click: ReturnType<typeof vi.fn>;
    let capturedAnchor: HTMLAnchorElement | null;

    beforeEach(() => {
      capturedAnchor = null;
      createObjectURL = vi.fn(() => "blob:mock");
      revokeObjectURL = vi.fn();
      click = vi.fn();
      vi.stubGlobal("URL", {
        createObjectURL,
        revokeObjectURL,
      });
      vi.spyOn(document, "createElement").mockImplementation((tagName: string) => {
        const el = document.createElementNS("http://www.w3.org/1999/xhtml", tagName);
        if (tagName === "a") capturedAnchor = el as HTMLAnchorElement;
        return el;
      });
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(click);
    });

    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    it("stringifies data, triggers a download, and revokes the blob URL", () => {
      const rows = [{ ticker: "URA", name: "Global X Uranium ETF" }];
      const stringifySpy = vi.spyOn(JSON, "stringify");
      downloadJson("uranium-board-2026-06-08.json", rows);

      expect(stringifySpy).toHaveBeenCalledWith(rows, null, 2);
      expect(createObjectURL).toHaveBeenCalledOnce();
      expect(click).toHaveBeenCalledOnce();
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock");
      expect(capturedAnchor?.download).toBe("uranium-board-2026-06-08.json");
      expect(capturedAnchor?.href).toBe("blob:mock");
    });
  });

  describe("sortByTicker", () => {
    it("orders rows alphabetically by ticker (case-insensitive), tie-broken by name", () => {
      const rows = [
        { ticker: "URA", name: "Global X Uranium ETF" },
        { ticker: "CCJ", name: "Cameco" },
        { ticker: "ccj", name: "Cameco dup" }, // case-insensitive → groups with CCJ, tie-broken by name
        { ticker: "NXE", name: "NexGen" },
      ];
      expect(sortByTicker(rows).map((r) => `${r.ticker}:${r.name}`)).toEqual([
        "CCJ:Cameco",
        "ccj:Cameco dup",
        "NXE:NexGen",
        "URA:Global X Uranium ETF",
      ]);
    });

    it("does not mutate the input array", () => {
      const rows = [
        { ticker: "URA", name: null },
        { ticker: "CCJ", name: null },
      ];
      sortByTicker(rows);
      expect(rows.map((r) => r.ticker)).toEqual(["URA", "CCJ"]); // original order intact
    });
  });

  describe("exportKeptNames", () => {
    let click: ReturnType<typeof vi.fn>;
    let capturedAnchor: HTMLAnchorElement | null;

    beforeEach(() => {
      capturedAnchor = null;
      click = vi.fn();
      vi.stubGlobal("URL", {
        createObjectURL: vi.fn(() => "blob:mock"),
        revokeObjectURL: vi.fn(),
      });
      vi.spyOn(document, "createElement").mockImplementation((tagName: string) => {
        const el = document.createElementNS("http://www.w3.org/1999/xhtml", tagName);
        if (tagName === "a") capturedAnchor = el as HTMLAnchorElement;
        return el;
      });
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(click);
    });

    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    it("exports kept rows under a stage-specific filename", () => {
      const rows = [{ ticker: "CCJ", name: "Cameco" }];
      const stringifySpy = vi.spyOn(JSON, "stringify");
      exportKeptNames({
        thesisName: "Uranium",
        stage: "triage",
        asof: "2026-06-08",
        rows,
      });

      expect(stringifySpy).toHaveBeenCalledWith(rows, null, 2);
      expect(click).toHaveBeenCalledOnce();
      expect(capturedAnchor?.download).toBe("Uranium-triage-2026-06-08.json");
    });
  });

  describe("exportSegmentedNames", () => {
    let click: ReturnType<typeof vi.fn>;
    let capturedAnchor: HTMLAnchorElement | null;

    beforeEach(() => {
      capturedAnchor = null;
      click = vi.fn();
      vi.stubGlobal("URL", {
        createObjectURL: vi.fn(() => "blob:mock"),
        revokeObjectURL: vi.fn(),
      });
      vi.spyOn(document, "createElement").mockImplementation((tagName: string) => {
        const el = document.createElementNS("http://www.w3.org/1999/xhtml", tagName);
        if (tagName === "a") capturedAnchor = el as HTMLAnchorElement;
        return el;
      });
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(click);
    });

    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    it("writes an object keyed by group, each group sorted, order preserved, empty groups dropped", () => {
      const stringifySpy = vi.spyOn(JSON, "stringify");
      exportSegmentedNames({
        thesisName: "Uranium",
        stage: "all",
        asof: "2026-06-08",
        groups: [
          { label: "Reactors", rows: [{ ticker: "SMR", name: "NuScale" }, { ticker: "OKLO", name: "Oklo" }] },
          { label: "Fuel", rows: [{ ticker: "URA", name: "Global X" }, { ticker: "CCJ", name: "Cameco" }] },
          { label: "Empty", rows: [] }, // dropped
          { label: "To Review", rows: [{ ticker: "LEU", name: "Centrus" }] },
        ],
      });

      const written = stringifySpy.mock.calls[0][0] as Record<string, { ticker: string }[]>;
      // group order preserved (chain order, buckets last); Empty dropped
      expect(Object.keys(written)).toEqual(["Reactors", "Fuel", "To Review"]);
      // each group alphabetical by ticker
      expect(written.Reactors.map((r) => r.ticker)).toEqual(["OKLO", "SMR"]);
      expect(written.Fuel.map((r) => r.ticker)).toEqual(["CCJ", "URA"]);
      expect(capturedAnchor?.download).toBe("Uranium-all-2026-06-08.json");
      expect(click).toHaveBeenCalledOnce();
    });

    it("merges rows for a repeated group label rather than dropping one", () => {
      const stringifySpy = vi.spyOn(JSON, "stringify");
      exportSegmentedNames({
        thesisName: "T",
        stage: "all",
        asof: "2026-06-08",
        groups: [
          { label: "Discovered", rows: [{ ticker: "ZZZ", name: null }] },
          { label: "Discovered", rows: [{ ticker: "AAA", name: null }] },
        ],
      });
      const written = stringifySpy.mock.calls[0][0] as Record<string, { ticker: string }[]>;
      expect(written.Discovered.map((r) => r.ticker)).toEqual(["AAA", "ZZZ"]);
    });
  });
});
