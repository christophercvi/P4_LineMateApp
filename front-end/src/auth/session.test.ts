import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useActor, useAuth } from '@/auth/useAuth';
import { useSession, useUi } from '@/auth/store';
import {
  isFreshConversation,
  markConversationStarted,
  newConversationKey,
} from '@/chat/conversationKeys';
import { loadHistory, historyToMessages } from '@/chat/provider';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/server';
import { makeToken, signIn, USERS } from '@/test/users';

describe('session store', () => {
  it('decodes JWT claims on login and clears them on logout', () => {
    useSession.getState().login(makeToken(USERS.sous));
    const { claims, token } = useSession.getState();
    expect(token).toBeTruthy();
    expect(claims).toMatchObject({
      sub: 'u-priya',
      role: 'sous_chef',
      station: 'grill',
      crew: 'CM-02',
      name: 'Priya Nair',
    });
    expect(claims!.exp).toBeGreaterThan(Date.now() / 1000);
    useSession.getState().logout();
    expect(useSession.getState()).toMatchObject({ token: null, claims: null });
  });

  it('persists the session and UI preferences to localStorage', () => {
    signIn('cook');
    expect(JSON.parse(localStorage.getItem('linemate-session')!).state.claims.username).toBe(
      'marco',
    );
    useUi.getState().toggleMode();
    useUi.getState().setSiderCollapsed(true);
    expect(JSON.parse(localStorage.getItem('linemate-ui')!).state).toMatchObject({
      mode: 'dark',
      siderCollapsed: true,
    });
  });

  it('maps claims to an actor for permission checks', () => {
    const { result } = renderHook(() => useAuth());
    expect(result.current.actor).toBeNull();
    expect(() => renderHook(() => useActor())).toThrow(/outside an authenticated route/);
    act(() => signIn('manager'));
    expect(result.current.actor).toEqual({
      role: 'kitchen_manager',
      station: null,
      crewMemberId: 'CM-11',
      userId: 'u-elena',
    });
  });
});

describe('conversation keys and history', () => {
  it('skips the history request for a freshly minted conversation until it is started', async () => {
    let calls = 0;
    server.use(
      http.get('/api/conversations/:id', ({ params }) => {
        calls += 1;
        return HttpResponse.json({
          id: params.id,
          title: 'Cooling chili',
          history: [
            { role: 'user', content: 'How do I cool chili?', extra: {} },
            {
              role: 'assistant',
              content: 'Use shallow pans [1].',
              extra: {
                model: 'llama3.2:3b',
                runId: 'RUN-0042',
                sources: [{ docId: 'DOC-SOP-003' }],
                reasoning: 'Check SOP',
              },
            },
          ],
        });
      }),
    );
    const key = newConversationKey();
    expect(key).toMatch(/^c-[a-z0-9]+-[a-z0-9]{6}$/);
    expect(isFreshConversation(key)).toBe(true);
    expect(await loadHistory(key)).toEqual([]);
    expect(calls).toBe(0);
    markConversationStarted(key);
    const msgs = await loadHistory(key);
    expect(calls).toBe(1);
    expect(msgs).toHaveLength(2);
    expect(msgs[1]).toMatchObject({
      status: 'success',
      message: { role: 'assistant', model: 'llama3.2:3b', runId: 'RUN-0042', done: true },
    });
    expect(await loadHistory(`${key}::compare`)).toEqual([]);
  });

  it('treats a missing conversation as empty and marks failed exchanges as errors', async () => {
    expect(await loadHistory('c-unknown-abcdef')).toEqual([]);
    const msgs = historyToMessages('c-1', {
      id: 'c-1',
      title: 't',
      history: [{ role: 'assistant', content: '', extra: { error: 'model offline' } }],
    } as never);
    expect(msgs[0]).toMatchObject({
      status: 'error',
      message: { content: '**Error:** model offline', error: true },
    });
  });
});
