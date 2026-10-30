import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The thumbnail rules (docs/DASHBOARDS.md, "Thumbnails"): every dashboard folder carries a code-drawn
// thumbnail.svg, shown on the /dashboards index. A drawing is shapes only, in the app's own colors, so it can
// never carry a ticker, a card name or a number and is always safe to commit to a public repo. Rule 1 (a
// thumbnail for every folder) is also the registry's: buildRegistry throws without one (registry.test.ts). The
// one rule no test can check is review's: draw what makes the page different, not a generic table.

const HERE = dirname(fileURLToPath(import.meta.url));
const DASHBOARDS_DIR = join(HERE, "..");
const INDEX_CSS = join(HERE, "..", "..", "index.css");

const ROOT_XMLNS = "http://www.w3.org/2000/svg";
const ROOT_VIEWBOX = "0 0 320 200";
const MAX_BYTES = 4096;
const MAX_ACCENTS = 3;

/** The tokens that draw structure. Every other color token in `:root` counts as an accent. */
const NEUTRAL_TOKENS = [
  "--bg",
  "--bg-2",
  "--panel",
  "--panel-2",
  "--line",
  "--line-2",
  "--txt",
  "--txt-2",
  "--txt-3",
  "--txt-4",
  "--incub",
] as const;

// Rule 3: no words, and rule 4: self-contained and flat. Compared lowercase (XML keeps the case).
const WORD_ELEMENTS = ["text", "tspan", "textpath", "title", "desc", "foreignobject"];
const FORBIDDEN_ELEMENTS = ["image", "script", "style", "filter", "lineargradient", "radialgradient", "pattern"];
// Rule 5: these attributes hold a color.
const COLOR_ATTRS = ["fill", "stroke", "stop-color"];

type Palette = {
  /** neutral hex (lowercase) -> its token name */
  neutral: Map<string, string>;
  /** accent hex (lowercase) -> its token name */
  accent: Map<string, string>;
};

/** The color tokens of the FIRST `:root` block of index.css: `--name: #hex;`. A non-color token (`--mono`, `--sans`)
 *  has no hex and is skipped. A token in NEUTRAL_TOKENS is neutral; every other one is an accent. */
function readPalette(css: string): Palette {
  const root = /:root\s*\{([^}]*)\}/.exec(css);
  if (!root) throw new Error("index.css has no :root block");
  const neutral = new Map<string, string>();
  const accent = new Map<string, string>();
  for (const m of root[1].matchAll(/(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    const [, name, hex] = m;
    ((NEUTRAL_TOKENS as readonly string[]).includes(name) ? neutral : accent).set(hex.toLowerCase(), name);
  }
  return { neutral, accent };
}

/** Every rule violation in one thumbnail's source, as readable lines; an empty list = the drawing follows the
 *  rules. Pure: the text and the palette in, the violations out. */
function checkThumbnail(svg: string, palette: Palette): string[] {
  const out: string[] = [];

  // rule 7: at most 4 KB
  const bytes = Buffer.byteLength(svg, "utf8");
  if (bytes > MAX_BYTES) out.push(`is ${bytes} bytes; the limit is ${MAX_BYTES}`);

  let doc: Document;
  try {
    doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  } catch (e) {
    return [...out, `is not well-formed XML (${(e as Error).message})`];
  }
  if (doc.getElementsByTagName("parsererror").length > 0) return [...out, "is not well-formed XML"];

  // rule 2: the root
  const root = doc.documentElement;
  if (root.localName !== "svg") out.push(`root is <${root.localName}>, not <svg>`);
  if (root.getAttribute("xmlns") !== ROOT_XMLNS) out.push(`root xmlns is not ${ROOT_XMLNS}`);
  if ((root.getAttribute("viewBox") ?? "").trim().replace(/\s+/g, " ") !== ROOT_VIEWBOX) {
    out.push(`root viewBox is "${root.getAttribute("viewBox")}", not "${ROOT_VIEWBOX}"`);
  }

  const allowed = new Set([...palette.neutral.keys(), ...palette.accent.keys()]);
  const accentsUsed = new Set<string>();

  // rule 3 (words in text nodes and comments), rule 4 (elements, attributes), rule 5 (colors)
  const walk = (node: Node): void => {
    if (node.nodeType === 8) out.push("has a comment (no words, not even in a comment)");
    if (node.nodeType === 3 && (node.nodeValue ?? "").trim() !== "") out.push("has text content (no words)");
    if (node.nodeType !== 1) return;
    const el = node as Element;
    const tag = el.localName.toLowerCase();
    if (WORD_ELEMENTS.includes(tag)) out.push(`has <${el.localName}> (no words)`);
    if (FORBIDDEN_ELEMENTS.includes(tag)) out.push(`has <${el.localName}> (not flat and self-contained)`);
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim();
      if (name === "style") out.push(`<${el.localName}> has a style attribute`);
      if (/^on/.test(name)) out.push(`<${el.localName}> has the ${attr.name} attribute`);
      if ((name === "href" || name === "xlink:href") && !/^#[\w-]+$/.test(value)) {
        out.push(`<${el.localName}> ${attr.name}="${value}" is not an in-file #id`);
      }
      for (const u of value.matchAll(/url\(\s*([^)]*?)\s*\)/gi)) {
        if (!/^#[\w-]+$/.test(u[1])) out.push(`<${el.localName}> ${attr.name} has url(${u[1]}), not url(#id)`);
      }
      if (COLOR_ATTRS.includes(name)) {
        const color = value.toLowerCase();
        if (color === "none") continue;
        if (!allowed.has(color)) out.push(`<${el.localName}> ${attr.name}="${value}" is not an app color`);
        else if (palette.accent.has(color)) accentsUsed.add(palette.accent.get(color) as string);
      }
    }
    for (const child of Array.from(el.childNodes)) walk(child);
  };
  walk(root);

  // rule 6: quiet, at most three accent tokens
  if (accentsUsed.size > MAX_ACCENTS) {
    out.push(`uses ${accentsUsed.size} accent colors (${[...accentsUsed].sort().join(", ")}); the limit is ${MAX_ACCENTS}`);
  }
  return [...new Set(out)];
}

