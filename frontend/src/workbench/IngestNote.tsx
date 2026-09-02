import type { PromoteIngestRef } from "../api/hooks";
import { useIngestJobStatus } from "../api/hooks";
import { ErrorToast } from "../components/ErrorToast";
import { errText } from "./format";

/** The on-promote ingest indicator (PR-4): a Save/promote that ADDED members kicked a background data
 *  fetch for exactly those names — this note polls the job and says how it went. HONEST LOUDNESS: the
 *  caller renders it ONLY when the promote response carried an `ingest` ref (a save that added nothing
 *  shows nothing at all); while running (or queued behind an earlier fetch) and on done it stays QUIET
 *  (`muted`), and only a failure — including a lost job (404: expired / server restart) — is LOUD. Every
 *  failure line names the backstop: the nightly cron ingests everything regardless, so no outcome here
 *  ever loses data. No browser storage — the ref lives in the save flow's state. */
export function IngestNote({ thesisId, ingest }: { thesisId: string; ingest: PromoteIngestRef }) {
  const jobQ = useIngestJobStatus(thesisId, ingest.job_id);
  const names = ingest.new_members === 1 ? "1 new name" : `${ingest.new_members} new names`;
  if (jobQ.isError) {
    return (
      <ErrorToast>
        Lost track of the data fetch for {names} — {errText(jobQ.error)}. The nightly cron will pick
        them up.
      </ErrorToast>
    );
  }
  const st = jobQ.data?.status;
  if (st === "failed") {
    return (
      <ErrorToast>
        Data fetch for {names} failed — {jobQ.data?.error ?? "unknown error"}. The nightly cron will
        retry them.
      </ErrorToast>
    );
  }
  if (st === "done") {
    return <p className="muted wb-ingest-note">✓ Data fetched for {names}.</p>;
  }
  return <p className="muted wb-ingest-note">Fetching data for {names}…</p>;
}
