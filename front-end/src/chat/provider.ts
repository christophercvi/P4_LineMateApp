import {
  AbstractChatProvider,
  XRequest,
  useXChat,
  type MessageInfo,
  type SSEOutput,
  type TransformMessage,
  type XRequestOptions,
} from '@ant-design/x-sdk';
import { api, apiFetch, ApiError } from '@/api/client';
import { isFreshConversation, markConversationStarted } from './conversationKeys';
import type {
  AskInput,
  ChatAttachment,
  ChatInterrupt,
  ChatMeta,
  ChatStep,
  LineMateMessage,
  SourceRef,
} from '@/api/types';
import type { ConversationDetailView } from '@/api/views';

/**
 * Adapts the LineMate SSE contract (meta, step, sources, reasoning, content, interrupt, done)
 * to one structured assistant message. Only the three transform* methods are implemented,
 * as the x-chat-provider guidance requires; transport and parsing stay in XRequest / XStream.
 */
export class LineMateChatProvider extends AbstractChatProvider<
  LineMateMessage,
  AskInput,
  SSEOutput
> {
  transformParams(
    requestParams: Partial<AskInput>,
    options: XRequestOptions<AskInput, SSEOutput, LineMateMessage>,
  ): AskInput {
    const prior = this.getMessages().filter((m) => m.content && !m.aborted);
    const last = prior[prior.length - 1];
    if (last?.role === 'user' && last.content === requestParams.question) prior.pop();
    const input = {
      ...(options?.params ?? {}),
      ...requestParams,
      history: prior.slice(-8).map((m) => ({ role: m.role, content: m.content })),
    } as AskInput;
    markConversationStarted(input.conversationId);
    return input;
  }

  transformLocalMessage(requestParams: Partial<AskInput>): LineMateMessage {
    return {
      role: 'user',
      content: requestParams.question ?? '',
      attachments: requestParams.attachments,
      ticketId: requestParams.ticketId,
    };
  }

  transformMessage(info: TransformMessage<LineMateMessage, SSEOutput>): LineMateMessage {
    const { originMessage, chunk, status } = info;
    const base: LineMateMessage =
      originMessage?.role === 'assistant'
        ? originMessage
        : { role: 'assistant', content: '', steps: [] };
    if (!chunk?.event) return status === 'success' ? { ...base, done: true } : base;
    let data: unknown = chunk.data;
    try {
      data = chunk.data ? JSON.parse(chunk.data) : undefined;
    } catch {
      /* keep raw string */
    }
    switch (chunk.event.trim()) {
      case 'meta': {
        const meta = data as ChatMeta;
        return { ...base, meta, model: meta.model };
      }
      case 'step': {
        const step = data as ChatStep;
        const steps = [...(base.steps ?? [])];
        const i = steps.findIndex((s) => s.key === step.key);
        if (i >= 0) steps[i] = { ...steps[i], ...step };
        else steps.push(step);
        return { ...base, steps };
      }
      case 'sources':
        return { ...base, sources: data as SourceRef[] };
      case 'reasoning':
        return { ...base, reasoning: (base.reasoning ?? '') + String(data ?? '') };
      case 'content':
        return { ...base, content: base.content + String(data ?? '') };
      case 'interrupt':
        return { ...base, interrupt: data as ChatInterrupt };
      case 'done': {
        const d = data as { elapsed_ms: number; reasoning_ms?: number; run_id?: string };
        return {
          ...base,
          elapsedMs: d.elapsed_ms,
          reasoningMs: d.reasoning_ms,
          runId: d.run_id,
          done: true,
        };
      }
      case 'error':
        return {
          ...base,
          content: `${base.content}\n\n**Error:** ${(data as { message?: string })?.message ?? 'stream failed'}`,
          error: true,
        };
      default:
        return base;
    }
  }
}

const providers = new Map<string, LineMateChatProvider>();

