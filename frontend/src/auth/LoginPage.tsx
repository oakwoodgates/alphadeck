import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { useLogin } from "../api/hooks";
import { errorDetail } from "./me";
import { safeNext } from "./next";
import { LIMITS } from "../api/limits";

// The login page (docs/AUTH.md): email, password, one error line. Success lands on Home, or back on the OAuth
// consent page that sent the visitor here (`?next=`, honored for that one path shape only: auth/next.ts). There is
// no sign-up and no password reset by email: a steward creates accounts with `python -m pipeline.users`.
export function LoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const next = safeNext(params.get("next"));

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    login.mutate({ email, password }, { onSuccess: () => navigate(next ?? "/", { replace: true }) });
  };

  return (
    <div className="login-page">
      <form className="login-form" onSubmit={submit} aria-label="Sign in">
        <div className="brand">
          <span className="dot" />
          ALPHA&nbsp;DECK
        </div>
        <label>
          Email
          <input
            type="email"
            autoComplete="username"
            maxLength={LIMITS.email}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            maxLength={LIMITS.passwordMax}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button type="submit" disabled={login.isPending}>
          {login.isPending ? "Signing in…" : "Sign in"}
        </button>
        {login.isError && (
          <div className="login-error" role="alert">
            {errorDetail(login.error, "Sign-in failed.")}
          </div>
        )}
      </form>
    </div>
  );
}