const PALETTE = readPalette(readFileSync(INDEX_CSS, "utf8"));
const GOOD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200">';
const svg = (body: string, open = GOOD) => `${open}${body}</svg>`;
const hex = (token: string): string => {
  for (const m of [PALETTE.neutral, PALETTE.accent]) for (const [h, t] of m) if (t === token) return h;
  throw new Error(`no ${token} in index.css`);
};

describe("the palette the thumbnails are checked against", () => {
  it("reads every neutral token, and at least one accent, from the first :root block of index.css", () => {
    expect([...PALETTE.neutral.values()].sort()).toEqual([...NEUTRAL_TOKENS].sort());
    expect(PALETTE.accent.size).toBeGreaterThanOrEqual(1);
    // the font tokens are not colors
    expect([...PALETTE.accent.values()]).not.toContain("--mono");
    // a color is in one list only
    for (const h of PALETTE.neutral.keys()) expect(PALETTE.accent.has(h)).toBe(false);
  });

  it("reads only the first :root block", () => {
    const p = readPalette(":root { --bg: #000001; --warm: #000002; --mono: ui-monospace; } :root { --x: #000003; }");
    expect([...p.neutral.keys()]).toEqual(["#000001"]);
    expect([...p.accent.keys()]).toEqual(["#000002"]);
  });
});

