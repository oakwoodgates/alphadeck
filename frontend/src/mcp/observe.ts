const TEXT_LIMIT = 12_000;
const ROW_LIMIT = 500;
const TARGET_LIMIT = 2_000;
const ANNOTATION_LIMIT = 1_000;
const CHART_LIMIT = 200;
const NOTE_LIMIT = 500;
const words = (value: string | null | undefined) => (value ?? "").replace(/\s+/g, " ").trim();
const text = (el: Element) => words(el.textContent);
const bound = (value: string) => ({ text: value.slice(0, TEXT_LIMIT), truncated: value.length > TEXT_LIMIT, total_characters: value.length });

export class DuplicateTargetError extends Error {
  constructor() { super("Duplicate capture target identifier."); }
}

function geometry(el: Element) {
  const rect = el.getBoundingClientRect();
  let visible = rect.width > 0 && rect.height > 0;
  let clipped = rect.top < 0 || rect.left < 0 || rect.bottom > innerHeight || rect.right > innerWidth;
  for (const node of [el, ...el.querySelectorAll("*")]) {
    if (!(node instanceof HTMLElement)) continue;
    const style = getComputedStyle(node);
    if (/hidden|clip|auto|scroll/.test(`${style.overflow} ${style.overflowX} ${style.overflowY}`) &&
      (node.scrollHeight > node.clientHeight + 1 || node.scrollWidth > node.clientWidth + 1)) clipped = true;
  }
  for (let node: Element | null = el; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") visible = false;
    if (node instanceof HTMLDetailsElement && !node.open && !node.querySelector("summary")?.contains(el) && node !== el) visible = false;
    if (node !== el && /hidden|auto|scroll|clip/.test(`${style.overflow} ${style.overflowX} ${style.overflowY}`)) {
      const box = node.getBoundingClientRect();
      if (rect.bottom > box.bottom + 1 || rect.top < box.top - 1 || rect.right > box.right + 1 || rect.left < box.left - 1) clipped = true;
      if (rect.bottom <= box.top || rect.top >= box.bottom || rect.right <= box.left || rect.left >= box.right) visible = false;
    }
  }
  return { visible, clipped, in_viewport: visible && rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth,
    bounds: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } };
}

function tables(el: Element) {
  if (el.matches("tr")) {
    return [{ headers: [...(el.closest("table")?.querySelectorAll("thead th") ?? [])].map(text),
      rows: [[...el.querySelectorAll("td")].map(text)], row_count: 1, rows_truncated: false }];
  }
  return [...el.querySelectorAll("table")].map((table) => {
    const rows = [...table.querySelectorAll("tbody tr")];
    return {
      headers: [...table.querySelectorAll("thead th")].map(text),
      rows: rows.slice(0, ROW_LIMIT).map((row) => [...row.querySelectorAll("td")].map(text)),
      row_count: rows.length, rows_truncated: rows.length > ROW_LIMIT,
    };
  });
}

/** Read the React-rendered words and geometry, so Python never reimplements presentation rules. */
export function observe(root: HTMLElement, title: string) {
  const bounded = bound(root.innerText ?? text(root));
  const elements = [...root.querySelectorAll<HTMLElement>("[data-mcp-id]")];
  const ids = elements.map((el) => el.dataset.mcpId);
  if (new Set(ids).size !== ids.length) throw new DuplicateTargetError();
  const targets = elements.slice(0, TARGET_LIMIT).map((el) => {
    const presentation = geometry(el);
    return {
    id: el.dataset.mcpId!, label: el.getAttribute("aria-label") ?? text(el.querySelector(".nm,.rm-label,.ni-name") ?? el).slice(0, 200),
    ...bound(presentation.visible ? (el.innerText ?? text(el)) : text(el)), ...presentation, tables: tables(el),
    links: JSON.parse(el.dataset.mcpLinks ?? "[]"), source_refs: JSON.parse(el.dataset.mcpSourceRefs ?? "[]"),
    reference_links: [...el.querySelectorAll<HTMLAnchorElement>("a[href]")].slice(0, 100).map((link) => ({ label: text(link), url: link.getAttribute("href") })),
    reference_link_count: el.querySelectorAll("a[href]").length,
    reference_links_truncated: el.querySelectorAll("a[href]").length > 100,
    };
  });
  const notes = [...root.querySelectorAll(".dash-note,.ni-note,.board-recompute,.dq-empty,[role=alert]")];
  const annotations = [...root.querySelectorAll("[title],[aria-label]")];
  const charts = [...root.querySelectorAll("svg[role=img]")];
  const referenceLinks = [...root.querySelectorAll<HTMLAnchorElement>("a[href]")];
  return {
    observation: {
      title, ...bounded, tables: tables(root),
      text_semantics: "Browser innerText; CSS-clipped text may remain in the string. Targets report visibility and clipping, and annotations preserve hidden context.",
      notes: notes.slice(0, NOTE_LIMIT).map((el) => bound(text(el)).text),
      note_count: notes.length, notes_truncated: notes.length > NOTE_LIMIT || notes.some((el) => text(el).length > TEXT_LIMIT),
      annotations: annotations.slice(0, ANNOTATION_LIMIT).map((el) => ({
        text: text(el).slice(0, 500), title: el.getAttribute("title"), label: el.getAttribute("aria-label"), ...geometry(el),
      })),
      annotation_count: annotations.length, annotations_truncated: annotations.length > ANNOTATION_LIMIT,
      charts: charts.slice(0, CHART_LIMIT).map((el) => ({
        label: el.getAttribute("aria-label"), text: text(el),
        paths: [...el.querySelectorAll("path")].map((path) => ({ class: path.getAttribute("class"), d: path.getAttribute("d") })),
        ...geometry(el),
      })),
      chart_count: charts.length, charts_truncated: charts.length > CHART_LIMIT,
      reference_links: referenceLinks.slice(0, ANNOTATION_LIMIT).map((link) => ({ label: text(link), url: link.getAttribute("href"), ...geometry(link) })),
      reference_link_count: referenceLinks.length, reference_links_truncated: referenceLinks.length > ANNOTATION_LIMIT,
      controls: [...root.querySelectorAll("input,select,button[aria-pressed],details")].map((el) => ({
        label: el.getAttribute("aria-label") ?? text(el.closest("label") ?? el).slice(0, 200),
        value: el instanceof HTMLInputElement || el instanceof HTMLSelectElement ? el.value : null,
        checked: el instanceof HTMLInputElement && el.type === "checkbox" ? el.checked : null,
        pressed: el.getAttribute("aria-pressed"), open: el instanceof HTMLDetailsElement ? el.open : null,
      })),
      target_count: elements.length, targets_truncated: elements.length > TARGET_LIMIT,
      fonts: { status: document.fonts?.status ?? "unavailable", family: getComputedStyle(root).fontFamily },
    }, targets,
  };
}
