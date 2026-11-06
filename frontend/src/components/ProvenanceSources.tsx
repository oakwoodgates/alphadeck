import type { ProvenanceOut } from "../api/hooks";
import { useState } from "react";
import "./provenance-sources.css";

export function safeReferenceUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try { return ["http:", "https:"].includes(new URL(value).protocol) ? value : null; }
  catch { return null; }
}

/** The stored source references and computation detail, without fetching or interpreting a source. */
export function ProvenanceSources({ sources, label = "Sources" }: {
  sources: readonly ProvenanceOut[];
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  if (!sources.length) return null;
  return <details className="provenance-sources" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{label} · {sources.length}</summary>
    {open && sources.map((source, index) => <section className="provenance-source" key={index}
      aria-label={`Source · ${source.source}`}>
      <dl>
        <dt>Source</dt><dd>{source.source}</dd>
        <dt>Reference</dt><dd>{source.ref}</dd>
        <dt>URL</dt><dd>{safeReferenceUrl(source.url) ? <a href={source.url!} target="_blank" rel="noreferrer">{source.url}</a> : (source.url ?? "—")}</dd>
        <dt>Detail</dt><dd><pre>{JSON.stringify(source.detail, null, 2)}</pre></dd>
      </dl>
    </section>)}
  </details>;
}
