import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** What a wrapping strip shows when it is held to a fixed number of rows. */
export interface RowCap {
  /** Index of the first child that sits below the last allowed row. `Infinity` while everything fits
   *  (and before the first measurement), so `i >= firstClipped` is simply false for every child. */
  firstClipped: number;
  /** Pixel height that holds exactly the allowed rows; `null` when everything fits. */
  height: number | null;
}

const FITS: RowCap = { firstClipped: Infinity, height: null };

/** Where a wrapped flex strip breaks into rows, read off the laid-out children (`offsetTop` is relative to the
 *  strip, so the strip must be `position: relative`). A row is a distinct `offsetTop`; nothing is estimated
 *  from a font size or an item width. */
function measure(el: HTMLElement, maxRows: number): RowCap {
  const kids = Array.from(el.children) as HTMLElement[];
  const tops = [...new Set(kids.map((k) => k.offsetTop))].sort((a, b) => a - b);
  if (tops.length <= maxRows) return FITS;
  const firstClipped = kids.findIndex((k) => k.offsetTop >= tops[maxRows]);
  const height = Math.max(...kids.slice(0, firstClipped).map((k) => k.offsetTop + k.offsetHeight));
  return { firstClipped, height };
}

/** Hold a wrapping strip to `maxRows` rows without deciding in code how many items that is: every child stays
 *  laid out (the strip is clipped to `cap.height`, the caller marks children from `cap.firstClipped` on), so the
 *  next measurement still sees the real rows. Re-measured after every render (an item's text can change its
 *  width), on a strip resize, and when web fonts land. jsdom has no layout and no ResizeObserver: there it
 *  reports "everything fits", which is the honest answer for a strip that was never measured. */
export function useRowCap<T extends HTMLElement>(maxRows: number) {
  const ref = useRef<T>(null);
  const [cap, setCap] = useState<RowCap>(FITS);

  const run = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const next = measure(el, maxRows);
    setCap((prev) =>
      prev.firstClipped === next.firstClipped && prev.height === next.height ? prev : next,
    );
  }, [maxRows]);

  useLayoutEffect(run);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(run) : null;
    ro?.observe(el);
    let live = true;
    void document.fonts?.ready.then(() => live && run());
    return () => {
      live = false;
      ro?.disconnect();
    };
  }, [run]);

  return { ref, cap };
}
