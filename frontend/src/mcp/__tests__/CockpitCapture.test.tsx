import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ClockProvider, clockFromServer } from "../../clock";
import { Cockpit } from "../../cockpit/Cockpit";
import CockpitCapture from "../../cockpit/CockpitCapture";
import type { CapturedSnapshot } from "../contract";
import { observe } from "../observe";
import { CaptureViewContext } from "../viewContext";
import fixture from "./fixtures/cockpit.json";

const h = vi.hoisted(() => ({ responses: [] as { path: string; query: Record<string, string>; body: unknown }[], calls: [] as string[], write: vi.fn() }));
vi.mock("../../api/client", () => ({ api: {
  GET: async (pattern: string, options?: { params?: { path?: Record<string, string>; query?: Record<string, unknown> } }) => {
    const path = pattern.replace(/\{([^}]+)\}/g, (_token, key) => options?.params?.path?.[key] ?? "");
    const query = Object.fromEntries(Object.entries(options?.params?.query ?? {}).map(([key, value]) => [key, String(value)]));
    const response = h.responses.find((item) => item.path === path && JSON.stringify(item.query) === JSON.stringify(query));
    h.calls.push(path);
    if (!response) throw new Error("Uncaptured fixture request");
    return { data: structuredClone(response.body) };
  }, POST: h.write, PUT: h.write,
} }));

beforeEach(() => { h.calls = []; h.write.mockReset(); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function mount(overrides: Record<string, unknown> = {}, change?: (snapshot: CapturedSnapshot) => void, interactive = false) {
  const snapshot = structuredClone(fixture) as unknown as CapturedSnapshot;
  change?.(snapshot);
  const settings = { ...snapshot.settings, ...overrides };
  h.responses = snapshot.responses;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } });
  function Body() {
    const [selected, select] = useState((settings.selected_name as string | null) ?? null);
    return interactive ? <Cockpit thesisId={settings.thesis_id as string} asof={snapshot.asof} onAsofChange={() => undefined}
      selectedName={selected} onSelectName={select} railOpen onRailChange={() => undefined} /> : <CockpitCapture asof={snapshot.asof} />;
  }
  const result = render(<QueryClientProvider client={client}>
    <ClockProvider value={clockFromServer(snapshot.clock)}>
      <CaptureViewContext.Provider value={{ ...snapshot, settings, readOnly: !interactive }}>
        <Body />
      </CaptureViewContext.Provider>
    </ClockProvider>
  </QueryClientProvider>);
  return { ...result, client, snapshot };
}

function overlayLayout(width = 1440) {
  vi.stubGlobal("innerWidth", width);
  vi.stubGlobal("innerHeight", 1080);
  const panelWidth = width <= 880 ? Math.min(600, width * 0.92) : 600;
  let railTop = 80;
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function () {
    if (this.id === "cp-rail") return new DOMRect(width - 380, railTop, 380, 1080);
    if (this.classList.contains("npanel")) return new DOMRect(width - panelWidth, 0, panelWidth, 1080);
    if (this.closest("#cp-rail")) return new DOMRect(width - 368, 100, 350, 100);
    if (this.closest(".npanel")) return new DOMRect(width - panelWidth + 20, 100, panelWidth - 40, 100);
    return new DOMRect(0, 0, width, 1080);
  });
  return { setRailTop: (top: number) => { railTop = top; } };
}

async function ready(container: HTMLElement, client: QueryClient) {
  await waitFor(() => expect(container.querySelector('[data-mcp-ready="true"]')).not.toBeNull());
  await waitFor(() => expect(client.isFetching()).toBe(0));
}

