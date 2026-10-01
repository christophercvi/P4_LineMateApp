import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { api, ApiError, apiFetch, fileHref, withBase } from '@/api/client';
import { useSession } from '@/auth/store';
import { server } from '@/test/server';
import { signIn } from '@/test/users';

describe('api client', () => {
  it('sends the bearer token and JSON body, and builds query strings without empty values', async () => {
    signIn('sous');
    let seen: { auth: string | null; type: string | null; url: string; body: unknown } | null =
      null;
    server.use(
      http.post('/api/tickets', async ({ request }) => {
        seen = {
          auth: request.headers.get('authorization'),
          type: request.headers.get('content-type'),
          url: request.url,
          body: await request.json(),
        };
        return HttpResponse.json({ id: 'TKT-900' }, { status: 201 });
      }),
    );
    const out = await api<{ id: string }>('/api/tickets', {
      body: { title: 'Fryer basket handle loose' },
      query: { a: 'x', empty: '', none: undefined, list: ['open', 'blocked'], flag: true },
    });
    expect(out.id).toBe('TKT-900');
    expect(seen!.auth).toMatch(/^Bearer .+\..+\..+$/);
    expect(seen!.type).toBe('application/json');
    expect(new URL(seen!.url).search).toBe('?a=x&list=open%2Cblocked&flag=true');
    expect(seen!.body).toEqual({ title: 'Fryer basket handle loose' });
  });

  it('lets the browser set the multipart boundary for FormData uploads', async () => {
    signIn('sous');
    let type = '';
    let fileName = '';
    server.use(
      http.post('/api/tickets/:id/attachments', async ({ request }) => {
        type = request.headers.get('content-type') ?? '';
        const raw = await request.text();
        // jsdom's FormData drops the filename when Node's fetch serializes it; the part and bytes remain.
        fileName =
          raw.includes('name="files"') && raw.includes('jpeg-bytes')
            ? 'files-part'
            : raw.slice(0, 200);
        return HttpResponse.json([], { status: 201 });
      }),
    );
    const form = new FormData();
    form.append(
      'files',
      new File(['jpeg-bytes'], 'gasket.jpg', { type: 'image/jpeg' }),
      'gasket.jpg',
    );
    await api('/api/tickets/TKT-001/attachments', { form });
    expect(type).toMatch(/^multipart\/form-data; boundary=/);
    expect(fileName).toBe('files-part');
  });

  it('raises ApiError with the FastAPI detail message and validation errors', async () => {
    server.use(
      http.get('/api/documents/DOC-X', () =>
        HttpResponse.json({ detail: 'Document not found', code: 'not_found' }, { status: 404 }),
      ),
      http.post('/api/tickets', () =>
        HttpResponse.json(
          { detail: [{ msg: 'String should have at least 5 characters' }] },
          { status: 422 },
        ),
      ),
      http.get('/api/broken', () => new HttpResponse('oops', { status: 500 })),
    );
    await expect(api('/api/documents/DOC-X')).rejects.toMatchObject({
      status: 404,
      message: 'Document not found',
      code: 'not_found',
    });
    await expect(api('/api/tickets', { body: { title: 'x' } })).rejects.toMatchObject({
      status: 422,
      code: 'validation_error',
      message: 'String should have at least 5 characters',
    });
    const err = (await api('/api/broken').catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe('Request failed (500)');
  });

  it('signs the user out when a protected call returns 401, but not for the login call itself', async () => {
    signIn('cook');
    server.use(
      http.post('/api/auth/login', () =>
        HttpResponse.json({ detail: 'Incorrect username or password' }, { status: 401 }),
      ),
      http.get('/api/dashboard', () =>
        HttpResponse.json({ detail: 'Token expired' }, { status: 401 }),
      ),
    );
    await expect(
      api('/api/auth/login', { body: { username: 'marco', password: 'nope' } }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(useSession.getState().token).not.toBeNull();
    await expect(api('/api/dashboard')).rejects.toMatchObject({ status: 401 });
    expect(useSession.getState().token).toBeNull();
  });

  it('returns undefined for 204 responses', async () => {
    server.use(
      http.delete('/api/conversations/:id', () => new HttpResponse(null, { status: 204 })),
    );
    await expect(api('/api/conversations/c-1', { method: 'DELETE' })).resolves.toBeUndefined();
  });

  it('keeps absolute URLs and resolves signed file links against the API base', async () => {
    expect(withBase('/api/files/FA-1?exp=1&sig=x')).toBe('/api/files/FA-1?exp=1&sig=x');
    expect(withBase('https://cdn.example.org/a.png')).toBe('https://cdn.example.org/a.png');
    expect(fileHref(null)).toBeUndefined();
    expect(fileHref('/api/files/FA-2?sig=y')).toBe('/api/files/FA-2?sig=y');
    server.use(
      http.get('/api/health', ({ request }) =>
        HttpResponse.json({ auth: request.headers.get('authorization') }),
      ),
    );
    const res = await apiFetch('/api/health', { headers: { Authorization: 'Bearer explicit' } });
    expect(await res.json()).toEqual({ auth: 'Bearer explicit' });
  });
});
