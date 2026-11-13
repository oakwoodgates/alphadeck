// The ONE component for a link whose URL comes from data (security hardening PR 4a).
//
// - It renders an anchor only for a URL that passes the same rule as the backend's domain/links.py: an http or
//   https URL with a host, no user:password, no whitespace or control character, at most 2,000 characters.
//   Anything else renders its children as plain text (the label stays visible; nothing is a link).
// - It always opens the link in a new tab with rel="noopener noreferrer".
// - It says where the link goes. A link to sec.gov (the host is sec.gov or ends with .sec.gov) renders exactly
//   as the call site always did. A link anywhere else shows that site's host and an external mark next to its
//   label, with the full URL in its title (operator decision 2026-11-12: a user-cited source may point at any
//   https host, but it never passes for an SEC filing).
//
// A call site that labels a link "EDGAR" picks that label with isSecLink(url), so "EDGAR" is only ever shown on
// a link that really goes to SEC. src/components/__tests__/externalLinkGuard.test.ts fails on any other anchor
// whose href is an expression.
import type { MouseEventHandler, ReactNode } from "react";
import "./external-link.css";

export const MAX_URL_LENGTH = 2000;

export type LinkDestination = "sec" | "external";

// whitespace (including the Unicode spaces and separators) and every C0/C1 control character
const FORBIDDEN = /[\s\u0000-\u001f\u007f-\u009f]/u;
const SCHEME = /^https?:\/\//i;

/** `value` when it is a well-formed http(s) URL by the rule above, else null. */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value || value.length > MAX_URL_LENGTH) return null;
  if (FORBIDDEN.test(value) || !SCHEME.test(value)) return null;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (!parsed.hostname || parsed.username || parsed.password) return null;
  // the authority as written (URL() drops an empty user name, as in "https://@host/")
  const authority = value.slice(value.indexOf("//") + 2).split(/[/?#]/, 1)[0];
  if (authority.includes("@")) return null;
  return value;
}

/** "sec" for a safe URL whose host is sec.gov or ends with .sec.gov, "external" for any other safe URL, null
 *  for a URL that fails the rule. */
export function linkDestination(value: unknown): LinkDestination | null {
  const url = safeHttpUrl(value);
  if (!url) return null;
  const host = new URL(url).hostname.toLowerCase();
  return host === "sec.gov" || host.endsWith(".sec.gov") ? "sec" : "external";
}

/** True only for a link that passes the rule AND goes to SEC — the one test for an "EDGAR" label. */
export function isSecLink(value: unknown): boolean {
  return linkDestination(value) === "sec";
}

export function ExternalLink({
  href,
  children,
  className,
  title,
  onClick,
  testId,
  ariaLabel,
}: {
  href: string | null | undefined;
  children: ReactNode;
  className?: string;
  title?: string;
  onClick?: MouseEventHandler<HTMLAnchorElement>;
  testId?: string;
  /** an accessible name; on a link that leaves SEC the destination host is added to it */
  ariaLabel?: string;
}) {
  const url = safeHttpUrl(href);
  if (!url) return <>{children}</>;
  if (linkDestination(url) === "sec") {
    return (
      <a
        className={className}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        title={title}
        onClick={onClick}
        aria-label={ariaLabel}
        data-destination="sec"
        data-testid={testId}
      >
        {children}
      </a>
    );
  }
  const host = new URL(url).hostname;
  return (
    <a
      className={className}
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      title={title && title !== url ? `${title} · ${url}` : url}
      onClick={onClick}
      aria-label={ariaLabel ? `${ariaLabel} (external site: ${host})` : undefined}
      data-destination="external"
      data-testid={testId}
    >
      {children}
      <span className="xl-host"> {host}</span>
      <span className="xl-mark" aria-label="external site">
        {" "}
        ext
      </span>
    </a>
  );
}
