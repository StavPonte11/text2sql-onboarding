import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface AdminUser {
  id: string;
  username: string;
}

interface AdminState {
  token: string | null;
  user: AdminUser | null;
  isAuthenticated: boolean;
  setAuth: (token: string, user: AdminUser) => void;
  logout: () => void;
}

export const useAdminStore = create<AdminState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      isAuthenticated: false,
      setAuth: (token, user) => set({ token, user, isAuthenticated: true }),
      logout: () => set({ token: null, user: null, isAuthenticated: false }),
    }),
    {
      name: 'admin-storage',
    }
  )
);
