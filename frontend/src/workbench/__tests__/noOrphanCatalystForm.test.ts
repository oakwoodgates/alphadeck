import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// V2 W6d: the orphan "+ log a catalyst" form was never rendered and is gone. Nothing may import it again.
const NAME = ["Catalyst", "Fact", "Form"].join("");

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? walk(p) : /\.(ts|tsx)$/.test(f) ? [p] : [];
  });
}

describe("the removed orphan form", () => {
  it("is imported or mocked by nothing under src", () => {
    const hits = walk(join(process.cwd(), "src")).filter(
      (p) => !p.endsWith("noOrphanCatalystForm.test.ts") && readFileSync(p, "utf8").includes(NAME),
    );
    expect(hits).toEqual([]);
  });
});
