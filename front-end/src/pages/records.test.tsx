import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { fixtures } from '@/test/handlers';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

describe('tickets', () => {
  it('lists tickets with priority and status', async () => {
    renderApp('/tickets', { as: 'sous' });
    expect((await screen.findAllByText('TKT-017')).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Blast chiller not pulling down/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Hood suppression inspection overdue/).length).toBeGreaterThan(0);
  });

  it('opens a ticket with attachments, the ownership-mismatch warning and its comment thread', async () => {
    renderApp('/tickets/TKT-017', { as: 'sous' });
    expect(await screen.findByText(fixtures.ticketDetail.ticket.title)).toBeInTheDocument();
    expect(screen.getByText('Ownership mismatch')).toBeInTheDocument();
    expect(screen.getByText(/blast-chiller-e4\.jpg/)).toBeInTheDocument();
    expect(screen.getByText(/chiller-service-log\.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/Attachments \(2\)/)).toBeInTheDocument();
  });

  it('posts a comment to the ticket', async () => {
    const user = userEvent.setup();
    let posted: unknown = null;
    server.use(
      http.post('/api/tickets/:id/comments', async ({ request, params }) => {
        posted = { id: params.id, ...((await request.json()) as object) };
        return HttpResponse.json(
          {
            id: 'CMT-9002',
            ticketId: params.id,
            authorId: 'CM-02',
            body: 'Tech booked for 7 AM.',
            parentId: null,
            createdAt: new Date().toISOString(),
          },
          { status: 201 },
        );
      }),
    );
    renderApp('/tickets/TKT-017', { as: 'sous' });
    await user.type(await screen.findByPlaceholderText(/Add an update/), 'Tech booked for 7 AM.');
    await user.click(screen.getByRole('button', { name: /Post/ }));
    await waitFor(() => expect(posted).toEqual({ id: 'TKT-017', body: 'Tech booked for 7 AM.' }));
  });

  it('creates a ticket from the drawer and opens it', async () => {
    const user = userEvent.setup();
    let created: Record<string, unknown> | null = null;
    server.use(
      http.post('/api/tickets', async ({ request }) => {
        created = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...fixtures.tickets[0], id: 'TKT-043', title: created.title },
          { status: 201 },
        );
      }),
    );
    const { router } = renderApp('/tickets', { as: 'cook' });
    await user.click((await screen.findAllByRole('button', { name: /New ticket/ }))[0]);
    const drawer = await screen.findByRole('dialog');
    await user.type(within(drawer).getByLabelText('Title'), 'Reach-in door gasket torn');
    await user.type(
      within(drawer).getByLabelText('What happened?'),
      'Bottom-left gasket split; door will not seal.',
    );
    await user.click(within(drawer).getByRole('button', { name: /Create ticket/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tickets/TKT-043'));
    expect(created).toMatchObject({
      title: 'Reach-in door gasket torn',
      description: 'Bottom-left gasket split; door will not seal.',
      station: 'grill',
      tags: [],
    });
  });

  it('lets the kitchen manager resolve a ticket', async () => {
    const user = userEvent.setup();
    let patched: unknown = null;
    const all = {
      changeStatus: true,
      lowerOrClose: true,
      raise: true,
      assign: true,
      comment: true,
      attach: true,
    };
    server.use(
      http.get('/api/tickets/:id', () =>
        HttpResponse.json({ ...fixtures.ticketDetail, permissions: all }),
      ),
      http.patch('/api/tickets/:id', async ({ request, params }) => {
        patched = { id: params.id, ...((await request.json()) as object) };
        return HttpResponse.json({ ...fixtures.ticketDetail.ticket, status: 'resolved' });
      }),
    );
    renderApp('/tickets/TKT-017', { as: 'manager' });
    await screen.findByText(fixtures.ticketDetail.ticket.title);
    const manage = screen.getByText('Manage').closest('.ant-card') as HTMLElement;
    const [status] = within(manage).getAllByRole('combobox');
    await user.click(status);
    const resolved = screen
      .getAllByTitle('Resolved')
      .find((el) => el.classList.contains('ant-select-item-option'))!;
    await user.click(resolved);
    await waitFor(() => expect(patched).toEqual({ id: 'TKT-017', status: 'resolved' }));
  });

  it('keeps status and assignment read-only for a sous chef outside the ticket station', async () => {
    renderApp('/tickets/TKT-017', { as: 'sous' });
    await screen.findByText(fixtures.ticketDetail.ticket.title);
    const manage = screen.getByText('Manage').closest('.ant-card') as HTMLElement;
    const [status, priority, assignee] = within(manage).getAllByRole('combobox');
    expect(status).toBeDisabled();
    expect(assignee).toBeDisabled();
    expect(priority).toBeEnabled();
  });

  it('shows a not-found state for an unknown ticket', async () => {
    server.use(
      http.get('/api/tickets/:id', () =>
        HttpResponse.json({ detail: 'Ticket not found' }, { status: 404 }),
      ),
    );
    renderApp('/tickets/TKT-999', { as: 'cook' });
    expect(
      await screen.findByText('Ticket not found', { selector: '.ant-result-title' }),
    ).toBeInTheDocument();
  });
});

