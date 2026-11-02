import { createContext, useContext } from "react";

export type CaptureSettings = Record<string, unknown>;
export const CaptureViewContext = createContext<{ settings: CaptureSettings; readOnly: boolean }>({
  settings: {}, readOnly: false,
});

/** A frozen view supplies initial controls; ordinary dashboard visits retain their local state. */
export function useCaptureView() {
  return useContext(CaptureViewContext);
}