export function providerFor(conversationKey: string) {
  let p = providers.get(conversationKey);
  if (!p) {
    p = new LineMateChatProvider({
      request: XRequest<AskInput, SSEOutput, LineMateMessage>('/api/ask/stream', {
        manual: true,
        fetch: apiFetch as unknown as XRequestOptions<
          AskInput,
          SSEOutput,
          LineMateMessage
        >['fetch'],
      }),
    });
    providers.set(conversationKey, p);
  }
  return p;
}

export function dropProvider(conversationKey: string) {
  providers.delete(conversationKey);
  providers.delete(`${conversationKey}::compare`);
}

type Extra = {
  attachments?: ChatAttachment[];
  model?: string;
  runId?: string;
  reasoning?: string;
  reasoningOn?: boolean;
  reasoningMs?: number;
  sources?: SourceRef[];
  steps?: ChatStep[];
  interrupt?: ChatInterrupt | null;
  error?: string | null;
  aborted?: boolean;
};

/** Rebuild the Bubble list for a saved conversation from the back-end's stored exchange extras. */
export function historyToMessages(
  key: string,
  detail: ConversationDetailView,
): MessageInfo<LineMateMessage>[] {
  return detail.history.map((h, i) => {
    const x = (h.extra ?? {}) as Extra;
    if (h.role === 'user')
      return {
        id: `${key}-${i}`,
        status: 'local',
        message: { role: 'user', content: h.content, attachments: x.attachments },
      };
    const message: LineMateMessage = {
      role: 'assistant',
      content: x.error && !h.content ? `**Error:** ${x.error}` : h.content,
      reasoning: x.reasoning || undefined,
      reasoningMs: x.reasoningMs || undefined,
      steps: x.steps ?? [],
      sources: x.sources ?? [],
      interrupt: x.interrupt ?? undefined,
      model: x.model,
      runId: x.runId,
      meta: x.model
        ? {
            model: x.model,
            reasoning: !!x.reasoningOn,
            thinking: 'toggle',
            memoryTurns: 0,
            hiddenSources: 0,
            runId: x.runId ?? '',
            conversationId: key,
          }
        : undefined,
      done: true,
      aborted: x.aborted,
      error: !!x.error,
    };
    return {
      id: `${key}-${i}`,
      status: x.error ? 'error' : x.aborted ? 'abort' : 'success',
      message,
    };
  });
}

/** Saved conversations live in the back-end; brand-new chats have no record yet (404 → empty). */
export async function loadHistory(
  conversationKey?: string,
): Promise<MessageInfo<LineMateMessage>[]> {
  if (!conversationKey || conversationKey.includes('::') || isFreshConversation(conversationKey))
    return [];
  try {
    const detail = await api<ConversationDetailView>(
      `/api/conversations/${encodeURIComponent(conversationKey)}`,
    );
    return historyToMessages(conversationKey, detail);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return [];
    throw e;
  }
}

export function useLineMateChat(conversationKey: string) {
  return useXChat<LineMateMessage, LineMateMessage, AskInput, SSEOutput>({
    provider: providerFor(conversationKey),
    conversationKey,
    defaultMessages: (info?: { conversationKey?: string }) => loadHistory(info?.conversationKey),
    requestPlaceholder: () => ({ role: 'assistant', content: '', steps: [] }),
    requestFallback: (_params, { error, messageInfo }) => {
      const prev = messageInfo?.message;
      if (error?.name === 'AbortError' || /abort/i.test(String(error?.message))) {
        return {
          ...(prev ?? { role: 'assistant', content: '' }),
          role: 'assistant',
          aborted: true,
          done: true,
        };
      }
      const text = String(error?.message ?? '');
      const forbidden = /\b403\b/.test(text);
      const offline = /failed to fetch|network/i.test(text);
      return {
        role: 'assistant',
        content: forbidden
          ? 'Your account cannot use chat. Service accounts call LineMate through MCP tools.'
          : offline
            ? 'LineMate could not reach the back-end. Check that the API server is running, then try again.'
            : `Something went wrong while answering (${text || 'unknown error'}). Try again.`,
        error: true,
        done: true,
      };
    },
  });
}
