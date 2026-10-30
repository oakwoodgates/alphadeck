import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { HotBoardOut } from "../useHotBoard";
import { ASOF, armedRead, payload, sample } from "./fixtures";

// The page's two reads against a REAL query client with only the HTTP client stubbed: the armed read on load, the
// full read once the first time a toggle is checked (never again for the same as-of: the rest is client side), and
// no full read at all for a page whose toggles stay off. The component's own rendering has its test with the hook
// stubbed (HotBoard.test.tsx).

type Query = { asof: string; rows: "armed" | "all" };
const h = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import HotBoard from "../HotBoard";

const OTHER = "2026-10-21";
const fulls = new Map<string, HotBoardOut>();
const fullOf = (asof: string) => {
  if (!fulls.has(asof)) fulls.set(asof, payload(Object.values(sample()), { asof }));
  return fulls.get(asof) as HotBoardOut;
};

/** Every request the page made, as "<as-of>:<rows>". */
const requests = () => h.get.mock.calls.map((c) => {
  const q = (c[1] as { params: { query: Query } }).params.query;
  return `${q.asof}:${q.rows}`;
});

beforeEach(() => {
  h.get.mockReset();
  fulls.clear();
  h.get.mockImplementation(async (path: string, opts: { params: { query: Query } }) => {
    expect(path).toBe("/dashboards/hot-board");
    const { asof, rows } = opts.params.query;
    return { data: rows === "all" ? fullOf(asof) : armedRead(fullOf(asof)), error: undefined };
  });
});

function mount(asof = ASOF) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (a: string) => (
    <QueryClientProvider client={qc}>
      <HotBoard asof={a} onSelectThesis={vi.fn()} />
    </QueryClientProvider>
  );
  const view = render(ui(asof));
  return { goTo: (a: string) => view.rerender(ui(a)) };
}

const tickers = () => screen.queryAllByTestId("hb-row").map((r) => r.querySelector(".hb-tkbtn")?.textContent);
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 30)); });

describe("HotBoard — the page's reads", () => {
  it("reads the armed rows on load, the full read once on the first toggle, and nothing more after", async () => {
    mount();
    await waitFor(() => expect(tickers()).toEqual(["AAA", "CCC", "BBB", "FLG"]));
    expect(requests()).toEqual([`${ASOF}:armed`]);
    // the toggles show their counts from the armed read, before any other row is loaded
    expect(screen.getByLabelText(/Warming names/).closest("label")).toHaveTextContent("+1");

    await userEvent.click(screen.getByLabelText(/Warming names/));
    await waitFor(() => expect(tickers()).toContain("WRM"));
    expect(requests()).toEqual([`${ASOF}:armed`, `${ASOF}:all`]);
    expect(screen.getByText(/read loaded: every candidate row/)).toBeInTheDocument();

    // every further toggle, filter and sort is client side: no new request
    await userEvent.click(screen.getByLabelText(/Watch names/));
    await userEvent.click(screen.getByLabelText(/Managing/));
    expect(tickers()).toHaveLength(7);
    // unchecking every toggle returns to the armed rows; checking one again reads from the cache
    await userEvent.click(screen.getByLabelText(/Warming names/));
    await userEvent.click(screen.getByLabelText(/Watch names/));
    await userEvent.click(screen.getByLabelText(/Managing/));
    expect(tickers()).toEqual(["AAA", "CCC", "BBB", "FLG"]);
    await userEvent.click(screen.getByLabelText(/Watch names/));
    expect(tickers()).toContain("WTC");
    await settle();
    expect(requests()).toEqual([`${ASOF}:armed`, `${ASOF}:all`]);
  });

  it("never reads the full rows for a page whose toggles stay off, even across as-ofs", async () => {
    const { goTo } = mount();
    await waitFor(() => expect(tickers()).toHaveLength(4));
    goTo(OTHER);
    await waitFor(() => expect(requests()).toContain(`${OTHER}:armed`));
    await settle();
    expect(requests()).toEqual([`${ASOF}:armed`, `${OTHER}:armed`]);
  });

  it("reads the full rows for the new as-of when a toggle is still checked there", async () => {
    const { goTo } = mount();
    await waitFor(() => expect(tickers()).toHaveLength(4));
    await userEvent.click(screen.getByLabelText(/Managing/));
    await waitFor(() => expect(tickers()).toContain("MNG"));
    goTo(OTHER);
    await waitFor(() => expect(requests()).toContain(`${OTHER}:all`));
    await waitFor(() => expect(screen.getByTestId("hb-count")).toHaveTextContent("5 rows"));
    expect(requests().sort()).toEqual([`${ASOF}:all`, `${ASOF}:armed`, `${OTHER}:all`, `${OTHER}:armed`].sort());
  });
});
