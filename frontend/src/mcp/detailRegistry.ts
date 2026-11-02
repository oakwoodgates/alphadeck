/// <reference types="vite/client" />
import type { ComponentType, LazyExoticComponent } from "react";

export type DetailCapability = {
  slug: string; label: string; description: string; surface_kind: string;
  capabilities: { observe: boolean };
};
export type DetailCaptureEntry = {
  capability: DetailCapability;
  component: LazyExoticComponent<ComponentType<{ asof: string }>>;
};

/** Detail surfaces register beside their domain component, independently of the dashboard menu. */
export function buildDetailRegistry(modules: Record<string, unknown>, metadata: Record<string, unknown>): DetailCaptureEntry[] {
  const entries: DetailCaptureEntry[] = [];
  const seen = new Set<string>();
  for (const [path, module] of Object.entries(modules)) {
    const folder = path.split("/").at(-2)!;
    const entry = (module as { captureEntry?: DetailCaptureEntry })?.captureEntry;
    const capabilityPath = path.replace(/captureEntry\.ts$/, "capability.json");
    const capability = metadata[capabilityPath] as DetailCapability | undefined;
    if (!entry || !capability) throw new Error(`Detail capture registry: ${folder} requires captureEntry and capability.json`);
    if (entry.capability.slug !== folder || capability.surface_kind !== "detail" || JSON.stringify(entry.capability) !== JSON.stringify(capability)) {
      throw new Error(`Detail capture registry: ${folder} has mismatched capability metadata`);
    }
    if (seen.has(capability.slug)) throw new Error(`Detail capture registry: duplicate slug ${capability.slug}`);
    seen.add(capability.slug);
    entries.push(entry);
  }
  for (const [path, capability] of Object.entries(metadata)) {
    if ((capability as DetailCapability).surface_kind === "detail" && !seen.has((capability as DetailCapability).slug)) {
      throw new Error(`Detail capture registry: ${path} has no captureEntry.ts`);
    }
  }
  return entries.sort((a, b) => a.capability.slug.localeCompare(b.capability.slug, "en"));
}

export const DETAIL_CAPTURES = buildDetailRegistry(
  import.meta.glob("../*/captureEntry.ts", { eager: true }),
  import.meta.glob("../*/capability.json", { eager: true, import: "default" }),
);
