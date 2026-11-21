import { describe, expect, it } from "vitest";

import { NO_SHARED_SHARES } from "../../components/valueWords";
import { AUTHORITY_MEANS, KIND_LABEL as LEDGER_KIND_LABEL } from "../evidence-ledger/ledgerView";
import { KIND_LABEL as STREAM_KIND_LABEL } from "../filing-stream/streamView";
import { GAP_WORDS } from "../peer-table/tableView";
import { CAP_GAP_WORDS as LOOKUP_CAP } from "../ticker-lookup/lookupView";
import { CAP_GAP_WORDS as TRACKER_CAP } from "../insider-tracker/trackerView";

// V2 W5g: a share count and cash are PLATFORM VALUES (from the filing, or verified by the steward); a user reads
// them and never ratifies or confirms them. The user's own PURITY is still theirs to ratify, so purity wording is
// allowed. This scans every dashboard view module's source for a line that tells the user to ratify or confirm a
// share count or cash value (comments are skipped: only strings a person reads).

const sources = import.meta.glob(["../**/*.ts", "../**/*.tsx", "!../**/__tests__/**", "!../**/*.test.*"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const isComment = (line: string) => /^\s*(\/\/|\/\*|\*)/.test(line);
const VALUE = /\b(shares?|share counts?|cash|burn|runway)\b/i;
const OPERATOR_VERB = /(ratif|operator[- ]confirm|confirm(ed)? (the |a )?(share|cash)|mark(ed)? (the name |it )?for data|get data|unconfirmed)/i;
const PURITY = /(purity|you ratified|ratified by you|ratifiedWords|facts_ratified|ratified_on)/i;

describe("dashboard words: shared values are never ratified or confirmed by the user", () => {
  it("scans the dashboards' view modules", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(30);
  });

  it("no string tells the user to ratify, confirm or 'get data' for a share count or cash value", () => {
    const hits: string[] = [];
    for (const [file, src] of Object.entries(sources)) {
      src.split(/\r?\n/).forEach((line, i) => {
        if (isComment(line) || !VALUE.test(line) || !OPERATOR_VERB.test(line) || PURITY.test(line)) return;
        hits.push(`${file}:${i + 1}: ${line.trim()}`);
      });
    }
    expect(hits).toEqual([]);
  });

  it("no string names the retired V1 steps anywhere", () => {
    const hits: string[] = [];
    for (const [file, src] of Object.entries(sources)) {
      src.split(/\r?\n/).forEach((line, i) => {
        if (isComment(line)) return;
        if (/mark the name for data|Ratified and stored facts|ratified or stored fact|Workbench fact|awaiting shares|awaiting a share count/i.test(line)) {
          hits.push(`${file}:${i + 1}: ${line.trim()}`);
        }
      });
    }
    expect(hits).toEqual([]);
  });

  it("every dashboard that blanks a cap for want of a share count says it the one way", () => {
    expect(NO_SHARED_SHARES).toBe("no shared share count yet");
    expect(LOOKUP_CAP.no_shares).toBe(NO_SHARED_SHARES);
    expect(TRACKER_CAP.no_shares).toBe(NO_SHARED_SHARES);
    expect(GAP_WORDS.no_shares).toContain(NO_SHARED_SHARES);
  });

  it("the shared-value group is named for what it holds: shared values and your purity", () => {
    expect(STREAM_KIND_LABEL.ratified).toBe("Shared values and your purity");
    expect(LEDGER_KIND_LABEL.ratified).toBe("Shared values and your purity");
    expect(AUTHORITY_MEANS.automatic).toBe("applied automatically from a filing, or stored by an ingest leg; no person set it");
  });
});
