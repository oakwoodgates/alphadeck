// The five permanent labels, rendered VERBATIM from the backend.
//
// They are backend-authored (the `ingest_note` precedent) and this component composes nothing: no
// re-wording, no truncation, no picking a subset. The whole point is that the caveat on the page and the
// caveat in the artifact are the same bytes, so a reader who quotes a number off this surface cannot have
// been shown a softer version of its limits than the run recorded.
//
// Public clock · counterfactual universe · survivorship · adjusted closes · recompute, not the record.

export function BacktestLabels({ labels }: { labels: readonly string[] }) {
  if (!labels.length) return null;
  return (
    <ul className="bt-labels" aria-label="What this surface is, and is not">
      {labels.map((l) => (
        <li key={l}>{l}</li>
      ))}
    </ul>
  );
}
