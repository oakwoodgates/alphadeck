import { useState } from "react";

import { useChangePassword } from "../../api/hooks";
import { errorDetail } from "../../auth/me";
import { LIMITS } from "../../api/limits";

const MIN_LEN = 12;

// Change my password (docs/AUTH.md), on the Settings page's Profile tab (moved from the old /account page,
// unchanged in behavior and copy). The current password must match and the new one needs at least 12 characters;
// every OTHER session of mine is signed out, this one stays.
export function PasswordForm() {
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
    <form className="login-form" onSubmit={submit} aria-label="Change password">
      <label>
        Current password
        <input
          type="password"
          autoComplete="current-password"
          maxLength={LIMITS.passwordMax}
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
          maxLength={LIMITS.passwordMax}
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
          maxLength={LIMITS.passwordMax}
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
  );
}
