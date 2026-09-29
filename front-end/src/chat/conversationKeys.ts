/**
 * Conversation keys are minted in the browser. A freshly minted key has no server-side history yet,
 * so the chat hook skips the history request for it until the first question is sent.
 */
const fresh = new Set<string>();

export function newConversationKey(): string {
  const key = `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  fresh.add(key);
  return key;
}

export const isFreshConversation = (key: string) => fresh.has(key);

export function markConversationStarted(key: string | undefined) {
  if (key) fresh.delete(key);
}
