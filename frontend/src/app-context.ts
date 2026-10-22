import { createContext, useContext } from "react";
import type { Overview } from "./api";

export interface AppState {
  batchId: string | null;
  setBatchId: (id: string) => void;
  overview: Overview | null;
  refreshOverview: () => void;
  go: (path: string) => void;
}

export const AppCtx = createContext<AppState>({
  batchId: null,
  setBatchId: () => {},
  overview: null,
  refreshOverview: () => {},
  go: () => {},
});

export const useApp = () => useContext(AppCtx);
