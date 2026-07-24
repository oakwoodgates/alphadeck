import { useEffect, useId, useRef, useState, type ReactNode } from "react";

// A generic, content-agnostic slide-out overlay — a reusable spine (the Scoreboard episode
// scorecard is its first tenant; a future full-screen chart mounts into the same shell). Modeled
// on the Cockpit NamePanel's sibling-overlay idiom, but MODAL: a dimming backdrop + a right-side
// panel that slides in. Reversible by construction (workbench principle #1): the ✕, the backdrop,
// and Escape all close, and closing never touches what's underneath — the caller owns the content
// and its state, the Drawer only frames it.

interface Props {
  open: boolean;
  onClose: () => void;
  /** Rendered in the header; when a string, also names the dialog for a11y. */
  title?: ReactNode;
  children: ReactNode;
}

export function Drawer({ open, onClose, title, children }: Props) {
  // Expand-to-full-width is LOCAL to the drawer and resets each time it opens (no browser storage,
  // no leak across episodes). Default ≈ the Cockpit panel width; expanded near-full for a chart.
  const [expanded, setExpanded] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const titleId = useId();

  // On open: reset width, remember the opener, move focus into the panel. On close: return focus to
  // the opener (attention is reversible too). Keyed on `open` so a parent re-render never re-steals.
  useEffect(() => {
    if (!open) return;
    setExpanded(false);
    openerRef.current = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => openerRef.current?.focus?.();
  }, [open]);

  // Escape closes (only while open — no dangling global listener otherwise).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="drawer-overlay">
      {/* the backdrop dims the page behind and closes on click (never destroys — reversible) */}
      <div className="drawer-backdrop" aria-hidden="true" onClick={onClose} />
      <aside
        className={`drawer-panel${expanded ? " expanded" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title != null ? titleId : undefined}
        aria-label={title == null ? "drawer" : undefined}
      >
        <div className="drawer-head">
          {title != null && (
            <div className="drawer-title" id={titleId}>
              {title}
            </div>
          )}
          <button
            type="button"
            className="drawer-expand"
            aria-label={expanded ? "collapse drawer" : "expand drawer to full width"}
            aria-pressed={expanded}
            title={expanded ? "collapse" : "expand"}
            onClick={() => setExpanded((v) => !v)}
          >
            ⤢
          </button>
          <button
            ref={closeRef}
            type="button"
            className="drawer-close"
            aria-label="close drawer"
            title="close (Esc)"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}
