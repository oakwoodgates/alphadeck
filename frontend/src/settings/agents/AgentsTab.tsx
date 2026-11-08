import { useEffect, useRef, useState } from "react";

import {
  useAgents,
  useConnections,
  useCreateToken,
  useDisconnect,
  useRevokeToken,
  useTokens,
} from "../../api/hooks";
import type { ConnectionOut, TokenCreatedOut, TokenOut } from "../../api/hooks";
import { errorDetail } from "../../auth/me";

// /settings/agents — how an AI client reaches MY workspace (docs/AGENT_TOOLS.md §Hosted endpoint) and my access
// tokens. The copy is the signed-off mockup's (docs/temp/mockups/agents-tab-mockup-2026-11-08.html). A quiet page:
// no banner when the install has no public address. Every action has a visible inverse or is re-doable: a token
// revoked here stays on screen, grayed, for the rest of the visit (hide, never vanish); after a reload the list hides
// revoked tokens unless "Show revoked tokens" is ticked. A new token is shown ONCE, until Done. Connected apps
// (the OAuth sign-in, docs/CONNECTORS.md; copy from docs/temp/mockups/consent-and-connected-apps-mockup-2026-11-08.html)
// follow the same rules: a disconnected app stays on screen, grayed, for the visit.

const COPIED_MS = 2000;

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  const copy = () => {
    void navigator.clipboard?.writeText(text).catch(() => undefined);
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), COPIED_MS);
  };
  return (
    <button className="btn" type="button" onClick={copy}>
      {copied ? "Copied" : label}
    </button>
  );
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** "today 09:12" for a time today, else the local date as YYYY-MM-DD. */
export function whenLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const day = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  return day === today ? `today ${pad(d.getHours())}:${pad(d.getMinutes())}` : day;
}

function ConnectSection({
  hosted,
  url,
  steward,
  oauth,
}: {
  hosted: boolean;
  url: string | null;
  steward: boolean;
  oauth: boolean;
}) {
  if (!hosted || !url) {
    return (
      <p className="muted">
        Connecting from Claude, ChatGPT or Grok needs Alpha Deck on a public address. This install does not have one
        yet, so there is no connector URL to give them. Claude Code on this machine can read your workspace today
        through the local server with a token from below (see docs/AGENT_TOOLS.md).
      </p>
    );
  }
  const command = `claude mcp add --transport http alphadeck ${url} --header "Authorization: Bearer <your token>"`;
  return (
    <>
      <p className="muted">
        Give this URL to an AI client. It signs in as you and reads only your workspace: your cards, calls, decisions
        and ratified facts. Nothing it does can change them.
      </p>
      <div className="url-row">
        <input type="text" readOnly value={url} aria-label="Connector URL" />
        <CopyButton text={url} />
      </div>
      {steward && (
        <p className="steward-line">
          You are a steward: a client you grant the steward scope also reads the Admin readouts (the splits queue, the
          onboarding queue, the status). It still cannot confirm or change anything.
        </p>
      )}
      <div className="agent-clients">
        <details>
          <summary>Claude (claude.ai)</summary>
          <div className="body">
            {oauth ? (
              <ol>
                <li>
                  In Claude, open <b>Customize → Connectors → Add custom connector</b>.
                </li>
                <li>
                  Paste the URL above. Under Authentication choose <b>Sign in now</b> and leave the client identity on
                  its default.
                </li>
                <li>
                  Sign in with your Alpha Deck email and password on the page that opens, then <b>Allow</b>.
                </li>
              </ol>
            ) : (
              <ol>
                <li>
                  In Claude, open <b>Customize → Connectors → Add custom connector</b>.
                </li>
                <li>
                  Paste the URL above. Under Authentication choose <b>Sign in now</b>.
                </li>
                <li>
                  Sign in with your Alpha Deck email and password, then <b>Allow</b>.
                </li>
              </ol>
            )}
            {oauth && (
              <p>
                If Claude asks for a client ID instead, enter <code>alphadeck-claude</code> and leave the secret blank.
              </p>
            )}
            <p>
              On a Free plan one custom connector is allowed. On a Team plan the Owner adds it under Organization
              settings → Connectors, and each member signs in to it individually.
            </p>
            {!oauth && (
              <p>
                Available once sign-in through Claude is enabled on this install (Phase 2). Until then use Claude Code
                below.
              </p>
            )}
          </div>
        </details>
        <details open>
          <summary>Claude Code</summary>
          <div className="body">
            <p>With a token from the Access tokens section:</p>
            <div className="cmd-row">
              <code className="cmd">{command}</code>
              <CopyButton text={command} />
            </div>
            <p>Start a new Claude Code session afterwards; a running session keeps the servers it opened with.</p>
          </div>
        </details>
        <details>
          <summary>ChatGPT</summary>
          <div className="body">
            <ol>
              <li>
                Open <b>Plugins → Add custom MCP server</b> (Business and Enterprise plans; Pro can read only).
              </li>
              <li>
                Paste the URL above, choose OAuth, sign in with your Alpha Deck email and password, then <b>Allow</b>.
              </li>
            </ol>
            <p>Available once sign-in through ChatGPT is enabled on this install (Phase 4).</p>
          </div>
        </details>
        <details>
          <summary>Grok</summary>
          <div className="body">
            <ol>
              <li>
                Open <b>grok.com/connectors → New Connector → Custom</b>.
              </li>
              <li>Paste the URL above and sign in with your Alpha Deck email and password.</li>
            </ol>
            <p>
              On Business and Enterprise plans an admin provisions the connector first. Available once sign-in through
              Grok is enabled on this install (Phase 4).
            </p>
          </div>
        </details>
      </div>
    </>
  );
}

