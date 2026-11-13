// Guard (security hardening PR 4a, spec item 3): a link whose URL is an expression goes through ExternalLink.
//
// Scans every source file under src/ (tests excluded) and fails on an `<a` whose `href` is an expression, or a
// script assignment to an anchor's `.href`, anywhere outside components/ExternalLink.tsx — except the explicit
// allowlist below: the three static internal links and the two blob downloads.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

// file (relative to src/, forward slashes) -> the exact href expressions allowed there, each with its reason
const ALLOWED: Record<string, string[]> = {
  "admin/Admin.tsx": ["scoreboardPath(null)"], // a static internal route
  "board/Board.tsx": ["workbenchPath(null)"], // a static internal route
  "admin/PriceFeedRepairs.tsx": ["workbenchPath(null)"], // a static internal route
  "util/exportNames.ts": ["url", "url"], // the two same-origin blob: downloads (URL.createObjectURL)
};

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name === "__tests__" || name === "test") continue;
      out.push(...sources(full));
    } else if (/\.(tsx?|jsx?)$/.test(name) && !/\.test\.|\.d\.ts$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

/** The attribute text of each `<a ...>` opening tag: from `<a` to the first `>` outside any `{...}`. */
function anchorTags(text: string): string[] {
  const tags: string[] = [];
  for (const m of text.matchAll(/<a(?=[\s>])/g)) {
    let depth = 0;
    let j = m.index! + 2;
    for (; j < text.length; j++) {
      const c = text[j];
      if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (c === ">" && depth === 0) break;
    }
    tags.push(text.slice(m.index! + 2, j));
  }
  return tags;
}

/** Every `href={...}` on an `<a` element (JSX), and every `.href = ...` assignment, as its expression text. */
function hrefExpressions(text: string): string[] {
  const found: string[] = [];
  for (const attrs of anchorTags(text)) {
    const i = attrs.search(/\bhref=\{/);
    if (i < 0) continue;
    let depth = 0;
    let j = attrs.indexOf("{", i);
    const start = j + 1;
    for (; j < attrs.length; j++) {
      if (attrs[j] === "{") depth++;
      else if (attrs[j] === "}" && --depth === 0) break;
    }
    found.push(attrs.slice(start, j).trim());
  }
  for (const m of text.matchAll(/\.href\s*=(?!=)\s*([^;\n]+)/g)) found.push(m[1].trim());
  return found;
}

describe("data-fed links go through ExternalLink", () => {
  it("finds the anchors it is meant to find", () => {
    expect(hrefExpressions(`<a className="x" href={row.url} target="_blank">`)).toEqual(["row.url"]);
    expect(hrefExpressions(`<a\n  className="x"\n  href={f(a, {b: 1})}\n>`)).toEqual(["f(a, {b: 1})"]);
    expect(hrefExpressions(`<a onClick={(e) => e.stopPropagation()} href={s.url}>`)).toEqual(["s.url"]);
    expect(hrefExpressions(`<abbr title="x">`)).toEqual([]);
    expect(hrefExpressions(`<a href="/static">`)).toEqual([]);
    expect(hrefExpressions(`a.href = url;`)).toEqual(["url"]);
    expect(hrefExpressions(`if (a.href === b) {}`)).toEqual([]);
  });

  it("no source file outside ExternalLink.tsx renders a data-fed anchor", () => {
    const offenders: string[] = [];
    const seen: Record<string, string[]> = {};
    for (const file of sources(SRC)) {
      const rel = relative(SRC, file).split(sep).join("/");
      if (rel === "components/ExternalLink.tsx") continue;
      const exprs = hrefExpressions(readFileSync(file, "utf8"));
      if (!exprs.length) continue;
      seen[rel] = exprs;
      const allowed = [...(ALLOWED[rel] ?? [])];
      for (const e of exprs) {
        const k = allowed.indexOf(e);
        if (k >= 0) allowed.splice(k, 1);
        else offenders.push(`${rel}: href={${e}}`);
      }
    }
    expect(offenders).toEqual([]);
    // the allowlist stays exact: an entry that no longer matches anything is removed, not kept "just in case"
    expect(Object.keys(seen).sort()).toEqual(Object.keys(ALLOWED).sort());
  });
});
