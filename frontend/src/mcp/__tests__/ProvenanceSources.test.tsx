import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ProvenanceSources } from "../../components/ProvenanceSources";

describe("real provenance disclosure", () => {
  it("starts collapsed for the operator, expands all existing sources/details, and closes again without a fetch", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const { container } = render(<ProvenanceSources path="" pointer="" scope="example" sources={[
      { source: "fixture_filing", ref: "example-a", url: "https://example.com/a", detail: { value: 12.5 } },
      { source: "fixture_computation", ref: "example-b", url: null, detail: { count: 18 } },
    ]} />);
    const disclosure = container.querySelector("details")!;
    expect(disclosure).not.toHaveAttribute("open");
    await userEvent.click(screen.getByText("Sources · 2"));
    expect(disclosure).toHaveAttribute("open");
    expect(screen.getByRole("link", { name: "https://example.com/a" })).toBeInTheDocument();
    expect(container.textContent).toContain('"count": 18');
    await userEvent.click(screen.getByText("Sources · 2"));
    expect(disclosure).not.toHaveAttribute("open");
    expect(fetch).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("keeps non-http source URLs as inert reference text", async () => {
    render(<ProvenanceSources path="" pointer="" scope="example" sources={[
      { source: "fixture", ref: "a", url: "javascript:alert(1)", detail: {} },
      { source: "fixture", ref: "b", url: "data:text/plain,example", detail: {} },
    ]} />);
    await userEvent.click(screen.getByText("Sources · 2"));
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText("javascript:alert(1)")).toBeInTheDocument();
  });
});