function TokenRow({
  token,
  revokedNow,
  onRevoke,
  busy,
}: {
  token: TokenOut;
  revokedNow: boolean;
  onRevoke: () => void;
  busy: boolean;
}) {
  const revoked = token.revoked_at != null || revokedNow;
  return (
    <tr className={revoked ? "revoked" : undefined}>
      <td>{token.name}</td>
      <td className="when">{whenLabel(token.created_at)}</td>
      <td className="when">{token.last_used_at ? whenLabel(token.last_used_at) : "never"}</td>
      <td className={revoked ? "act muted" : "act"}>
        {revokedNow ? (
          "revoked just now"
        ) : revoked ? (
          "revoked"
        ) : (
          <button className="btn" type="button" disabled={busy} onClick={onRevoke}>
            Revoke
          </button>
        )}
      </td>
    </tr>
  );
}

function TokensSection() {
  const tokens = useTokens();
  const create = useCreateToken();
  const revoke = useRevokeToken();
  const [name, setName] = useState("");
  const [showRevoked, setShowRevoked] = useState(false);
  // revoked on this visit: kept on screen, grayed, whatever the toggle says (hide, never vanish)
  const [revokedNow, setRevokedNow] = useState<ReadonlySet<string>>(new Set());
  const [created, setCreated] = useState<TokenCreatedOut | null>(null);

  const rows = (tokens.data?.tokens ?? []).filter(
    (t) => t.revoked_at == null || showRevoked || revokedNow.has(t.id),
  );

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    create.mutate(name.trim(), {
      onSuccess: (data) => {
        setCreated(data);
        setName("");
      },
    });
  };
  const revokeOne = (id: string) =>
    revoke.mutate(id, { onSuccess: () => setRevokedNow((prev) => new Set(prev).add(id)) });

  return (
    <>
      <p className="muted">
        A token lets a client read your workspace without signing in each time: Claude Code, a script, or an API that
        takes a bearer token. Each one is shown once, when you create it. Revoke a token you no longer use.
      </p>
      {tokens.isPending && <p className="muted">Loading…</p>}
      {tokens.isError && <p className="login-error">Your tokens could not be read.</p>}
      {tokens.data && (
        <table className="tokens" aria-label="Access tokens">
          <thead>
            <tr>
              <th>Name</th>
              <th>Created</th>
              <th>Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <TokenRow
                key={t.id}
                token={t}
                revokedNow={revokedNow.has(t.id)}
                busy={revoke.isPending}
                onRevoke={() => revokeOne(t.id)}
              />
            ))}
          </tbody>
        </table>
      )}
      {revoke.isError && <p className="login-error">{errorDetail(revoke.error, "The token was not revoked.")}</p>}
      <label className="show-revoked">
        <input type="checkbox" checked={showRevoked} onChange={(e) => setShowRevoked(e.target.checked)} /> Show
        revoked tokens
      </label>
      {created ? (
        <div className="reveal" role="status">
          <div className="name">
            Token <b>{created.name}</b> created.
          </div>
          <div className="url-row">
            <input type="text" readOnly value={created.token} aria-label="The new token" />
            <CopyButton text={created.token} />
          </div>
          <p className="once">
            This is the only time it is shown. Store it where your client reads it (for Claude Code, the{" "}
            <code>--header</code> value; for the local server, <code>ALPHADECK_MCP_TOKEN</code>).
          </p>
          <div className="settings-actions">
            <button className="btn" type="button" onClick={() => setCreated(null)}>
              Done
            </button>
          </div>
        </div>
      ) : (
        <form className="login-form" onSubmit={submit} aria-label="New token">
          <label>
            New token name
            <input
              type="text"
              autoComplete="off"
              placeholder="e.g. claude-code laptop"
              value={name}
              maxLength={60}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </label>
          <button type="submit" disabled={create.isPending}>
            Create
          </button>
          {create.isError && (
            <div className="login-error" role="alert">
              {errorDetail(create.error, "The token was not created.")}
            </div>
          )}
        </form>
      )}
    </>
  );
}

