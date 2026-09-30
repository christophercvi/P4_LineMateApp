import type { Role, StationId } from '@/api/types';
import { useSession } from '@/auth/store';

export interface TestUser {
  sub: string;
  username: string;
  name: string;
  role: Role;
  station: StationId | null;
  crew: string | null;
}

/** Mirrors the seeded Hearthline accounts used by the back-end. */
export const USERS = {
  cook: {
    sub: 'u-marco',
    username: 'marco',
    name: 'Marco Alvarez',
    role: 'line_cook',
    station: 'grill',
    crew: 'CM-01',
  },
  sous: {
    sub: 'u-priya',
    username: 'priya',
    name: 'Priya Nair',
    role: 'sous_chef',
    station: 'grill',
    crew: 'CM-02',
  },
  manager: {
    sub: 'u-elena',
    username: 'elena',
    name: 'Elena Petrova',
    role: 'kitchen_manager',
    station: null,
    crew: 'CM-11',
  },
  admin: {
    sub: 'u-admin',
    username: 'alex.admin',
    name: 'Alex Morgan',
    role: 'admin',
    station: null,
    crew: null,
  },
  service: {
    sub: 'u-svc-mcp',
    username: 'svc-mcp',
    name: 'MCP service account',
    role: 'service',
    station: null,
    crew: null,
  },
} satisfies Record<string, TestUser>;

export type Who = keyof typeof USERS;

const b64url = (o: unknown) =>
  btoa(JSON.stringify(o)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');

/** The front-end only decodes claims (the back-end verifies signatures), so an unsigned token is enough. */
export function makeToken(user: TestUser, ttlSeconds = 3600): string {
  const now = Math.floor(Date.now() / 1000);
  return `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url({ ...user, iat: now, exp: now + ttlSeconds })}.test-signature`;
}

export function signIn(who: Who) {
  useSession.getState().login(makeToken(USERS[who]));
}

/** Role of the caller, read back from the bearer token in an intercepted request. */
export function roleOf(request: Request): Role | null {
  const auth = request.headers.get('authorization') ?? '';
  const token = auth.replace(/^Bearer\s+/i, '');
  const part = token.split('.')[1];
  if (!part) return null;
  try {
    return (JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/'))) as { role: Role }).role;
  } catch {
    return null;
  }
}
