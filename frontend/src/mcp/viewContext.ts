import { createContext, useContext } from "react";
import type { CaptureNavigation, CaptureReference, FrozenResponse } from "./contract";

export type CaptureSettings = Record<string, unknown>;
export type CaptureView = {
  settings: CaptureSettings;
  readOnly: boolean;
  responses?: readonly FrozenResponse[];
  navigation?: readonly CaptureNavigation[];
  references?: readonly CaptureReference[];
};
export const CaptureViewContext = createContext<CaptureView>({
  settings: {}, readOnly: false,
});

/** A frozen view supplies initial controls; ordinary dashboard visits retain their local state. */
export function useCaptureView() {
  return useContext(CaptureViewContext);
}
