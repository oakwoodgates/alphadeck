import type { PromoteOnboardingRef } from "../api/hooks";
import { useThesisOnboarding } from "../api/hooks";
import { ErrorToast } from "../components/ErrorToast";
import { errText } from "./format";

/** The staged-onboarding note (X1): a Save/promote that added more COLD names than the fast lane takes (or
 *  landed inside the nightly window) QUEUED them for the onboarding job instead of fetching at once — their
 *  filing history loads in batches outside the nightly window. This note polls the thesis's slice of the
 *  queue and says how far it got. HONEST LOUDNESS: rendered only when the promote response carried an
 *  `onboarding` ref; QUIET while names wait or load and when all are loaded; LOUD only when a name FAILED
 *  (Retry lives on the Admin page) or the poll itself fails. No browser storage — the ref lives in the save
 *  flow's state. */
export function OnboardingNote({
  thesisId,
  onboarding,
}: {
  thesisId: string;
  onboarding: PromoteOnboardingRef;
}) {
  const q = useThesisOnboarding(thesisId, true);
  const names = onboarding.queued === 1 ? "1 new name" : `${onboarding.queued} new names`;
  if (q.isError) {
    return (
      <ErrorToast>
        Lost track of the history load for {names} — {errText(q.error)}. The onboarding job still loads
        them; the Admin page shows the queue.
      </ErrorToast>
    );
  }
  const c = q.data?.counts;
  const failed = (q.data?.pending ?? []).filter((n) => n.status === "failed");
  if (failed.length > 0) {
    return (
      <ErrorToast>
        History load FAILED for {failed.length === 1 ? "1 name" : `${failed.length} names`} (
        {failed.map((n) => n.ticker ?? n.security_id).join(", ")}) — Retry on the Admin page. Until then
        the nightly reads only their recent filings.
      </ErrorToast>
    );
  }
  const waiting = c ? (c.queued ?? 0) + (c.in_progress ?? 0) : null;
  if (c && waiting === 0) {
    return <p className="muted wb-ingest-note">✓ History loaded for {names}.</p>;
  }
  return (
    <p className="muted wb-ingest-note">
      Queued {names} to load their filing history — {onboarding.reason}.
      {c ? ` ${c.done ?? 0} loaded, ${waiting} waiting.` : ""} Until a name is loaded, the nightly reads only
      its recent filings and marks the call "not fully loaded".
    </p>
  );
}
