import { useEffect, useState } from "react";

import { useUpdateProfile } from "../../api/hooks";
import { errorDetail, useCurrentUser } from "../../auth/me";
import { PasswordForm } from "./PasswordForm";

// /settings/profile — my display name and email (PUT /account/profile), then the change-password form. On a save
// the reply is written straight into the /auth/me cache, so the header's name changes at once.
export function ProfileTab() {
  const me = useCurrentUser();
  const update = useUpdateProfile();
  const [name, setName] = useState(me?.display_name ?? "");
  const [email, setEmail] = useState(me?.email ?? "");

  // the signed-in user arrives with the shell; follow it if it changes underneath (a save, a re-fetch)
  useEffect(() => {
    if (me) {
      setName(me.display_name);
      setEmail(me.email);
    }
  }, [me]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    update.mutate({ display_name: name, email });
  };

  return (
    <div className="settings-section">
      {me && (
        <p className="muted">
          Signed in as {me.display_name} ({me.email}) · {me.role === "super_admin" ? "steward" : "user"}
        </p>
      )}
      <form className="login-form" onSubmit={submit} aria-label="Profile">
        <label>
          Display name
          <input type="text" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Email
          <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <button type="submit" disabled={update.isPending}>
          Save
        </button>
        {update.isError && (
          <div className="login-error" role="alert">
            {errorDetail(update.error, "The profile was not saved.")}
          </div>
        )}
        {update.isSuccess && (
          <div className="muted" role="status">
            Saved.
          </div>
        )}
      </form>
      <h3>Password</h3>
      <PasswordForm />
    </div>
  );
}
