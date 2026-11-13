// ExternalLink — the ONE component for a link whose URL comes from data (security hardening PR 4a). It renders an
// anchor only for a plain http(s) URL with a host and no credentials (the backend's domain/links.py rule), always
// opens it in a new tab with rel="noopener noreferrer", and says where it goes: a link to sec.gov looks exactly as
// it always did; a link anywhere else shows that site's host and an external mark, with the full URL as its title.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExternalLink, isSecLink, linkDestination, safeHttpUrl } from "../ExternalLink";

// The hostile-output cases (spec item 6), the same list the backend's tests/domain/test_links.py uses.
const HOSTILE = [
  "javascript:alert(1)",
  "data:text/html,<script>alert(1)</script>",
  "https://user:pass@evil.example/",
  "https://evil.example/ ",
  "https://evil.example/" + "a".repeat(5000),
];

const SEC = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/";

describe("safeHttpUrl", () => {
  it("passes a well-formed http(s) URL through unchanged", () => {
    expect(safeHttpUrl(SEC)).toBe(SEC);
    expect(safeHttpUrl("http://example.com/a?b=c#d")).toBe("http://example.com/a?b=c#d");
    expect(safeHttpUrl("HTTPS://WWW.SEC.GOV/x")).toBe("HTTPS://WWW.SEC.GOV/x");
  });

  it.each(HOSTILE)("refuses the hostile case %#", (value) => {
    expect(safeHttpUrl(value)).toBeNull();
  });

  it.each([
    null,
    undefined,
    "",
    "0000320193-24-000123",
    "ftp://www.sec.gov/x",
    "vbscript:msgbox(1)",
    "//evil.example/x",
    "/relative",
    "https://user@evil.example/",
    "https://evil.example/a b",
    "https://evil.example/\nx",
    " https://evil.example/",
    "https://evil.example/\u00a0x",
    "https://",
  ])("refuses %j", (value) => {
    expect(safeHttpUrl(value)).toBeNull();
  });

  it("caps the length at 2,000 characters inclusive", () => {
    const base = "https://www.sec.gov/";
    const atCap = base + "a".repeat(2000 - base.length);
    expect(safeHttpUrl(atCap)).toBe(atCap);
    expect(safeHttpUrl(atCap + "a")).toBeNull();
  });
});

describe("linkDestination", () => {
  it("is sec for sec.gov and its subdomains only", () => {
    expect(linkDestination(SEC)).toBe("sec");
    expect(linkDestination("https://sec.gov/x")).toBe("sec");
    expect(linkDestination("https://efts.sec.gov/LATEST/search-index")).toBe("sec");
    expect(isSecLink(SEC)).toBe(true);
  });

  it("is external for everything else, including look-alikes", () => {
    for (const u of [
      "https://evil.example/sec.gov",
      "https://evil.example/?u=www.sec.gov",
      "https://sec.gov.evil.example/",
      "https://notsec.gov/",
      "https://investor.example.com/q3.pdf",
    ]) {
      expect(linkDestination(u)).toBe("external");
      expect(isSecLink(u)).toBe(false);
    }
  });

  it("is null for a URL that fails the rule", () => {
    expect(linkDestination("javascript:alert(1)")).toBeNull();
    expect(isSecLink("javascript:alert(1)")).toBe(false);
    expect(isSecLink(null)).toBe(false);
  });
});

describe("<ExternalLink>", () => {
  it("renders an SEC link exactly as before: the label, the class, a new tab, no host and no mark", () => {
    render(
      <ExternalLink className="radar-link" href={SEC}>
        filing ↗
      </ExternalLink>,
    );
    const a = screen.getByRole("link", { name: "filing ↗" });
    expect(a.getAttribute("href")).toBe(SEC);
    expect(a.className).toBe("radar-link");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.getAttribute("rel")).toBe("noopener noreferrer");
    expect(a.textContent).toBe("filing ↗");
    expect(a.getAttribute("data-destination")).toBe("sec");
  });

  it("keeps a caller's title on an SEC link", () => {
    render(
      <ExternalLink href={SEC} title="Open the company on EDGAR">
        EDGAR ↗
      </ExternalLink>,
    );
    expect(screen.getByRole("link").getAttribute("title")).toBe("Open the company on EDGAR");
  });

  it("marks a link that leaves SEC with its host and an external mark, the full URL as its title", () => {
    const url = "https://investor.example.com/press/q3.pdf";
    render(
      <ExternalLink className="dd-src" href={url} title="ignored for the destination">
        source ↗
      </ExternalLink>,
    );
    const a = screen.getByRole("link");
    expect(a.getAttribute("href")).toBe(url);
    expect(a.className).toBe("dd-src");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.getAttribute("rel")).toBe("noopener noreferrer");
    expect(a.getAttribute("data-destination")).toBe("external");
    expect(a.getAttribute("title")).toBe(`ignored for the destination · ${url}`);
    expect(a.textContent).toContain("source ↗");
    expect(a.textContent).toContain("investor.example.com");
    expect(a.querySelector(".xl-mark")).not.toBeNull();
  });

  it("keeps an accessible name, adding the host on a link that leaves SEC", () => {
    render(
      <>
        <ExternalLink href={SEC} ariaLabel="Open MU 10-Q on EDGAR">
          EDGAR ↗
        </ExternalLink>
        <ExternalLink href="https://evil.example/x" ariaLabel="Open MU note on its source">
          source ↗
        </ExternalLink>
      </>,
    );
    expect(screen.getByRole("link", { name: "Open MU 10-Q on EDGAR" })).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Open MU note on its source (external site: evil.example)" }),
    ).toBeTruthy();
  });

  it.each(HOSTILE)("renders no anchor for hostile case %#, only the label as text", (value) => {
    const { container } = render(<ExternalLink href={value}>EDGAR ↗</ExternalLink>);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toBe("EDGAR ↗");
  });

  it("renders no anchor for a missing URL", () => {
    const { container } = render(<ExternalLink href={null}>0001-26-000001</ExternalLink>);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toBe("0001-26-000001");
  });

  it("passes a click handler through", () => {
    let clicked = 0;
    render(
      <ExternalLink href={SEC} onClick={() => (clicked += 1)}>
        x
      </ExternalLink>,
    );
    screen.getByRole("link").click();
    expect(clicked).toBe(1);
  });
});
