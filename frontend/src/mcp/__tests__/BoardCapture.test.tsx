import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Board } from "../../board/Board";
import { ClockProvider, clockFromServer } from "../../clock";
import type { CaptureNavigation, CaptureReference, FrozenResponse } from "../contract";
import { observe } from "../observe";
import { targetId } from "../targetId";
import { CaptureViewContext } from "../viewContext";

// The Board's evidence references (docs/MCP.md, "Add a dashboard" step 5): the backend advertises, per active
// card, its /theses row, its call's decision fields and every /data-flags entry that holds it; the actual React
// Board binds those exact IDs to the card's Decision Queue item AND its lifecycle-column card. Synthetic data.
const ASOF = "2026-10-30";
const CLOCK = { current_night: ASOF, market_today: "2026-10-31", action_day: "2026-11-02", market_tz: "America/New_York" };
const A = "11111111-1111-4111-8111-111111111111"; // armed: in the queue and the Armed column
const B = "22222222-2222-4222-8222-222222222222"; // archived: no call, no references
const C = "33333333-3333-4333-8333-333333333333"; // warming: a column card only
const FIELDS = ["state", "verdict", "conviction_grade", "confirmation_grade", "entry_grade", "armed_members", "missing", "counter_case", "risk_signals", "arm_until", "exit_by"];

const call = (thesisId: string, state: string) => ({
  thesis_id: thesisId, asof: ASOF, state, verdict: state === "armed" ? "starter_entry" : "not_yet",
  conviction_grade: state === "armed" ? "core" : null, confirmation_grade: null, entry_grade: null,
  armed_security_id: null, expression: "Synthetic expression", exit_by: null, arm_until: state === "armed" ? "2026-11-06" : null,
  catalyst_surface: [], confidence: null, key_conviction: { turned: state === "armed", detail: "" },
  key_confirmation: { turned: state === "armed", detail: "" }, triggers_fired: [], risk_signals: [], missing: ["Synthetic condition"],
  counter_case: "Synthetic counter-case", safe_sleeve: null,
  armed_members: state === "armed" ? [{ security_id: "s-syna", ticker: "SYNA", triggers: [] }] : [], watch_members: [],
});
const member = (sid: string, ticker: string, holders: string[]) => ({
  security_id: sid, ticker, marked: false, flags: [], splits: [],
  notes: [{ kind: "short_price_history", label: "short price history", reason: "Synthetic note." }],
  theses: holders.map((id) => ({ id, name: "Synthetic holder" })),
});
const thesis = (id: string, name: string, archived: boolean) => ({ id, name, ticker: null, basket_size: 3, narrative: "Synthetic.", archived });

const responses: FrozenResponse[] = [
  { method: "GET", path: "/clock", query: {}, status: 200, body: CLOCK },
  { method: "GET", path: "/theses", query: { include_archived: "true" }, status: 200,
    body: [thesis(A, "Synthetic Alpha", false), thesis(B, "Synthetic Beta", true), thesis(C, "Synthetic Gamma", false)] },
  { method: "GET", path: `/theses/${A}/call`, query: { asof: ASOF }, status: 200, body: call(A, "armed") },
  { method: "GET", path: `/theses/${C}/call`, query: { asof: ASOF }, status: 200, body: call(C, "warming") },
  { method: "GET", path: "/data-flags", query: { asof: ASOF }, status: 200, body: { thesis_id: null, asof: ASOF, names: 4, members: [
    member("s-one", "SYNO", [A]), member("s-two", "SYNT", [A, C]), member("s-three", "SYNH", [C]),
  ] } },
];
// What the backend adapter advertises (capture_adapters/board.py): opaque IDs, exact pointers. A flag entry two
// cards hold is ONE reference that both cards' targets carry.
const ref = (id: string, index: number, pointer: string, category: string, label: string): CaptureReference =>
  ({ id, kind: "captured_json", response_index: index, pointer, category, label });
const references: CaptureReference[] = [
  ref("ref_row_a", 1, "/0", "thesis_summary", "theses[0]"),
  ...FIELDS.map((field) => ref(`ref_a_${field}`, 2, `/${field}`, "call_result", field)),
  ref("ref_flag_0", 4, "/members/0", "data_flag", "SYNO"),
  ref("ref_flag_1", 4, "/members/1", "data_flag", "SYNT"),
  ref("ref_row_c", 1, "/2", "thesis_summary", "theses[2]"),
  ...FIELDS.map((field) => ref(`ref_c_${field}`, 3, `/${field}`, "call_result", field)),
  ref("ref_flag_2", 4, "/members/2", "data_flag", "SYNH"),
];
const navigation: CaptureNavigation[] = [
  { id: "nav_a", kind: "fresh_capture", destination: { dashboard: "cockpit", settings: { thesis_id: A, rail_open: true } } },
  { id: "nav_a_name", kind: "fresh_capture", destination: { dashboard: "cockpit", settings: { thesis_id: A, selected_name: "s-syna", rail_open: true } } },
  { id: "nav_c", kind: "fresh_capture", destination: { dashboard: "cockpit", settings: { thesis_id: C, rail_open: true } } },
];

