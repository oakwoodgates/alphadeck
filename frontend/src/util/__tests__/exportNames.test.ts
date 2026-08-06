import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildWatchlistTxt,
  downloadJson,
  exportFilename,
  exportKeptNames,
  exportSegmentedNames,
  exportWatchlist,
  slugForFilename,
  sortByTicker,
  toExportedName,
  tvExchangePrefix,
  tvSymbol,
  watchlistFilename,
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

  describe("tvExchangePrefix", () => {
    it("maps the confident security-master exchanges to TradingView codes", () => {
      // measured live vocabulary: Nasdaq / NYSE / OTC dominate the universe
      expect(tvExchangePrefix("Nasdaq")).toBe("NASDAQ");
      expect(tvExchangePrefix("NYSE")).toBe("NYSE");
      expect(tvExchangePrefix("OTC")).toBe("OTC");
    });

    it("maps NYSE American and NYSE Arca (US-listed ETFs) to AMEX", () => {
      expect(tvExchangePrefix("NYSE American")).toBe("AMEX");
      expect(tvExchangePrefix("NYSEAmerican")).toBe("AMEX");
      expect(tvExchangePrefix("NYSE Arca")).toBe("AMEX"); // e.g. AMEX:SPY
    });

    it("is case- and whitespace-insensitive and handles Nasdaq tiers", () => {
      expect(tvExchangePrefix("nasdaq")).toBe("NASDAQ");
      expect(tvExchangePrefix("  NasdaqGS ")).toBe("NASDAQ");
      expect(tvExchangePrefix("nyse")).toBe("NYSE");
    });

    it("returns null for the ambiguous tail → the caller falls back to a bare ticker", () => {
      // a WRONG prefix fails to resolve — worse than bare — so CBOE / unknown / null stay unmapped
      expect(tvExchangePrefix("CBOE")).toBeNull();
      expect(tvExchangePrefix("BATS")).toBeNull();
      expect(tvExchangePrefix("IEX")).toBeNull();
      expect(tvExchangePrefix("")).toBeNull();
      expect(tvExchangePrefix(null)).toBeNull();
      expect(tvExchangePrefix(undefined)).toBeNull();
    });
  });

  describe("tvSymbol", () => {
    it("prefixes with the mapped exchange and uppercases the ticker", () => {
      expect(tvSymbol({ ticker: "MU", exchange: "Nasdaq" })).toBe("NASDAQ:MU");
      expect(tvSymbol({ ticker: "IBM", exchange: "NYSE" })).toBe("NYSE:IBM");
      expect(tvSymbol({ ticker: "nlst", exchange: "OTC" })).toBe("OTC:NLST");
    });

    it("emits a bare ticker when the exchange is unmappable or missing", () => {
      expect(tvSymbol({ ticker: "xyz", exchange: "CBOE" })).toBe("XYZ"); // unmappable → bare
      expect(tvSymbol({ ticker: "ARM", exchange: null })).toBe("ARM"); // null → bare
      expect(tvSymbol({ ticker: "ARM" })).toBe("ARM"); // absent → bare
    });

    it("returns an empty string for a blank ticker", () => {
      expect(tvSymbol({ ticker: "", exchange: "Nasdaq" })).toBe("");
      expect(tvSymbol({ ticker: "   ", exchange: "NYSE" })).toBe("");
    });
  });

  describe("buildWatchlistTxt", () => {
    it("writes a ###section header + comma-joined symbols, sorted by ticker", () => {
      // a real slice of the AI Memory basket (measured exchanges)
      const txt = buildWatchlistTxt("AI Memory & Storage", [
        { ticker: "IBM", exchange: "NYSE" },
        { ticker: "MU", exchange: "Nasdaq" },
        { ticker: "ATEYY", exchange: "OTC" },
        { ticker: "NVDA", exchange: "Nasdaq" },
      ]);
      expect(txt).toBe("###AI Memory & Storage,OTC:ATEYY,NYSE:IBM,NASDAQ:MU,NASDAQ:NVDA");
    });

    it("drops ticker-less rows and de-duplicates by emitted symbol", () => {
      const txt = buildWatchlistTxt("T", [
        { ticker: "MU", exchange: "Nasdaq" },
        { ticker: "mu", exchange: "Nasdaq" }, // same symbol NASDAQ:MU → deduped
        { ticker: "", exchange: "NYSE" }, // no ticker → dropped
      ]);
      expect(txt).toBe("###T,NASDAQ:MU");
    });

    it("keeps a bare and a prefixed listing of the same ticker (distinct TV symbols)", () => {
      const txt = buildWatchlistTxt("T", [
        { ticker: "X", exchange: "Nasdaq" }, // NASDAQ:X
        { ticker: "X", exchange: "CBOE" }, // bare X (unmappable) — a different symbol, kept
      ]);
      expect(txt).toBe("###T,NASDAQ:X,X");
    });

    it("strips commas from the section label (comma is the delimiter) and falls back when empty", () => {
      expect(buildWatchlistTxt("A, B, C", [{ ticker: "MU", exchange: "Nasdaq" }])).toBe(
        "###A B C,NASDAQ:MU",
      );
      expect(buildWatchlistTxt("   ", [{ ticker: "MU", exchange: "Nasdaq" }])).toBe(
        "###Watchlist,NASDAQ:MU",
      );
    });
  });

  describe("watchlistFilename", () => {
    it("builds a slugged, dated .txt filename", () => {
      expect(watchlistFilename("AI Memory & Storage", "2026-08-06")).toBe(
        "AI-Memory-Storage-watchlist-2026-08-06.txt",
      );
    });
  });

  describe("exportWatchlist", () => {
    let click: ReturnType<typeof vi.fn>;
    let capturedAnchor: HTMLAnchorElement | null;
    // jsdom's Blob has no async .text(); capture the parts at construction instead.
    let blobParts: BlobPart[] | undefined;
    let blobType: string | undefined;

    beforeEach(() => {
      capturedAnchor = null;
      blobParts = undefined;
      blobType = undefined;
      click = vi.fn();
      vi.stubGlobal(
        "Blob",
        class {
          constructor(parts: BlobPart[], opts?: BlobPropertyBag) {
            blobParts = parts;
            blobType = opts?.type;
          }
        },
      );
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

    it("downloads a .txt TradingView watchlist under a dated filename", () => {
      exportWatchlist({
        thesisName: "Uranium",
        asof: "2026-06-08",
        rows: [
          { ticker: "CCJ", exchange: "NYSE" },
          { ticker: "URA", exchange: "NYSE Arca" },
        ],
      });

      expect(click).toHaveBeenCalledOnce();
      expect(capturedAnchor?.download).toBe("Uranium-watchlist-2026-06-08.txt");
      expect(blobType).toBe("text/plain");
      expect(blobParts?.[0]).toBe("###Uranium,NYSE:CCJ,AMEX:URA");
    });
  });
});
