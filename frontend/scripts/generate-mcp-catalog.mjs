import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = new URL("../src/dashboards/", import.meta.url);
const catalog = readdirSync(root, { withFileTypes: true })
  .filter((d) => d.isDirectory() && !d.name.startsWith("__"))
  .map((d) => {
    const value = JSON.parse(readFileSync(new URL(`${d.name}/capability.json`, root), "utf8"));
    if (value.slug !== d.name) throw new Error(`Capability slug does not match ${d.name}`);
    return value;
  }).sort((a, b) => a.order - b.order || a.label.localeCompare(b.label, "en"));
const output = new URL("../../backend/agent_access/catalog.json", import.meta.url);
const content = `${JSON.stringify({ schema_version: "1", dashboards: catalog }, null, 2)}\n`;
if (process.argv.includes("--check")) {
  if (readFileSync(output, "utf8") !== content) throw new Error("MCP catalog drift: run npm run gen:mcp");
} else {
  writeFileSync(output, content);
  process.stdout.write(`Generated ${catalog.length} dashboard capabilities: ${fileURLToPath(output)}\n`);
}
