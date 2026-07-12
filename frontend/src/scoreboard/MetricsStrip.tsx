import type { ScoreboardMetricOut } from "../api/hooks";
import { gateMetrics, metricHeadline } from "./rows";

// The gated metrics strip — shared by the live summary and the historical (replayed) panel, so
// the two strips render identically and stay comparable: sufficient metrics as quiet cards, the
// rest collapsed into ONE line (the gate itself is the information). Renders nothing at all when
// there are no metrics.

export function MetricsStrip({ metrics, minN }: { metrics: ScoreboardMetricOut[]; minN: number }) {
  if (!metrics.length) return null;
  const gated = gateMetrics(metrics, minN);
  return (
    <div className="sb-metrics">
      {gated.shown.map((m) => (
        <div key={m.name} className="sb-metric" title={m.claim}>
          <div className="sb-mname">{m.name.replaceAll("_", " ")}</div>
          <div className="sb-mval">{metricHeadline(m)}</div>
        </div>
      ))}
      {gated.gatedLine && <div className="sb-gated">{gated.gatedLine}</div>}
    </div>
  );
}