describe("the thumbnail rules — checkThumbnail on small cases", () => {
  const line = hex("--line-2");
  const txt = hex("--txt-3");
  const accents = [...PALETTE.accent.keys()];

  it("accepts a quiet drawing in the app's colors (any case, none, opacities)", () => {
    const body =
      `<rect x="10" y="10" width="40" height="20" rx="3" fill="none" stroke="${line.toUpperCase()}"/>` +
      `<path d="M10 40h60" stroke="${txt}" fill="none" opacity=".5" stroke-opacity=".6" fill-opacity=".4"/>` +
      `<g fill="${accents[0]}"><circle cx="5" cy="5" r="2"/></g>`;
    expect(checkThumbnail(svg(body), PALETTE)).toEqual([]);
  });

  it("accepts an in-file #id reference and a url(#id)", () => {
    const body = `<g id="a"><rect width="2" height="2" fill="${txt}"/></g><use href="#a" x="9"/><rect width="1" height="1" fill="none" clip-path="url(#a)"/>`;
    expect(checkThumbnail(svg(body), PALETTE)).toEqual([]);
  });

  it("refuses words: <text> and its kin", () => {
    for (const tag of ["text", "tspan", "textPath", "title", "desc", "foreignObject"]) {
      expect(checkThumbnail(svg(`<${tag}/>`), PALETTE), tag).toEqual([`has <${tag}> (no words)`]);
    }
    expect(checkThumbnail(svg(`<g>word</g>`), PALETTE)).toEqual(["has text content (no words)"]);
    expect(checkThumbnail(svg(`<!-- a name --><g/>`), PALETTE)).toEqual(["has a comment (no words, not even in a comment)"]);
  });

  it("refuses a color that is not an app token", () => {
    expect(checkThumbnail(svg(`<rect fill="#123456"/>`), PALETTE)).toEqual(['<rect> fill="#123456" is not an app color']);
    expect(checkThumbnail(svg(`<rect stroke="red"/>`), PALETTE)).toEqual(['<rect> stroke="red" is not an app color']);
    expect(checkThumbnail(svg(`<rect fill="currentColor"/>`), PALETTE)).toEqual([
      '<rect> fill="currentColor" is not an app color',
    ]);
  });

  it("refuses a fourth accent, and counts a repeated accent once", () => {
    const four = accents.slice(0, 4).map((a, i) => `<rect x="${i}" fill="${a}"/>`).join("");
    const out = checkThumbnail(svg(four), PALETTE);
    expect(out).toHaveLength(1);
    expect(out[0]).toContain("uses 4 accent colors");
    const three = accents.slice(0, 3).map((a, i) => `<rect x="${i}" fill="${a}"/><rect x="${i}" stroke="${a}"/>`).join("");
    expect(checkThumbnail(svg(three), PALETTE)).toEqual([]);
  });

  it("refuses a style attribute, a style element and an on* attribute", () => {
    expect(checkThumbnail(svg(`<rect style="fill:red"/>`), PALETTE)).toEqual(["<rect> has a style attribute"]);
    expect(checkThumbnail(svg(`<style/>`), PALETTE)).toEqual(["has <style> (not flat and self-contained)"]);
    expect(checkThumbnail(svg(`<rect onclick="x()"/>`), PALETTE)).toEqual(["<rect> has the onclick attribute"]);
  });

  it("refuses a root that is not the 320 x 200 svg", () => {
    expect(checkThumbnail(svg("", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'), PALETTE)).toEqual([
      'root viewBox is "0 0 100 100", not "0 0 320 200"',
    ]);
    expect(checkThumbnail(svg("", '<svg viewBox="0 0 320 200">'), PALETTE)).toEqual([
      "root xmlns is not http://www.w3.org/2000/svg",
    ]);
    expect(checkThumbnail(`<svg xmlns="http://www.w3.org/2000/svg">`, PALETTE)).toEqual(["is not well-formed XML"]);
  });

  it("refuses an image, a script, a filter, a gradient and a pattern", () => {
    for (const tag of ["image", "script", "filter", "linearGradient", "radialGradient", "pattern"]) {
      expect(checkThumbnail(svg(`<${tag}/>`), PALETTE), tag).toEqual([`has <${tag}> (not flat and self-contained)`]);
    }
  });

  it("refuses an outside link and an outside url()", () => {
    expect(checkThumbnail(svg(`<use href="other.svg#a"/>`), PALETTE)).toEqual([
      '<use> href="other.svg#a" is not an in-file #id',
    ]);
    expect(checkThumbnail(svg(`<use xlink:href="https://x.test/a.svg" xmlns:xlink="http://www.w3.org/1999/xlink"/>`), PALETTE)).toEqual([
      '<use> xlink:href="https://x.test/a.svg" is not an in-file #id',
    ]);
    expect(checkThumbnail(svg(`<rect clip-path="url(https://x.test/a.svg#c)"/>`), PALETTE)).toEqual([
      "<rect> clip-path has url(https://x.test/a.svg#c), not url(#id)",
    ]);
  });

  it("refuses a drawing over 4 KB", () => {
    const big = svg(`<path d="M0 0${"h1v1".repeat(1100)}" fill="none" stroke="${txt}"/>`);
    const out = checkThumbnail(big, PALETTE);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatch(/bytes; the limit is 4096$/);
  });
});

describe("the thumbnail rules — every dashboard's thumbnail.svg", () => {
  const folders = readdirSync(DASHBOARDS_DIR, { withFileTypes: true })
    .filter((d) => d.isDirectory() && !d.name.startsWith("__"))
    .map((d) => d.name)
    .sort();

  it("finds the dashboard folders", () => {
    expect(folders.length).toBeGreaterThan(0);
  });

  it.each(folders)("%s/thumbnail.svg exists and follows the rules", (folder) => {
    const file = join(DASHBOARDS_DIR, folder, "thumbnail.svg");
    expect(existsSync(file), `${folder}/thumbnail.svg is missing`).toBe(true);
    const problems = checkThumbnail(readFileSync(file, "utf8"), PALETTE);
    expect(problems, `${folder}/thumbnail.svg`).toEqual([]);
  });
});
