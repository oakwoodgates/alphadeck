import { useLayoutEffect, useRef, useState } from "react";

/** Keep the rail mounted, but remove its covered controls from paint/focus/accessibility.
 * Read the actual boxes: at a narrow viewport the panel can leave a strip of the rail exposed.
 */
export function useCoveredRail(panelOpen: boolean) {
  const shellRef = useRef<HTMLDivElement>(null);
  const [covered, setCovered] = useState(false);
  useLayoutEffect(() => {
    const shell = shellRef.current;
    const rail = shell?.querySelector<HTMLElement>("#cp-rail");
    const panel = shell?.querySelector<HTMLElement>(".npanel");
    if (!panelOpen || !rail || !panel) { setCovered(false); return; }
    const measure = () => {
      const r = rail.getBoundingClientRect(), p = panel.getBoundingClientRect();
      const left = Math.max(0, r.left), right = Math.min(innerWidth, r.right);
      const top = Math.max(0, r.top), bottom = Math.min(innerHeight, r.bottom);
      setCovered(right > left && bottom > top && p.left <= left && p.right >= right && p.top <= top && p.bottom >= bottom);
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(rail);
    observer?.observe(panel);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [panelOpen]);
  return { shellRef, covered };
}
