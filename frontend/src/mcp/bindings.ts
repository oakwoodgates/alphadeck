import { useMemo } from "react";
import type { CaptureReference } from "./contract";
import { useCaptureView } from "./viewContext";

/** One captured response's references: `pointer` selects one exact JSON location; omitted, it takes every
 *  reference the backend advertised into that response (its adapter owns which pointers exist). */
export type SourceSelector = { path: string; pointer?: string };

/** Only backend-issued capabilities may become advertised target links or source references. */
export function useCaptureBindings() {
  const view = useCaptureView();
  // path -> its references in envelope order; built once per frozen view, so a page of targets stays cheap
  const byPath = useMemo(() => {
    const index = new Map<string, CaptureReference[]>();
    if (!view.readOnly) return index;
    for (const ref of view.references ?? []) {
      const path = view.responses?.[ref.response_index]?.path;
      if (path === undefined) continue;
      const refs = index.get(path) ?? [];
      refs.push(ref);
      index.set(path, refs);
    }
    return index;
  }, [view.readOnly, view.responses, view.references]);
  /** Every advertised reference the selectors name, in selector order then envelope order, each ID once. */
  const sourceRefsAt = (selectors: readonly SourceSelector[]) => {
    if (!view.readOnly) return {};
    const seen = new Set<string>();
    const refs = [];
    for (const { path, pointer } of selectors) {
      for (const ref of byPath.get(path) ?? []) {
        if ((pointer !== undefined && ref.pointer !== pointer) || seen.has(ref.id)) continue;
        seen.add(ref.id);
        refs.push({ id: ref.id, kind: ref.kind, label: ref.label ?? ref.category ?? "Captured source", ...(ref.category ? { category: ref.category } : {}) });
      }
    }
    return refs.length ? { "data-mcp-source-refs": JSON.stringify(refs) } : {};
  };
  const sourceRefs = (path: string, pointer: string) => sourceRefsAt([{ path, pointer }]);
  const cockpitLinks = (thesisId: string, selectedName: string | null = null) => {
    if (!view.readOnly) return {};
    const links = (view.navigation ?? []).filter((nav) => nav.destination.dashboard === "cockpit" &&
      nav.destination.settings.thesis_id === thesisId && (nav.destination.settings.selected_name ?? null) === selectedName)
      .map((nav) => ({ id: nav.id, kind: nav.kind, label: selectedName === null ? "Open Cockpit" : "Open this name in Cockpit" }));
    return links.length ? { "data-mcp-links": JSON.stringify(links) } : {};
  };
  return { sourceRefs, sourceRefsAt, cockpitLinks };
}
