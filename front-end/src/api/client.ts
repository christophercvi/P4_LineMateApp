import { useSession } from '@/auth/store';

/**
 * Base URL of the FastAPI back-end. Empty means same origin: in development Vite proxies /api to
 * the back-end (see vite.config.ts); in production put both behind one host or set VITE_API_BASE_URL.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export const withBase = (path: string) => (path.startsWith('/') ? `${API_BASE}${path}` : path);

/** Signed file links from the back-end are relative (/api/files/...); make them absolute for <img>/<a>. */
export const fileHref = (url: string | null | undefined) => (url ? withBase(url) : undefined);

/** fetch with the bearer token; shared by TanStack Query, XRequest (Ask) and the triage stream. */
export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = useSession.getState().token;
  if (token && !headers.has('authorization')) headers.set('Authorization', `Bearer ${token}`);
  const isForm = typeof FormData !== 'undefined' && init.body instanceof FormData;
  if (init.body && !isForm && !headers.has('content-type'))
    headers.set('Content-Type', 'application/json');
  const url = typeof input === 'string' ? withBase(input) : input;
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401 && !String(url).includes('/api/auth/')) useSession.getState().logout();
  return res;
}

type Query = Record<string, string | number | boolean | undefined | null | string[]>;

function errorMessage(data: unknown, status: number): { message: string; code?: string } {
  const d = data as { detail?: unknown; code?: string };
  if (typeof d?.detail === 'string') return { message: d.detail, code: d.code };
  if (Array.isArray(d?.detail) && d.detail[0]?.msg)
    return { message: String(d.detail[0].msg), code: 'validation_error' };
  return { message: `Request failed (${status})` };
}

export async function api<T>(
  path: string,
  opts: {
    method?: string;
    body?: unknown;
    form?: FormData;
    query?: Query;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  let url = path;
  if (opts.query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.query)) {
      if (v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0))
        continue;
      qs.set(k, Array.isArray(v) ? v.join(',') : String(v));
    }
    const s = qs.toString();
    if (s) url += `?${s}`;
  }
  const hasBody = opts.form !== undefined || opts.body !== undefined;
  const res = await apiFetch(url, {
    method: opts.method ?? (hasBody ? 'POST' : 'GET'),
    body: opts.form ?? (opts.body === undefined ? undefined : JSON.stringify(opts.body)),
    signal: opts.signal,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const { message, code } = errorMessage(data, res.status);
    throw new ApiError(res.status, message, code);
  }
  return data as T;
}