export function AgentsTab() {
  const agents = useAgents();
  return (
    <div className="settings-section agents-tab">
      <h3>Connect an AI client</h3>
      {agents.isPending && <p className="muted">Loading…</p>}
      {agents.isError && <p className="login-error">The connection details could not be read.</p>}
      {agents.data && (
        <ConnectSection
          hosted={agents.data.hosted}
          url={agents.data.connector_url ?? null}
          steward={agents.data.steward}
          oauth={agents.data.oauth ?? false}
        />
      )}
      <h3>Access tokens</h3>
      <TokensSection />
      <h3>Connected apps</h3>
      <ConnectionsSection />
    </div>
  );
}

function ConnectionRow({
  connection,
  disconnectedNow,
  onDisconnect,
  busy,
}: {
  connection: ConnectionOut;
  disconnectedNow: boolean;
  onDisconnect: () => void;
  busy: boolean;
}) {
  const gone = connection.revoked_at != null || disconnectedNow;
  return (
    <tr className={gone ? "revoked" : undefined}>
      <td>
        {connection.client_known ? (
          connection.client_name
        ) : (
          <>
            “{connection.client_name}” <span className="tag">unverified</span>
          </>
        )}
      </td>
      <td className="scopes-cell">{connection.scopes.join(" ")}</td>
      <td className="when">{whenLabel(connection.created_at)}</td>
      <td className="when">{connection.last_used_at ? whenLabel(connection.last_used_at) : "never"}</td>
      <td className={gone ? "act muted" : "act"}>
        {disconnectedNow ? (
          "disconnected just now"
        ) : gone ? (
          "disconnected"
        ) : (
          <button className="btn" type="button" disabled={busy} onClick={onDisconnect}>
            Disconnect
          </button>
        )}
      </td>
    </tr>
  );
}

/** The apps signed in to my account through the OAuth sign-in (docs/CONNECTORS.md). Disconnect asks nothing and
 *  keeps the row, grayed, for the visit (hide, never vanish); "Show disconnected apps" brings back older ones. */
function ConnectionsSection() {
  const connections = useConnections();
  const disconnect = useDisconnect();
  const [showGone, setShowGone] = useState(false);
  const [goneNow, setGoneNow] = useState<ReadonlySet<string>>(new Set());

  const all = connections.data?.connections ?? [];
  const rows = all.filter((c) => c.revoked_at == null || showGone || goneNow.has(c.id));
  const anyGone = all.some((c) => c.revoked_at != null);
  const disconnectOne = (id: string) =>
    disconnect.mutate(id, { onSuccess: () => setGoneNow((prev) => new Set(prev).add(id)) });

  return (
    <>
      <p className="muted">
        Apps you signed in to with your Alpha Deck account. Each one reads your workspace with the access you allowed
        and nothing more. Disconnect an app to sign it out everywhere at once.
      </p>
      {connections.isPending && <p className="muted">Loading…</p>}
      {connections.isError && <p className="login-error">Your connected apps could not be read.</p>}
      {connections.data && rows.length === 0 && (
        <p className="muted">No app is connected yet. Connect one with the URL above.</p>
      )}
      {connections.data && rows.length > 0 && (
        <table className="tokens" aria-label="Connected apps">
          <thead>
            <tr>
              <th>App</th>
              <th>Access</th>
              <th>Connected</th>
              <th>Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <ConnectionRow
                key={c.id}
                connection={c}
                disconnectedNow={goneNow.has(c.id)}
                busy={disconnect.isPending}
                onDisconnect={() => disconnectOne(c.id)}
              />
            ))}
          </tbody>
        </table>
      )}
      {disconnect.isError && (
        <p className="login-error">{errorDetail(disconnect.error, "The app was not disconnected.")}</p>
      )}
      {anyGone && (
        <label className="show-revoked">
          <input type="checkbox" checked={showGone} onChange={(e) => setShowGone(e.target.checked)} /> Show
          disconnected apps
        </label>
      )}
    </>
  );
}
