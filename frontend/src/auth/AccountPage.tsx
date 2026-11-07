import { useState } from "react";

import { useChangePassword } from "../api/hooks";
import { errorDetail, useCurrentUser } from "./me";

const MIN_LEN = 12;

// /account — change my password (docs/AUTH.md). The current password must match and the new one needs at least
// 12 characters; every OTHER session of mine is signed out, this one stays.
export function AccountPage({ header }: { header: React.ReactNode }) {
  const me = useCurrentUser();
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [local, setLocal] = useState<string | null>(null);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setLocal(null);
    if (next.length < MIN_LEN) return setLocal(`The new password needs at least ${MIN_LEN} characters.`);
    if (next !== again) return setLocal("The two new passwords differ.");
    change.mutate(
      { current, new: next },
      {
        onSuccess: () => {
          setCurrent("");
          setNext("");
          setAgain("");
        },
      },
    );
  };

  return (
    <div>
      {header}
      <div className="account-page">
        <h2>Account</h2>
        {me && (
          <p className="muted">
            Signed in as {me.display_name} ({me.email}) · {me.role === "super_admin" ? "steward" : "user"}
          </p>
        )}
        <form className="login-form" onSubmit={submit} aria-label="Change password">
          <label>
            Current password
            <input
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
            />
          </label>
          <label>
            New password
            <input
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              required
            />
          </label>
          <label>
            New password again
            <input
              type="password"
              autoComplete="new-password"
              value={again}
              onChange={(e) => setAgain(e.target.value)}
              required
            />
          </label>
          <button type="submit" disabled={change.isPending}>
            Change password
          </button>
          {local && (
            <div className="login-error" role="alert">
              {local}
            </div>
          )}
          {!local && change.isError && (
            <div className="login-error" role="alert">
              {errorDetail(change.error, "The password was not changed.")}
            </div>
          )}
          {change.isSuccess && (
            <div className="muted" role="status">
              Password changed. Your other sessions are signed out.
            </div>
          )}
        </form>
      </div>
    </div>
  );
}
