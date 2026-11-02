import { useCaptureView } from "./viewContext";

/** Only backend-issued capabilities may become advertised target links or source references. */
export function useCaptureBindings() {
  const view = useCaptureView();
  const sourceRefs = (path: string, pointer: string) => {
    if (!view.readOnly) return {};
    const indices = new Set((view.responses ?? []).flatMap((response, index) => response.path === path ? [index] : []));
    const refs = (view.references ?? []).filter((ref) => indices.has(ref.response_index) && ref.pointer === pointer)
      .map((ref) => ({ id: ref.id, kind: ref.kind, label: ref.label ?? ref.category ?? "Captured source", ...(ref.category ? { category: ref.category } : {}) }));
    return refs.length ? { "data-mcp-source-refs": JSON.stringify(refs) } : {};
  };
  const cockpitLinks = (thesisId: string, selectedName: string | null = null) => {
    if (!view.readOnly) return {};
    const links = (view.navigation ?? []).filter((nav) => nav.destination.dashboard === "cockpit" &&
      nav.destination.settings.thesis_id === thesisId && (nav.destination.settings.selected_name ?? null) === selectedName)
      .map((nav) => ({ id: nav.id, kind: nav.kind, label: selectedName === null ? "Open Cockpit" : "Open this name in Cockpit" }));
    return links.length ? { "data-mcp-links": JSON.stringify(links) } : {};
  };
  return { sourceRefs, cockpitLinks };
}
