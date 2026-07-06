import { useLayoutEffect, useRef } from "react";

interface Props {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  ariaLabel?: string;
  className?: string;
  maxRows?: number; // grow to fit, then scroll past this many rows (default 3)
}

/** A textarea that sizes to its content — one row when empty, growing to `maxRows`, then scrolling. A JS
 *  sizer (not the Chrome-only `field-sizing:content`) so it works in Safari too. On every value change it
 *  measures `scrollHeight` and caps the height at `maxRows` lines, toggling the scrollbar past the cap. */
export function AutoTextarea({
  value,
  onChange,
  placeholder,
  ariaLabel,
  className,
  maxRows = 3,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto"; // reset so scrollHeight reflects the content, not the prior height
    const cs = window.getComputedStyle(el);
    const line = parseFloat(cs.lineHeight) || 18; // "normal" → NaN → sane fallback (also covers jsdom)
    const chrome =
      parseFloat(cs.paddingTop) +
      parseFloat(cs.paddingBottom) +
      parseFloat(cs.borderTopWidth) +
      parseFloat(cs.borderBottomWidth);
    const max = line * maxRows + (chrome || 0);
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
    el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
  }, [value, maxRows]);

  return (
    <textarea
      ref={ref}
      className={className}
      rows={1}
      aria-label={ariaLabel}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
