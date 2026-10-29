import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Idea Timeline dashboard's one read (docs/dashboards/idea-timeline.md). Its own hook, not api/hooks.ts,
// so cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type IdeaTimelineOut = components["schemas"]["IdeaTimelineOut"];
export type IdeaTimelineRowOut = components["schemas"]["IdeaTimelineRowOut"];
export type IdeaTimelineSegmentOut = components["schemas"]["IdeaTimelineSegmentOut"];
export type IdeaTimelineHatchOut = components["schemas"]["IdeaTimelineHatchOut"];
export type IdeaTimelineCatalystOut = components["schemas"]["IdeaTimelineCatalystOut"];
export type IdeaTimelineDecisionOut = components["schemas"]["IdeaTimelineDecisionOut"];

/** Every live card's recorded state over time. A "now" view: no as-of, so no key beyond its name; zoom,
 *  filter and sort are on-screen choices over this one payload. */
export function useIdeaTimeline() {
  return useQuery({
    queryKey: ["dashboard-idea-timeline"] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/idea-timeline");
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
  });
}
