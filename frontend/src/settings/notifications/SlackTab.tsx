import { useState } from "react";

import {
  useClearSlackWebhook,
  useSetSlackWebhook,
  useSlackWebhook,
  useTestSlackWebhook,
} from "../../api/hooks";
import { errorDetail } from "../../auth/me";
import { LIMITS } from "../../api/limits";

// /settings/notifications — my own Slack webhook (docs/AUTH.md §Settings page, docs/FEED_LOOP.md step 3). The URL
// is write-only: the API never returns it, so the field is always empty on load and the status line says only
// whether one is set. Remove and the test message show only when one is set. A quiet page: no badges, no banners.
export function SlackTab() {
  const status = useSlackWebhook();
  const save = useSetSlackWebhook();
  const clear = useClearSlackWebhook();
  const test = useTestSlackWebhook();
  const [url, setUrl] = useState("");

  const isSet = status.data?.set === true;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    test.reset();
    save.mutate(url.trim(), { onSuccess: () => setUrl("") });
  };

  return (
    <div className="settings-section">
      <p className="muted">
        A card of yours arming, or changing while armed, and the nightly digest of names newly armed in your cards
        are posted to this Slack webhook. Operations pages never are.
      </p>
      {status.isPending && <p className="muted">Loading…</p>}
      {status.isError && <p className="login-error">The webhook status could not be read.</p>}
      {status.data && (
        <p className="settings-status" role="status">
          {isSet && status.data.updated_at
            ? `Slack webhook set on ${new Date(status.data.updated_at).toLocaleDateString()}.`
            : isSet
              ? "Slack webhook set."
              : "No Slack webhook. Armed calls and the nightly digest for your cards are logged only."}
        </p>
      )}
      <form className="login-form" onSubmit={submit} aria-label="Slack webhook">
        <label>
          {isSet ? "Replace the Slack webhook URL" : "Slack webhook URL"}
          <input
            type="url"
            autoComplete="off"
            placeholder="https://hooks.slack.com/services/…"
            maxLength={LIMITS.url}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
          />
        </label>
        <button type="submit" disabled={save.isPending}>
          Save
        </button>
        {save.isError && (
          <div className="login-error" role="alert">
            {errorDetail(save.error, "The webhook was not saved.")}
          </div>
        )}
      </form>
      {isSet && (
        <div className="settings-actions">
          <button type="button" disabled={test.isPending} onClick={() => test.mutate()}>
            Send a test message
          </button>
          <button
            type="button"
            disabled={clear.isPending}
            onClick={() => {
              test.reset();
              clear.mutate();
            }}
          >
            Remove
          </button>
        </div>
      )}
      {isSet && test.data && (
        <p className="muted" role="status">
          {test.data.delivered ? "Test message sent" : "Test message not delivered"} ({test.data.detail}).
        </p>
      )}
      {isSet && test.isError && (
        <p className="login-error">{errorDetail(test.error, "The test message was not sent.")}</p>
      )}
      {clear.isError && (
        <p className="login-error">{errorDetail(clear.error, "The webhook was not removed.")}</p>
      )}
    </div>
  );
}
