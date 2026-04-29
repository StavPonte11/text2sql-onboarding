import { create } from "zustand";
import type { UserScope } from "../types";

interface AppState {
  activeScope: UserScope | null;
  setActiveScope: (scope: UserScope | null) => void;
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  activeScope: null,
  setActiveScope: (scope) => set({ activeScope: scope }),
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}));
