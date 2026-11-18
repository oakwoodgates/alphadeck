import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ScoredFigureOut, ScoredMemberOut } from "../../api/hooks";
import { DDRail } from "../DDRail";
import { meterValueLabel, runwayCashWords, runwayPaceWords } from "../format";

// V2 extractor correctness PR 3 (operator decision D3): the runway reads AT LAST QUARTER'S PACE; the year-to-date
// pace and the runway it implies ride beside it when the stored note carries one (the scored read threads them
// on the runway provenance's detail), and a quiet one-sentence caveat appears only when last quarter is far off that
// pace. Display only: the pips never change.

vi.mock("../CatalystFactForm", () => ({ CatalystFactForm: () => null }));
vi.mock("../FactsPanel", () => ({ FactsPanel: () => null }));
vi.mock("../SharedValues", () => ({ SharedValues: () => null }));

const UP_CAVEAT =
  "Last quarter's burn ($17.1M) is well below the year-to-date pace ($69.9M a quarter): runway reads 9.1 months at last quarter's pace and 2.2 months at the year-to-date pace.";

const runway = (detail: Record<string, unknown>, value: number | null = 9.1): ScoredFigureOut => ({
  pips: 1,
  value,
  provenance: [{ source: "10-q", ref: "https://www.sec.gov/x.htm", detail: { cash_usd: 51_811_000, ...detail } }],
});
const UP = runway({
  quarterly_burn_usd: 17_085_000,
  ytd_quarterly_burn_usd: 69_943_025,
  ytd_burn_days: 272,
  ytd_runway_months: 2.2,
  pace_caveat: UP_CAVEAT,
});
const STEADY = runway({ quarterly_burn_usd: 77_406_000, ytd_quarterly_burn_usd: 88_779_250, ytd_runway_months: 18.6 }, 21.3);
const GENERATOR = runway({ quarterly_burn_usd: -357_700_000, ytd_quarterly_burn_usd: -321_507_284 }, null);
const OLD = runway({ quarterly_burn_usd: 17_085_000 });

// GAP 3: a yuan annual value, stored with its currency and investment split (0208)
const YUAN: ScoredFigureOut = {
  pips: 4,
  value: 72,
  provenance: [
    {
      source: "annual-statements",
      ref: "https://www.sec.gov/y.htm",
      detail: {
        cash_usd: 1_200_000_000,
        quarterly_burn_usd: 50_000_000,
        currency: "CNY",
        investments_usd: 300_000_000,
        ytd_quarterly_burn_usd: 40_000_000,
        ytd_runway_months: 90,
      },
    },
  ],
};

describe("the runway's figures in their own currency", () => {
  it("prints a yuan value in CN¥ with its split, and a quarterly one in $", () => {
    expect(runwayCashWords(YUAN)).toBe(
      "Cash CN¥1.2B, of which short-term investments CN¥300.0M; burn CN¥50.0M last quarter",
    );
    expect(runwayPaceWords(YUAN)?.ytd).toBe("Year-to-date pace: CN¥40.0M burn a quarter, 90 mo of runway");
    expect(runwayCashWords(UP)).toBe("Cash $51.8M; burn $17.1M last quarter");
  });

  it("reads '?' for an annual value with no currency, never '$'", () => {
    const d = { ...YUAN.provenance[0].detail, currency: undefined };
    const unread = { ...YUAN, provenance: [{ ...YUAN.provenance[0], detail: d }] };
    expect(runwayCashWords(unread)).toBe("Cash ?1.2B, of which short-term investments ?300.0M; burn ?50.0M last quarter");
  });

  it("shows the cash line quietly on the rail", () => {
    render(<DDRail member={member(YUAN)} />);
    expect(screen.getByTestId("dd-runway-cash")).toHaveTextContent(
      "Cash CN¥1.2B, of which short-term investments CN¥300.0M; burn CN¥50.0M last quarter",
    );
    expect(screen.getByTestId("dd-runway-pace")).toHaveTextContent("CN¥40.0M");
  });
});

describe("the runway label and its pace words", () => {
  it("labels the runway at last quarter's pace", () => {
    expect(meterValueLabel("runway", UP)).toBe("9.1 mo at last quarter's pace");
    expect(meterValueLabel("runway", GENERATOR)).toBe("cash-generative last quarter");
  });

  it("gives the year-to-date pace and its runway, and the caveat only when it fires", () => {
    expect(runwayPaceWords(UP)).toEqual({
      ytd: "Year-to-date pace: $69.9M burn a quarter, 2.2 mo of runway",
      caveat: UP_CAVEAT,
    });
    expect(runwayPaceWords(STEADY)).toEqual({
      ytd: "Year-to-date pace: $88.8M burn a quarter, 18.6 mo of runway",
      caveat: null,
    });
    expect(runwayPaceWords(GENERATOR)).toEqual({
      ytd: "Year-to-date pace: cash-generative, $321.5M a quarter",
      caveat: null,
    });
  });

  it("says nothing more for a value whose note carries no pace (a first quarter, an annual or hand-entered value)", () => {
    expect(runwayPaceWords(OLD)).toBeNull();
    expect(runwayPaceWords({ pips: null, value: null, provenance: [] })).toBeNull();
  });
});

const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
const member = (rw: ScoredFigureOut) =>
  ({
    security_id: "s-up",
    ticker: "UP",
    name: "Wheels Up",
    sector: null,
    exchange: null,
    category: null,
    origin: null,
    foreign_filer_form: null,
    price_symbol: null,
    business_type: null,
    business_supersector: null,
    business_type_override: null,
    royalty: false,
    instrument_kind: "equity",
    segment: null,
    purity: fig(null, null),
    runway: rw,
    catalysts: fig(0, 0),
    dilution: fig(null, null),
    market_cap: fig(null, null),
    fit: "unrated",
    unconfirmed_estimates: 0,
    thin_price_history: false,
  }) as unknown as ScoredMemberOut;

describe("DDRail — the scored runway's provenance", () => {
  it("shows the label, the year-to-date pace and the caveat for UP", () => {
    render(<DDRail member={member(UP)} />);
    expect(screen.getByText("9.1 mo at last quarter's pace")).toBeInTheDocument();
    expect(screen.getByTestId("dd-runway-pace")).toHaveTextContent("Year-to-date pace: $69.9M burn a quarter, 2.2 mo of runway");
    expect(screen.getByTestId("dd-runway-caveat")).toHaveTextContent(UP_CAVEAT);
  });

  it("shows a steady burner's pace quietly, with no caveat", () => {
    render(<DDRail member={member(STEADY)} />);
    expect(screen.getByTestId("dd-runway-pace")).toBeInTheDocument();
    expect(screen.queryByTestId("dd-runway-caveat")).toBeNull();
  });

  it("adds nothing for a value without a pace", () => {
    render(<DDRail member={member(OLD)} />);
    expect(screen.getByText("9.1 mo at last quarter's pace")).toBeInTheDocument();
    expect(screen.queryByTestId("dd-runway-pace")).toBeNull();
    expect(screen.queryByTestId("dd-runway-caveat")).toBeNull();
  });
});
