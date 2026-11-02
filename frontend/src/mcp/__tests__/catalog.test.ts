import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import { DASHBOARDS } from "../../dashboards/registry";

describe("agent dashboard catalog", () => {
  it("retains the complete registry metadata and explicitly marks unsupported dashboards", () => {
    const catalog = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../../../../backend/agent_access/catalog.json"), "utf8"));
    expect(catalog.schema_version).toBe("1");
    expect(catalog.dashboards).toHaveLength(DASHBOARDS.length);
    for (const entry of DASHBOARDS) {
      const metadata = catalog.dashboards.find((d: { slug: string }) => d.slug === entry.slug);
      expect(metadata).toMatchObject({ label: entry.label, description: entry.description, asofAware: entry.asofAware, capabilities: entry.capabilities, response_paths: entry.response_paths });
      expect(metadata.settings_schema.additionalProperties).toBe(false);
      expect(metadata.response_paths.length > 0).toBe(metadata.capabilities.data);
    }
    expect(catalog.dashboards.filter((d: { capabilities: { observe: boolean } }) => d.capabilities.observe).map((d: { slug: string }) => d.slug)).toEqual(["board", "rotation-map", "narrative-index"]);
  });
});
