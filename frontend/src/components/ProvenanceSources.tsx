import type { ProvenanceOut } from "../api/hooks";
import { useState } from "react";
import { useCaptureBindings } from "../mcp/bindings";
import { targetId } from "../mcp/targetId";
import { useCaptureView } from "../mcp/viewContext";
import "./provenance-sources.css";

export function safeReferenceUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try { return ["http:", "https:"].includes(new URL(value).protocol) ? value : null; }
  catch { return null; }
}

/** The stored source references and computation detail, without fetching or interpreting a source. */
export function ProvenanceSources({ sources, path, pointer, scope, sourceKey = "sources", label = "Sources" }: {
  sources: readonly ProvenanceOut[];
  path: string;
  pointer: string;
  scope: string;
  sourceKey?: string;
  label?: string;
}) {
  const { readOnly, settings } = useCaptureView();
  const { sourceRefs } = useCaptureBindings();
  const [open, setOpen] = useState(false);
  const expanded = readOnly ? settings.evidence_open === true : open;
  if (!sources.length) return null;
  return <details className="provenance-sources" open={expanded} onToggle={(event) => { if (!readOnly) setOpen(event.currentTarget.open); }}
    data-mcp-id={targetId("source-block", scope, pointer)} {...sourceRefs(path, pointer)}>
    <summary>{label} · {sources.length}</summary>
    {expanded && sources.map((source, index) => <section className="provenance-source" key={index}
      aria-label={`Source · ${source.source}`} data-mcp-id={targetId("source", scope, pointer, String(index))}
      {...sourceRefs(path, `${pointer}/${sourceKey}/${index}`)}>
      <dl>
        <dt>Source</dt><dd>{source.source}</dd>
        <dt>Reference</dt><dd>{source.ref}</dd>
        <dt>URL</dt><dd>{safeReferenceUrl(source.url) ? <a href={source.url!} target="_blank" rel="noreferrer">{source.url}</a> : (source.url ?? "—")}</dd>
        <dt>Detail</dt><dd><pre>{JSON.stringify(source.detail, null, 2)}</pre></dd>
      </dl>
    </section>)}
  </details>;
}