vi.mock("../../api/client", () => ({ api: {
  GET: async (pattern: string, options?: { params?: { path?: Record<string, string>; query?: Record<string, unknown> } }) => {
    const path = pattern.replace(/\{([^}]+)\}/g, (_token, key) => options?.params?.path?.[key] ?? "");
    const query = Object.fromEntries(Object.entries(options?.params?.query ?? {}).map(([key, value]) => [key, String(value)]));
    const response = responses.find((item) => item.path === path && JSON.stringify(item.query) === JSON.stringify(query));
    if (!response) throw new Error(`Uncaptured fixture request ${path}`);
    return { data: structuredClone(response.body) };
  },
} }));

afterEach(() => { vi.restoreAllMocks(); });

async function mount(readOnly: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } });
  const result = render(<QueryClientProvider client={client}>
    <ClockProvider value={clockFromServer(CLOCK)}>
      <CaptureViewContext.Provider value={{ settings: {}, readOnly, responses, navigation, references }}>
        <Board asof={ASOF} onSelect={() => undefined} />
      </CaptureViewContext.Provider>
    </ClockProvider>
  </QueryClientProvider>);
  await waitFor(() => expect(result.container.querySelector('[data-mcp-ready="true"]')).not.toBeNull());
  await waitFor(() => expect(client.isFetching()).toBe(0));
  return result.container;
}

const expected = (thesisId: string) => {
  const rowId = thesisId === A ? "ref_row_a" : "ref_row_c";
  const callIds = FIELDS.map((field) => `ref_${thesisId === A ? "a" : "c"}_${field}`);
  const flagIds = thesisId === A ? ["ref_flag_0", "ref_flag_1"] : ["ref_flag_1", "ref_flag_2"];
  return [rowId, ...callIds, ...flagIds];
};

describe("the Board's captured evidence references", () => {
  it("binds each card's advertised IDs to its queue item and its lifecycle card, links unchanged", async () => {
    const container = await mount(true);
    const targets = new Map(observe(container, "Board").targets.map((target) => [target.id, target]));
    const queue = targets.get(targetId("queue", A))!;
    const cardA = targets.get(targetId("card", A))!;
    const cardC = targets.get(targetId("card", C))!;
    expect(queue && cardA && cardC).toBeTruthy();
    expect(targets.has(targetId("queue", C))).toBe(false); // warming: not in the queue

    for (const target of [queue, cardA]) expect(target.source_refs.map((r: { id: string }) => r.id)).toEqual(expected(A));
    expect(cardC.source_refs.map((r: { id: string }) => r.id)).toEqual(expected(C));
    // every advertised entry carries the backend's kind, label and category, nothing invented
    const byId = new Map(references.map((r) => [r.id, r]));
    for (const target of [queue, cardA, cardC]) {
      for (const entry of target.source_refs as { id: string; kind: string; label: string; category: string }[]) {
        const source = byId.get(entry.id)!;
        expect(entry).toEqual({ id: source.id, kind: "captured_json", label: source.label, category: source.category });
      }
    }
    // the shared flag entry (held by both cards) is the SAME reference on both
    expect(cardA.source_refs.some((r: { id: string }) => r.id === "ref_flag_1")).toBe(true);
    expect(cardC.source_refs.some((r: { id: string }) => r.id === "ref_flag_1")).toBe(true);
    // the Cockpit navigation links are unchanged
    expect(queue.links.map((l: { id: string }) => l.id)).toEqual(["nav_a_name"]);
    expect(cardA.links.map((l: { id: string }) => l.id)).toEqual(["nav_a"]);
    expect(cardC.links.map((l: { id: string }) => l.id)).toEqual(["nav_c"]);
  });

  it("advertises nothing on the interactive Board", async () => {
    const container = await mount(false);
    expect(container.querySelector("[data-mcp-source-refs]")).toBeNull();
    expect(container.querySelector("[data-mcp-links]")).toBeNull();
  });
});
