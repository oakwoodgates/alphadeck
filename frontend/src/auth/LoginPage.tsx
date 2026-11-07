import { useState } from "react";
import { useNavigate } from "react-router";

import { useLogin } from "../api/hooks";
import { errorDetail } from "./me";

// The login page (docs/AUTH.md): email, password, one error line. Success lands on Home. There is no sign-up and
// no password reset by email: a steward creates accounts with `python -m pipeline.users`.
export function LoginPage() {
  const navigate = useNavigate();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    login.mutate({ email, password }, { onSuccess: () => navigate("/", { replace: true }) });
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
