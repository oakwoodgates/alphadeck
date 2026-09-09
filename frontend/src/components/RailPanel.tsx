import { useState, type ReactNode } from "react";

/** A rail list section: its own bounded, independently-scrolling widget with a counted, collapsible
 *  header. The rail is 96% list by height (measured: 17,862px of rows against 503px of chrome), and
 *  those lists only grow — so a section is capped rather than allowed to run, and says how much it
 *  is holding. It CAPS, it never truncates: everything is still there, one scroll away, and the
 *  count is on the header whether the panel is open or closed (interaction principles #2 and #3 —
 *  the basket's bucket headers do the same thing with the same chev · label · count idiom).
 *
 *  It is deliberately NOT another bordered box: the section rides full-bleed with a hairline above
 *  it, so the rail keeps one border (the card's state accent) instead of a box inside a box. */
export function RailPanel({
  label,
  count,
  defaultOpen = true,
  children,
}: {
  label: string;
  count: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rpanel">
      <button
        type="button"
        className="rpanel-h"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {/* one glyph, rotated closed — the basket's bucket-header idiom */}
        <span className="chev" aria-hidden="true">
          ▾
        </span>
        <span className="lbl">{label}</span>
        <span className="ct">{count}</span>
      </button>
      {/* the rows stay MOUNTED-or-not by open state; a closed panel keeps its count, so a collapse
          never reads as a section that emptied out */}
      {open && <div className="rpanel-body">{children}</div>}
    </section>
  );
}
