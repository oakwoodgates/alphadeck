import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  downloadJson,
  exportFilename,
  exportKeptNames,
  slugForFilename,
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
});
