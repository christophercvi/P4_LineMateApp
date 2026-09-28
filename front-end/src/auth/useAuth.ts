import { useMemo } from 'react';
import type { Actor } from './permissions';
import { useSession } from './store';

export function useAuth() {
  const claims = useSession((s) => s.claims);
  const logout = useSession((s) => s.logout);
  const actor = useMemo<Actor | null>(
    () =>
      claims
        ? {
            role: claims.role,
            station: claims.station,
            crewMemberId: claims.crew,
            userId: claims.sub,
          }
        : null,
    [claims],
  );
  return { claims, actor, logout };
}

/** Use inside the authenticated layout, where a session is guaranteed. */
export function useActor(): Actor {
  const { actor } = useAuth();
  if (!actor) throw new Error('useActor() used outside an authenticated route');
  return actor;
}
