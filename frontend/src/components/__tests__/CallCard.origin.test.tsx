import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CallCardResponse } from "../../api/hooks";
import { CallCard } from "../CallCard";

function cardWith(trigger: Record<string, unknown>) {
  return {
    thesis_id: "t1",
    asof: "2026-06-20",
    state: "armed",
    verdict: "core_entry",
    conviction_grade: null,
    confirmation_grade: null,
    entry_grade: null,
    armed_security_id: null,
    expression: null,
    exit_by: null,
    arm_until: null,
    catalyst_surface: [],
    confidence: null,
    key_conviction: { turned: true, detail: "c" },
    key_confirmation: { turned: false, detail: "c" },
    triggers_fired: [{ label: "NRC decision", kind: "catalyst", grade: "core", ticker: "SMR", sources: [], ...trigger }],
    risk_signals: [],
    missing: [],
    counter_case: "",
    safe_sleeve: null,
    armed_members: [],
    watch_members: [],
  } as unknown as CallCardResponse;
}

const own = { source: "thesis_catalyst", ref: "e1", url: null, detail: { counts_from: "2026-11-18" } };

describe("CallCard — origin chip and the also-live line", () => {
  it("marks an operator entry with 'your entry' and says when it counts from", () => {
    render(<CallCard card={cardWith({ origin: "operator", sources: [own] })} />);
    const chip = screen.getByText("your entry");
    expect(chip).toHaveAttribute("title", "From your catalyst list, counted since Nov 18.");
  });

  it.each([["platform"], [null], [undefined]])("shows no chip for origin %s", (origin) => {
    render(<CallCard card={cardWith({ origin })} />);
    expect(screen.queryByText("your entry")).toBeNull();
    expect(screen.queryByText(/Also live, not counted/)).toBeNull();
  });

  it("shows the also-live line only when a source carries it, with its link through ExternalLink", () => {
    const alsoLive = {
      source: "catalyst",
      ref: "p1",
      url: "https://example.com/p",
      detail: { also_live: true, counted: false, label: "DOE loan guarantee" },
    };
    const { rerender } = render(<CallCard card={cardWith({ origin: "operator", sources: [own] })} />);
    expect(screen.queryByText(/Also live, not counted/)).toBeNull();
    rerender(<CallCard card={cardWith({ origin: "operator", sources: [own, alsoLive] })} />);
    expect(screen.getByText(/Also live, not counted: DOE loan guarantee \(platform data\)/)).toBeInTheDocument();
    const link = screen.getAllByRole("link").find((a) => a.getAttribute("href") === "https://example.com/p");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("data-destination", "external");
  });

  it("names the other origin: '(your entry)' when the platform's catalyst won", () => {
    const alsoLive = {
      source: "thesis_catalyst",
      ref: "e1",
      url: null,
      detail: { also_live: true, counted: false, label: "My note" },
    };
    render(<CallCard card={cardWith({ origin: "platform", sources: [alsoLive] })} />);
    expect(screen.getByText(/Also live, not counted: My note \(your entry\)/)).toBeInTheDocument();
  });

  it("renders a label as plain text: no markup", () => {
    const nasty = "<b>x</b> [a](https://evil.example)";
    const alsoLive = {
      source: "catalyst",
      ref: "p",
      url: null,
      detail: { also_live: true, counted: false, label: nasty },
    };
    const { container } = render(<CallCard card={cardWith({ origin: "operator", sources: [own, alsoLive] })} />);
    expect(screen.getByText((t) => t.includes(nasty))).toBeInTheDocument();
    expect(container.querySelector(".also-live b, .also-live a, .also-live img")).toBeNull();
  });
});