describe("actual read-only Cockpit capture", () => {
  it("marks covered rail sources unavailable while preserving visible member source references", async () => {
    overlayLayout();
    const { container, client, snapshot } = mount({ selected_name: "22222222-2222-4222-8222-222222222222", rail_open: true });
    await ready(container, client);
    const rail = container.querySelector("#cp-rail")!;
    expect(rail).toHaveAttribute("aria-hidden", "true");
    expect(rail).toHaveStyle({ visibility: "hidden" });
    const ids = new Set([...rail.querySelectorAll<HTMLElement>("[data-mcp-id]")].map((element) => element.dataset.mcpId));
    const observed = observe(container, "Cockpit");
    const railSources = observed.targets.filter((target) => ids.has(target.id) && target.source_refs.length);
    expect(railSources.length).toBeGreaterThan(0);
    expect(railSources.every((target) => !target.visible && !target.in_viewport)).toBe(true);
    expect(railSources.some((target) => target.text.includes("example-ref-a"))).toBe(true);
    const memberRef = snapshot.references!.find((ref) => ref.pointer === "/armed_members/0/triggers/0/sources/0")!;
    const visibleSource = observed.targets.find((target) => target.source_refs.some((ref: { id: string }) => ref.id === memberRef.id));
    expect(visibleSource?.visible).toBe(true);
    expect(visibleSource?.in_viewport).toBe(true);
  });

  it("restores the normal UI rail when the operator closes the member panel", async () => {
    overlayLayout();
    const { container, client } = mount({ selected_name: "22222222-2222-4222-8222-222222222222" }, undefined, true);
    await ready(container, client);
    expect(container.querySelector("#cp-rail")).toHaveStyle({ visibility: "hidden" });
    await userEvent.click(container.querySelector<HTMLButtonElement>(".np-close")!);
    expect(container.querySelector(".npanel")).toBeNull();
    expect(container.querySelector("#cp-rail")).not.toHaveAttribute("aria-hidden");
    expect(container.querySelector("#cp-rail")).not.toHaveStyle({ visibility: "hidden" });
    expect(screen.getByRole("button", { name: "Act — log the fill" })).toBeVisible();
  });

  it("keeps a rail strip visible when a narrow panel does not fully cover it", async () => {
    overlayLayout(400);
    const { container, client } = mount({ selected_name: "22222222-2222-4222-8222-222222222222" });
    await ready(container, client);
    expect(container.querySelector("#cp-rail")).not.toHaveAttribute("aria-hidden");
    expect(container.querySelector("#cp-rail")).not.toHaveStyle({ visibility: "hidden" });
  });

  it("updates coverage when scrolling changes the rail's viewport bounds without a resize", async () => {
    const layout = overlayLayout();
    layout.setRailTop(1200);
    const { container, client } = mount({ selected_name: "22222222-2222-4222-8222-222222222222" });
    await ready(container, client);
    expect(container.querySelector("#cp-rail")).not.toHaveAttribute("aria-hidden");
    layout.setRailTop(80);
    fireEvent.scroll(window);
    expect(container.querySelector("#cp-rail")).toHaveAttribute("aria-hidden", "true");
  });
  it("renders both sources and computed detail, binds exact server refs and navigation, and disables writes/downloads", async () => {
    const { container, client, snapshot } = mount();
    await ready(container, client);
    expect(screen.getByRole("button", { name: "Act — log the fill" })).toBeDisabled();
    expect(screen.getAllByRole("button", { name: /export/i }).every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
    expect([...container.querySelectorAll(".spine-editor")]).toHaveLength(0);
    const observed = observe(container, "Cockpit");
    const source = observed.targets.find((target) => target.source_refs.some((ref: { id: string }) => ref.id === snapshot.references![2].id));
    expect(source?.text).toContain("example-ref-a");
    expect(source?.text).toContain("12.5");
    expect(source?.source_refs[0].category).toBe("call_input");
    expect(observed.targets.some((target) => target.text.includes("example-ref-b") && target.text.includes("observations"))).toBe(true);
    expect(observed.targets.some((target) => target.links.some((link: { id: string }) => link.id === snapshot.navigation![0].id))).toBe(true);
    expect(h.calls).toContain(`/theses/${snapshot.settings.thesis_id}/decisions`);
    expect(container.textContent).toContain("current captured records");
    await userEvent.click(screen.getByRole("button", { name: "Act — log the fill" }));
    expect(h.write).not.toHaveBeenCalled();
  });

  it("opens the requested member with exact armed-member source pointers while keeping its rail data mounted", async () => {
    const sid = "22222222-2222-4222-8222-222222222222";
    const { container, client, snapshot } = mount({ selected_name: sid, rail_open: false, group_mode: "segment", sort_col: "ticker", sort_dir: "asc" });
    await ready(container, client);
    expect(screen.getByLabelText("EXAMPLE — per-name panel")).toBeInTheDocument();
    const observed = observe(container, "Cockpit");
    const sourceRef = snapshot.references!.find((ref) => ref.pointer === "/armed_members/0/triggers/0/sources/1")!;
    const source = observed.targets.find((target) => target.source_refs.some((ref: { id: string }) => ref.id === sourceRef.id));
    expect(source?.text).toContain("example-ref-b");
    expect(container.querySelector("#cp-rail")).toHaveAttribute("hidden");
    expect(container.querySelector('[data-mcp-error="true"]')).toBeNull();
  });

  it("binds the displayed scored object when a security has distinct scores on multiple basket rows", async () => {
    const sid = "22222222-2222-4222-8222-222222222222";
    const lastMemberRef = "ref_aaaaaaaaaaaaaaaaaaaaaaaa";
    const lastSourceRef = "ref_bbbbbbbbbbbbbbbbbbbbbbbb";
    const firstMemberRef = "ref_cccccccccccccccccccccccc";
    const { container, client } = mount({ selected_name: sid }, (snapshot) => {
      const thesis = snapshot.responses[1].body as { basket: Record<string, unknown>[] };
      thesis.basket.push({ ...thesis.basket[0], segment: "Second example link" });
      const scored = snapshot.responses[5].body as { members: { name: string; segment?: string; purity: { provenance: { detail: Record<string, unknown> }[] } }[] };
      const last = structuredClone(scored.members[0]);
      last.name = "Example company on second link";
      last.segment = "Second example link";
      last.purity.provenance[0].detail = { example_segment_value: 88 };
      scored.members.push(last);
      snapshot.references!.push(
        { id: firstMemberRef, kind: "captured_json", response_index: 5, pointer: "/members/0", category: "scored_member" },
        { id: lastMemberRef, kind: "captured_json", response_index: 5, pointer: "/members/1", category: "scored_member" },
        { id: lastSourceRef, kind: "captured_json", response_index: 5, pointer: "/members/1/purity/provenance/0", category: "scored_provenance" },
      );
    });
    await ready(container, client);
    expect(container.querySelector(".np-co")?.textContent).toBe("Example company on second link");
    const observed = observe(container, "Cockpit");
    const panel = observed.targets.find((target) => target.source_refs.some((ref: { id: string }) => ref.id === lastMemberRef));
    expect(panel).toBeDefined();
    expect(panel?.source_refs.some((ref: { id: string }) => ref.id === firstMemberRef)).toBe(false);
    const source = observed.targets.find((target) => target.source_refs.some((ref: { id: string }) => ref.id === lastSourceRef));
    expect(source?.text).toContain('"example_segment_value": 88');
  });

  it.each(["unknown-name", "unavailable-segment"])("rejects unavailable requested state: %s", async (choice) => {
    const { container, client } = mount(choice === "unknown-name" ? { selected_name: "absent" } : { group_mode: "segment" }, (snapshot) => {
      if (choice === "unavailable-segment") {
        const thesis = snapshot.responses[1].body as { basket: { segment: string | null }[] };
        thesis.basket[0].segment = null;
      }
    });
    await ready(container, client);
    expect(container.querySelector('[data-mcp-error-code="invalid_view_state"]')).not.toBeNull();
  });
});
