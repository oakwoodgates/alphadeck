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
  /** Optional header controls rendered beside the expand and close buttons. */
  actions?: ReactNode;
  /** When true and collapsed, omits the dimming backdrop and keeps focus on the underlying page
   *  (used by the Cockpit so basket rows remain clickable and arrow-key stepping keeps working).
   *  Expanding to full width switches to a modal dialog automatically. */
  nonModal?: boolean;
  children: ReactNode;
}

export function Drawer({ open, onClose, title, actions, nonModal = false, children }: Props) {
  // Expand-to-full-width is LOCAL to the drawer and resets each time it opens (no browser storage,
  // no leak across episodes). Default ≈ the Cockpit panel width; expanded near-full for a chart.
  const [expanded, setExpanded] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const isModal = !nonModal || expanded;

  // On open: reset width; in modal mode remember the opener and move focus into the panel so
  // closing returns focus (non-modal Cockpit mode leaves focus on the basket row for ↑/↓ stepping).
  useEffect(() => {
    if (!open) return;
    setExpanded(false);
    if (nonModal) return;
    openerRef.current = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => openerRef.current?.focus?.();
  }, [open, nonModal]);

  // Escape closes (only while open — no dangling global listener otherwise). Capture phase +
  // stopPropagation keeps underlying pages with their own Escape handler (e.g. MarketMap unzoom)
  // untouched when closing the drawer.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className={`drawer-overlay${isModal ? "" : " non-modal"}`}>
      {/* the backdrop dims the page behind and closes on click (never destroys — reversible) */}
      {isModal && <div className="drawer-backdrop" aria-hidden="true" onClick={onClose} />}
      <aside
        className={`drawer-panel${expanded ? " expanded" : ""}`}
        role={isModal ? "dialog" : "complementary"}
        aria-modal={isModal ? "true" : undefined}
        aria-labelledby={title != null ? titleId : undefined}
        aria-label={title == null ? "drawer" : undefined}
      >
        <div className="drawer-head">
          {title != null && (
            <div className="drawer-title" id={titleId}>
              {title}
            </div>
          )}
          <div className="drawer-controls">
            {actions}
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
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}
