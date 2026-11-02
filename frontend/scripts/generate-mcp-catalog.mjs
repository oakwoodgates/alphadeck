import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = new URL("../src/dashboards/", import.meta.url);
const catalog = readdirSync(root, { withFileTypes: true })
  .filter((d) => d.isDirectory() && !d.name.startsWith("__"))
  .map((d) => {
    const value = JSON.parse(readFileSync(new URL(`${d.name}/capability.json`, root), "utf8"));
    if (value.slug !== d.name) throw new Error(`Capability slug does not match ${d.name}`);
    return value;
  });
const sourceRoot = new URL("../src/", import.meta.url);
for (const folder of readdirSync(sourceRoot, { withFileTypes: true }).filter((item) => item.isDirectory())) {
  const entryPath = new URL(`${folder.name}/captureEntry.ts`, sourceRoot);
  const metadataPath = new URL(`${folder.name}/capability.json`, sourceRoot);
  const metadata = existsSync(metadataPath) ? JSON.parse(readFileSync(metadataPath, "utf8")) : null;
  if (existsSync(entryPath) || metadata?.surface_kind === "detail") {
    if (!existsSync(entryPath) || !metadata || metadata.surface_kind !== "detail" || metadata.slug !== folder.name) throw new Error(`Incomplete detail capture registration: ${folder.name}`);
    catalog.push(metadata);
  }
}
if (new Set(catalog.map((item) => item.slug)).size !== catalog.length) throw new Error("Duplicate capture capability slug");
catalog.sort((a, b) => a.order - b.order || a.label.localeCompare(b.label, "en"));
const output = new URL("../../backend/agent_access/catalog.json", import.meta.url);
const content = `${JSON.stringify({ schema_version: "1", dashboards: catalog }, null, 2)}\n`;
if (process.argv.includes("--check")) {
  if (readFileSync(output, "utf8") !== content) throw new Error("MCP catalog drift: run npm run gen:mcp");
} else {
  writeFileSync(output, content);
  process.stdout.write(`Generated ${catalog.length} dashboard capabilities: ${fileURLToPath(output)}\n`);
}
