import type { ReactNode } from "react";

/** The shared error-toast shell (the `toast show err` chrome). Callers keep their own `{x.isError && …}`
 *  guard and pass the message as children; the success toast (`toast show`) is intentionally separate. */
export function ErrorToast({ children }: { children: ReactNode }) {
  return <div className="toast show err">{children}</div>;
}
