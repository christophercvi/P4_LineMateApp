import type { ReactNode } from 'react';
import type { Actor } from './permissions';
import { ROLE_LABEL } from './permissions';
import { useActor } from './useAuth';
import Forbidden from '@/pages/Forbidden';

export function RequireRole({
  allow,
  reason,
  children,
}: {
  allow: (a: Actor) => boolean;
  reason: string;
  children: ReactNode;
}) {
  const actor = useActor();
  if (!allow(actor))
    return <Forbidden reason={`${reason} You are signed in as ${ROLE_LABEL[actor.role]}.`} />;
  return <>{children}</>;
}
