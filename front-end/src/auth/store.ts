import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { jwtDecode } from 'jwt-decode';
import type { SessionClaims } from '@/api/types';

interface SessionState {
  token: string | null;
  claims: SessionClaims | null;
  login: (token: string) => void;
  logout: () => void;
}

export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      token: null,
      claims: null,
      login: (token) => set({ token, claims: jwtDecode<SessionClaims>(token) }),
      logout: () => set({ token: null, claims: null }),
    }),
    {
      name: 'linemate-session',
      onRehydrateStorage: () => (state) => {
        if (state?.claims && state.claims.exp * 1000 < Date.now()) state.logout();
      },
    },
  ),
);

interface UiState {
  mode: 'light' | 'dark';
  siderCollapsed: boolean;
  toggleMode: () => void;
  setSiderCollapsed: (v: boolean) => void;
}

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      mode: 'light',
      siderCollapsed: false,
      toggleMode: () => set((s) => ({ mode: s.mode === 'light' ? 'dark' : 'light' })),
      setSiderCollapsed: (v) => set({ siderCollapsed: v }),
    }),
    { name: 'linemate-ui' },
  ),
);
