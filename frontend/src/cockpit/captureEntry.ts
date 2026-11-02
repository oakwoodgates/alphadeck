import { lazy } from "react";
import capability from "./capability.json";
import type { DetailCaptureEntry } from "../mcp/detailRegistry";

export const captureEntry: DetailCaptureEntry = {
  capability,
  component: lazy(() => import("./CockpitCapture")),
};