describe('documents', () => {
  it('lists the role-visible knowledge base', async () => {
    renderApp('/documents', { as: 'cook' });
    expect((await screen.findAllByText('Opening & Closing Checklists')).length).toBeGreaterThan(0);
    // Line cooks never receive incident reports from the API.
    expect(fixtures.documentsCook.items.some((d) => d.category === 'incident')).toBe(false);
    expect(screen.getByRole('button', { name: /Upload document/ })).toBeDisabled();
  });

  it('shows a document with its review status and indexed chunks', async () => {
    renderApp('/documents/DOC-SOP-003', { as: 'cook' });
    expect(
      (await screen.findAllByText('Cooling Hot Foods (Two-Stage Cooling)')).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByText(/Last reviewed \d+ days ago — over the 180-day review policy/),
    ).toBeInTheDocument();
  });

  it('only offers a sous chef the non-SOP categories for their own station', async () => {
    const user = userEvent.setup();
    renderApp('/documents', { as: 'sous' });
    await user.click(await screen.findByRole('button', { name: /Upload document/ }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('SOP is disabled for Sous Chefs')).toBeInTheDocument();
    await user.click(within(drawer).getByLabelText('Category'));
    const option = (title: string) =>
      screen.getAllByTitle(title).find((el) => el.classList.contains('ant-select-item-option'))!;
    await waitFor(() => expect(option('SOP')).toHaveClass('ant-select-item-option-disabled'));
    expect(option('Recipe')).not.toHaveClass('ant-select-item-option-disabled');
  });

  it('uploads a document as multipart and reports the ingestion job', async () => {
    const user = userEvent.setup();
    let fields: Record<string, string> = {};
    server.use(
      http.post('/api/documents', async ({ request }) => {
        const raw = await request.text();
        fields = Object.fromEntries(
          [...raw.matchAll(/name="([^"]+)"\r\n\r\n([^\r]*)/g)].map((m) => [m[1], m[2]]),
        );
        return HttpResponse.json(
          {
            document: {
              ...fixtures.documentsSous.items[0],
              id: 'DOC-ONB-009',
              title: fields.title,
            },
            job: {
              ...fixtures.ingestJobs[0],
              id: 'JOB-0099',
              status: 'ready',
              docId: 'DOC-ONB-009',
            },
          },
          { status: 201 },
        );
      }),
      http.get('/api/ingest/jobs/:id', () =>
        HttpResponse.json({
          ...fixtures.ingestJobs[0],
          id: 'JOB-0099',
          status: 'ready',
          docId: 'DOC-ONB-009',
        }),
      ),
    );
    renderApp('/documents', { as: 'sous' });
    await user.click(await screen.findByRole('button', { name: /Upload document/ }));
    const drawer = await screen.findByRole('dialog');
    const input = drawer.querySelector('input[type=file]') as HTMLInputElement;
    await user.upload(
      input,
      new File(['%PDF-1.7 fry station'], 'fry-station-quick-reference.pdf', {
        type: 'application/pdf',
      }),
    );
    await user.clear(within(drawer).getByLabelText('Title'));
    await user.type(within(drawer).getByLabelText('Title'), 'Fry Station Quick Reference');
    await user.click(within(drawer).getByLabelText('Category'));
    await user.click(await screen.findByTitle('Onboarding'));
    await user.click(within(drawer).getByRole('button', { name: /^Upload/ }));
    await waitFor(() => expect(fields.title).toBe('Fry Station Quick Reference'));
    expect(fields).toMatchObject({ category: 'onboarding', station: 'grill' });
  });
});
