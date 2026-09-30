import { http, HttpResponse } from 'msw';
import adminModels from './fixtures/admin_models.json';
import adminUsers from './fixtures/admin_users.json';
import approvals from './fixtures/approvals.json';
import auditOwnership from './fixtures/audit_ownership.json';
import auditStale from './fixtures/audit_stale.json';
import dashboardCook from './fixtures/dashboard_cook.json';
import dashboardManager from './fixtures/dashboard_manager.json';
import documentDetail from './fixtures/document_detail.json';
import documentsCook from './fixtures/documents_cook.json';
import documentsSous from './fixtures/documents_sous.json';
import ingestJobs from './fixtures/ingest_jobs.json';
import lookups from './fixtures/lookups.json';
import mcp from './fixtures/mcp.json';
import mcpAdmin from './fixtures/mcp_admin.json';
import models from './fixtures/models.json';
import runs from './fixtures/runs.json';
import stations from './fixtures/stations.json';
import ticketDetail from './fixtures/ticket_detail.json';
import tickets from './fixtures/tickets.json';
import vectorStore from './fixtures/vector_store.json';
import workload from './fixtures/workload.json';
import { USERS, makeToken, roleOf } from './users';

export const fixtures = {
  adminModels,
  adminUsers,
  approvals,
  auditOwnership,
  auditStale,
  dashboardCook,
  dashboardManager,
  documentDetail,
  documentsCook,
  documentsSous,
  ingestJobs,
  lookups,
  mcp,
  mcpAdmin,
  models,
  runs,
  stations,
  ticketDetail,
  tickets,
  vectorStore,
  workload,
};

/** Build a text/event-stream body from (event, data) pairs, exactly as the FastAPI SSE endpoints do. */
export function sse(events: [string, unknown][]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const [event, data] of events) {
        controller.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
      }
      controller.close();
    },
  });
}

export const sseResponse = (events: [string, unknown][]) =>
  new HttpResponse(sse(events), { headers: { 'Content-Type': 'text/event-stream' } });

export const handlers = [
  http.get('/api/health', () => HttpResponse.json({ status: 'ok' })),
  http.get('/api/auth/stations', () => HttpResponse.json(stations)),
  http.post('/api/auth/login', async ({ request }) => {
    const { username, password } = (await request.json()) as { username: string; password: string };
    const user = Object.values(USERS).find((u) => u.username === username);
    if (!user || password !== 'Hearthline#2026') {
      return HttpResponse.json({ detail: 'Incorrect username or password' }, { status: 401 });
    }
    return HttpResponse.json({
      token: makeToken(user),
      tokenType: 'bearer',
      expiresIn: 28800,
      user,
    });
  }),
  http.post('/api/auth/register', async ({ request }) => {
    const body = (await request.json()) as {
      displayName: string;
      username: string;
      station: string;
      email: string;
    };
    if (body.username === 'marco')
      return HttpResponse.json({ detail: 'That username is taken' }, { status: 409 });
    const user = {
      sub: `u-${body.username}`,
      username: body.username,
      name: body.displayName,
      role: 'line_cook' as const,
      station: body.station as 'grill',
      crew: null,
    };
    return HttpResponse.json(
      { token: makeToken(user), tokenType: 'bearer', expiresIn: 28800, user },
      { status: 201 },
    );
  }),
  http.get('/api/lookups', () => HttpResponse.json(lookups)),
  http.get('/api/dashboard', ({ request }) =>
    HttpResponse.json(roleOf(request) === 'line_cook' ? dashboardCook : dashboardManager),
  ),
  http.get('/api/tickets', () => HttpResponse.json(tickets)),
  http.get('/api/tickets/:id', () => HttpResponse.json(ticketDetail)),
  http.patch('/api/tickets/:id', async ({ request }) =>
    HttpResponse.json({ ...ticketDetail.ticket, ...((await request.json()) as object) }),
  ),
  http.post('/api/tickets/:id/comments', async ({ request, params }) => {
    const body = (await request.json()) as { body: string; parentId?: string };
    return HttpResponse.json(
      {
        id: 'CMT-9001',
        ticketId: params.id,
        authorId: 'CM-02',
        body: body.body,
        parentId: body.parentId ?? null,
        createdAt: new Date().toISOString(),
      },
      { status: 201 },
    );
  }),
  http.get('/api/documents', ({ request }) =>
    HttpResponse.json(roleOf(request) === 'line_cook' ? documentsCook : documentsSous),
  ),
  http.get('/api/documents/:id', () => HttpResponse.json(documentDetail)),
  http.get('/api/ingest/jobs', () => HttpResponse.json(ingestJobs)),
  http.get('/api/approvals', () => HttpResponse.json(approvals)),
  http.get('/api/models', () => HttpResponse.json(models)),
  http.get('/api/admin/models', () => HttpResponse.json(adminModels)),
  http.get('/api/admin/users', () => HttpResponse.json(adminUsers)),
  http.get('/api/admin/vector-store', () => HttpResponse.json(vectorStore)),
  http.get('/api/mcp', ({ request }) =>
    HttpResponse.json(roleOf(request) === 'admin' ? mcpAdmin : mcp),
  ),
  http.get('/api/audits/stale', () => HttpResponse.json(auditStale)),
  http.get('/api/audits/ownership', () => HttpResponse.json(auditOwnership)),
  http.get('/api/analytics/workload', () => HttpResponse.json(workload)),
  http.get('/api/agent/runs', () => HttpResponse.json(runs)),
  http.get('/api/conversations', () => HttpResponse.json([])),
  http.get('/api/conversations/:id', () =>
    HttpResponse.json({ detail: 'Conversation not found' }, { status: 404 }),
  ),
];
