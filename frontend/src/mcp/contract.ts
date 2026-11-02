import type { ClockOut } from "../api/hooks";
import type { CaptureSettings } from "./viewContext";

export type FrozenResponse = {
  method: "GET";
  path: string;
  query: Record<string, string>;
  body: unknown;
  status: number;
};

export type CapturedSnapshot = {
  snapshot_id: string;
  dashboard: string;
  asof: string;
  settings: CaptureSettings;
  clock: ClockOut;
  responses: FrozenResponse[];
  context?: unknown;
  consistency?: unknown;
  captured_at?: string;
  navigation?: CaptureNavigation[];
  references?: CaptureReference[];
};

export type CaptureNavigation = {
  id: string;
  kind: "fresh_capture";
  destination: { dashboard: string; settings: CaptureSettings };
};

export type CaptureReference = {
  id: string;
  kind: "captured_json";
  response_index: number;
  pointer: string;
  label?: string | null;
  category?: string;
};

export type ViewBridge = {
  status: "loading" | "ready" | "error";
  snapshot_id: string;
  settings: CaptureSettings;
  observation: unknown;
  targets: unknown[];
  error?: string;
  error_code?: string;
};

declare global {
  interface Window {
    __ALPHA_DECK_MCP_SNAPSHOT__?: CapturedSnapshot;
    __ALPHA_DECK_MCP_VIEW__?: ViewBridge;
  }
}
