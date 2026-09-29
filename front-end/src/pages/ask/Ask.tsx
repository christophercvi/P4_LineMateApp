import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  App,
  Avatar,
  Button,
  Card,
  Flex,
  Input,
  Modal,
  Select,
  Switch,
  Tag,
  Tooltip,
  Typography,
  type GetProp,
  type UploadFile,
} from 'antd';
import {
  AppstoreAddOutlined,
  BulbOutlined,
  CameraOutlined,
  DeleteOutlined,
  EditOutlined,
  FireOutlined,
  HistoryOutlined,
  MessageOutlined,
  PaperClipOutlined,
  PlusOutlined,
  RedoOutlined,
  SafetyCertificateOutlined,
  SplitCellsOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons';
import {
  Actions,
  Attachments,
  Bubble,
  Conversations,
  Prompts,
  Sender,
  Suggestion,
  Welcome,
  type BubbleItemType,
} from '@ant-design/x';
import { useXConversations, type MessageInfo } from '@ant-design/x-sdk';
import dayjs from 'dayjs';
import { api, ApiError, fileHref } from '@/api/client';
import type { AskInput, ChatAttachment, LineMateMessage, ModelInfo } from '@/api/types';
import type { ConversationView, MyModelsView } from '@/api/views';
import { useActor } from '@/auth/useAuth';
import { ROLE_LABEL } from '@/auth/permissions';
import { AssistantMessage } from '@/chat/AssistantMessage';
import { dropProvider, useLineMateChat } from '@/chat/provider';
import { LOGO_URL } from '@/theme/tokens';
import { STARTER_PROMPTS } from './prompts';
import { newConversationKey } from '@/chat/conversationKeys';

const groupOf = (iso: string) =>
  dayjs(iso).isSame(dayjs(), 'day')
    ? 'Today'
    : dayjs(iso).isAfter(dayjs().subtract(7, 'day'))
      ? 'This week'
      : 'Earlier';

type AttachmentItem = GetProp<typeof Attachments, 'items'>[number];

const PROMPT_ICONS = [
  <FireOutlined key="a" />,
  <SafetyCertificateOutlined key="b" />,
  <ThunderboltOutlined key="c" />,
  <BulbOutlined key="d" />,
  <AppstoreAddOutlined key="e" />,
  <MessageOutlined key="f" />,
];

function reasoningRule(m: ModelInfo | undefined) {
  if (!m) return { disabled: true, locked: false, tip: '' };
  if (m.thinking === 'none')
    return {
      disabled: true,
      locked: false,
      tip: `${m.name} has no “thinking” capability — Ollama would reject think=true.`,
    };
  if (m.thinking === 'always')
    return {
      disabled: true,
      locked: true,
      tip: `${m.name} always reasons; think=false is ignored, so the toggle is locked on.`,
    };
  return {
    disabled: false,
    locked: false,
    tip: `${m.name} is hybrid: think=true streams a reasoning trace, think=false skips it.`,
  };
}

function ChatColumn({
  messages,
  onRetry,
  compact = false,
  label,
}: {
  messages: MessageInfo<LineMateMessage>[];
  onRetry: (id: string | number) => void;
  compact?: boolean;
  label?: string;
}) {
  const [feedback, setFeedback] = useState<Record<string, 'like' | 'dislike' | 'default'>>({});
  const items: BubbleItemType[] = messages.map((m) => ({
    key: m.id,
    role: m.message.role === 'user' ? 'user' : 'ai',
    content: m.message,
    status: m.status,
    loading: m.status === 'loading',
  }));
  return (
    <Flex vertical style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
      {label && (
        <Tag color="volcano" style={{ alignSelf: 'flex-start', marginBottom: 8 }}>
          {label}
        </Tag>
      )}
      <Bubble.List
        autoScroll
        style={{ flex: 1, minHeight: 0 }}
        items={items}
        role={{
          ai: {
            placement: 'start',
            variant: 'outlined',
            avatar: <Avatar src={LOGO_URL} shape="square" />,
            styles: { content: { maxWidth: compact ? '100%' : 760, width: '100%' } },
            contentRender: (content: LineMateMessage, info) => (
              <AssistantMessage
                msg={content}
                streaming={info.status === 'updating' || info.status === 'loading'}
              />
            ),
            footer: (content: LineMateMessage, info) =>
              content.done && !content.error ? (
                <Flex gap={4} align="center">
                  <Actions.Copy text={content.content} />
                  <Actions.Feedback
                    value={feedback[String(info.key)] ?? 'default'}
                    onChange={(v) => setFeedback((f) => ({ ...f, [String(info.key)]: v }))}
                  />
                  <Actions
                    items={[
                      {
                        key: 'retry',
                        label: 'Retry',
                        icon: <RedoOutlined />,
                        onItemClick: () => info.key !== undefined && onRetry(info.key),
                      },
                    ]}
                  />
                  {content.runId && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      run {content.runId}
                    </Typography.Text>
                  )}
                </Flex>
              ) : null,
          },
          user: {
            placement: 'end',
            variant: 'filled',
            avatar: <Avatar icon={<UserOutlined />} />,
            contentRender: (content: LineMateMessage) => (
              <Flex vertical gap={6}>
                {content.attachments?.map((a) =>
                  a.url ? (
                    <img
                      key={a.assetId ?? a.name}
                      src={fileHref(a.url)}
                      alt={a.name}
                      style={{ maxWidth: 220, borderRadius: 8 }}
                    />
                  ) : (
                    <Tag key={a.name} icon={<PaperClipOutlined />}>
                      {a.name}
                    </Tag>
                  ),
                )}
                {content.ticketId && (
                  <Tag color="orange" style={{ alignSelf: 'flex-start' }}>
                    context: {content.ticketId}
                  </Tag>
                )}
                <span style={{ whiteSpace: 'pre-wrap' }}>{content.content}</span>
              </Flex>
            ),
          },
        }}
      />
    </Flex>
  );
}

export default function Ask() {
  const actor = useActor();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const mine = useQuery({
    queryKey: ['models-mine'],
    queryFn: () => api<MyModelsView>('/api/models'),
  });
  const [model, setModel] = useState<string>();
  const [compareModel, setCompareModel] = useState<string>();
  const [reasoning, setReasoning] = useState(true);
  const [compare, setCompare] = useState(false);
  const [value, setValue] = useState('');
  const [attachOpen, setAttachOpen] = useState(false);
  const [files, setFiles] = useState<AttachmentItem[]>([]);
  const [uploaded, setUploaded] = useState<Record<string, ChatAttachment>>({});
  const [renaming, setRenaming] = useState<{ key: string; label: string } | null>(null);
  const senderRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  const { message } = App.useApp();
  const firstKey = useMemo(() => newConversationKey(), []);
  const saved = useQuery({
    queryKey: ['conversations'],
    queryFn: () => api<ConversationView[]>('/api/conversations'),
  });
  const {
    conversations,
    activeConversationKey,
    setActiveConversationKey,
    addConversation,
    removeConversation,
    setConversation,
    setConversations,
  } = useXConversations({
    defaultConversations: [{ key: firstKey, label: 'New chat', group: 'Today' }],
    defaultActiveConversationKey: firstKey,
  });
  const synced = useRef(false);
  useEffect(() => {
    if (!saved.data || synced.current) return;
    synced.current = true;
    setConversations([
      { key: firstKey, label: 'New chat', group: 'Today' },
      ...saved.data
        .filter((c) => c.id !== firstKey)
        .map((c) => ({ key: c.id, label: c.title, group: groupOf(c.updatedAt) })),
    ]);
  }, [saved.data, firstKey, setConversations]);
  const primary = useLineMateChat(activeConversationKey);
  const secondary = useLineMateChat(`${activeConversationKey}::compare`);

  const models = mine.data?.models ?? [];
  // Any model Ollama reports as able to complete text can chat; multimodal models such as gemma4 stay in the list.
  const chatModels = models.filter(
    (m) => m.capabilities.includes('completion') && !m.capabilities.includes('embedding'),
  );
  const current = model ?? mine.data?.default ?? undefined;
  const currentInfo = models.find((m) => m.name === current);
  const rule = reasoningRule(currentInfo);
  const effectiveReasoning = rule.locked ? true : rule.disabled ? false : reasoning;
  const isAdmin = actor.role === 'admin';
  const memoryTurns = primary.messages.filter((m) => m.message.role === 'user').length;
  const busy = primary.isRequesting || (compare && secondary.isRequesting);

  useEffect(() => {
    if (!compareModel && chatModels.length > 1)
      setCompareModel(chatModels.find((m) => m.name !== current)?.name);
  }, [chatModels, compareModel, current]);

  const submit = (question: string, extra: Partial<AskInput> = {}) => {
    if (!question.trim()) return;
    const attachments = files.map((f) => uploaded[f.uid]).filter(Boolean) as ChatAttachment[];
    const base = {
      question: question.trim(),
      reasoning: effectiveReasoning,
      conversationId: activeConversationKey,
      ...(attachments.length ? { attachments } : {}),
      ...extra,
    };
    if (!current) {
      message.warning('No chat model is available. Ask an Admin to pull one in Ollama.');
      return;
    }
    primary.onRequest({ ...base, model: current });
    if (compare && isAdmin && compareModel) {
      const ci = models.find((m) => m.name === compareModel);
      const cr = reasoningRule(ci);
      secondary.onRequest({
        ...base,
        model: compareModel,
        reasoning: cr.locked ? true : cr.disabled ? false : reasoning,
      });
    }
    const conv = conversations.find((c) => c.key === activeConversationKey);
    if (conv && conv.label === 'New chat')
      setConversation(activeConversationKey, { ...conv, label: question.trim().slice(0, 42) });
    setValue('');
    setFiles([]);
    setUploaded({});
    setAttachOpen(false);
    setTimeout(() => qc.invalidateQueries({ queryKey: ['conversations'] }), 1500);
  };

  const uploadPhoto = async (file: File, uid: string) => {
    const preview = URL.createObjectURL(file);
    setFiles((prev) => [
      ...prev.filter((f) => f.uid !== uid),
      {
        uid,
        name: file.name,
        size: file.size,
        status: 'uploading',
        thumbUrl: preview,
      } as UploadFile as AttachmentItem,
    ]);
    const form = new FormData();
    form.append('file', file);
    form.append('conversationId', activeConversationKey);
    try {
      const res = await api<{ assetId: string; name: string; url: string; bytes: number }>(
        '/api/chat/uploads',
        { form },
      );
      setUploaded((u) => ({
        ...u,
        [uid]: { name: res.name, url: res.url, bytes: res.bytes, assetId: res.assetId },
      }));
      setFiles((prev) =>
        prev.map((f) => (f.uid === uid ? { ...f, status: 'done', url: fileHref(res.url) } : f)),
      );
    } catch (e) {
      setFiles((prev) => prev.map((f) => (f.uid === uid ? { ...f, status: 'error' } : f)));
      message.error(e instanceof ApiError ? e.message : 'Photo upload failed');
    }
  };

  // Deep links: /ask?q=...&ticket=TKT-017 (from the ticket page) auto-send once.
  const sentRef = useRef(false);
  useEffect(() => {
    const q = params.get('q');
    if (!q || sentRef.current || !mine.data) return;
    sentRef.current = true;
    const ticket = params.get('ticket') ?? undefined;
    submit(q, ticket ? { ticketId: ticket } : {});
    setParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, mine.data]);

  const retry = (chat: typeof primary, chatModel: string | undefined) => (id: string | number) => {
    const idx = chat.messages.findIndex((m) => m.id === id);
    const user = [...chat.messages.slice(0, idx)].reverse().find((m) => m.message.role === 'user');
    if (!user) return;
    if (!chatModel) return;
    chat.onReload(id, {
      question: user.message.content,
      model: chatModel,
      reasoning: effectiveReasoning,
      conversationId: activeConversationKey,
      attachments: user.message.attachments,
      ticketId: user.message.ticketId,
    });
  };

  const newChat = () => {
    const key = newConversationKey();
    addConversation({ key, label: 'New chat', group: 'Today' }, 'prepend');
    setActiveConversationKey(key);
  };

  const prompts = useMemo(
    () => STARTER_PROMPTS.map((p, i) => ({ ...p, icon: PROMPT_ICONS[i % PROMPT_ICONS.length] })),
    [],
  );

  const removeChat = async (key: string) => {
    try {
      await api(`/api/conversations/${encodeURIComponent(key)}`, { method: 'DELETE' });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        message.error(e instanceof ApiError ? e.message : 'Could not delete the chat');
        return;
      }
    }
    removeConversation(key);
    dropProvider(key);
    if (key === activeConversationKey)
      setActiveConversationKey(conversations.find((c) => c.key !== key)?.key ?? '');
    qc.invalidateQueries({ queryKey: ['conversations'] });
  };

  const renameChat = async () => {
    if (!renaming?.label.trim()) return;
    const conv = conversations.find((c) => c.key === renaming.key);
    try {
      await api(`/api/conversations/${encodeURIComponent(renaming.key)}`, {
        method: 'PATCH',
        body: { title: renaming.label.trim() },
      });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404))
        message.error(e instanceof ApiError ? e.message : 'Rename failed');
    }
    if (conv) setConversation(renaming.key, { ...conv, label: renaming.label.trim() });
    setRenaming(null);
  };

  const empty = primary.messages.length === 0;

  const senderHeader = (
    <Sender.Header
      title="Ask about a photo"
      open={attachOpen}
      onOpenChange={setAttachOpen}
      styles={{ content: { padding: 0 } }}
    >
      <Attachments
        accept=".png,.jpg,.jpeg"
        items={files}
        beforeUpload={(file) => {
          void uploadPhoto(file, file.uid);
          return false;
        }}
        onRemove={(file) => {
          setFiles((prev) => prev.filter((f) => f.uid !== file.uid));
          setUploaded(({ [file.uid]: _gone, ...rest }) => rest);
          return true;
        }}
        placeholder={(type) =>
          type === 'drop'
            ? { title: 'Drop the photo here' }
            : {
                icon: <CameraOutlined />,
                title: 'Upload a photo',
                description:
                  'e.g. an equipment display or a label. LineMate reads any text on it, compares it with photos on file and matches it to SOPs.',
              }
        }
        getDropContainer={() => senderRef.current}
      />
    </Sender.Header>
  );

  const footer = (
    <Flex justify="space-between" align="center" wrap gap={8}>
      <Flex gap={8} align="center" wrap>
        <Tooltip
          title={
            mine.data?.vision
              ? 'Attach a photo'
              : 'Photo search is unavailable: the vision embedding model is not loaded on the server'
          }
        >
          <Button
            type="text"
            icon={<PaperClipOutlined />}
            disabled={!mine.data?.vision}
            onClick={() => setAttachOpen((v) => !v)}
            aria-label="Attach photo"
          />
        </Tooltip>
        <Select
          size="small"
          value={current}
          onChange={setModel}
          style={{ width: 190 }}
          loading={mine.isLoading}
          placeholder={mine.data && !chatModels.length ? 'No models available' : 'Model'}
          options={chatModels.map((m) => ({ value: m.name, label: m.name }))}
          popupMatchSelectWidth={false}
          aria-label="Model"
        />
        <Tooltip title={rule.tip}>
          <Flex gap={6} align="center">
            <Switch
              size="small"
              checked={effectiveReasoning}
              disabled={rule.disabled}
              onChange={setReasoning}
              aria-label="Reasoning"
            />
            <Typography.Text
              type={rule.disabled ? 'secondary' : undefined}
              style={{ fontSize: 13 }}
            >
              Reasoning{rule.locked ? ' (always on)' : ''}
            </Typography.Text>
          </Flex>
        </Tooltip>
        <Tooltip title="Earlier turns in this chat are sent with each question">
          <Tag icon={<HistoryOutlined />} variant="outlined">
            Memory: {memoryTurns} turn{memoryTurns === 1 ? '' : 's'}
          </Tag>
        </Tooltip>
        {isAdmin && (
          <Flex gap={6} align="center">
            <Switch
              size="small"
              checked={compare}
              onChange={setCompare}
              aria-label="Compare models"
            />
            <Typography.Text style={{ fontSize: 13 }}>
              <SplitCellsOutlined /> Compare
            </Typography.Text>
            {compare && (
              <Select
                size="small"
                value={compareModel}
                onChange={setCompareModel}
                style={{ width: 170 }}
                options={chatModels
                  .filter((m) => m.name !== current)
                  .map((m) => ({ value: m.name, label: m.name }))}
              />
            )}
          </Flex>
        )}
      </Flex>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Type “/” for shortcuts · answers cite the knowledge base
      </Typography.Text>
    </Flex>
  );

  return (
    <Flex gap={16} style={{ height: 'calc(100vh - 112px)', minHeight: 560 }}>
      <Card
        style={{ width: 260, flex: 'none' }}
        styles={{ body: { padding: 8, height: '100%', display: 'flex', flexDirection: 'column' } }}
        className="lm-hide-mobile"
      >
        <Button
          type="primary"
          ghost
          icon={<PlusOutlined />}
          block
          onClick={newChat}
          style={{ marginBottom: 8 }}
        >
          New chat
        </Button>
        <Conversations
          style={{ flex: 1, overflowY: 'auto', padding: 0 }}
          items={conversations.map((c) => ({
            key: c.key,
            label: c.label as string,
            group: c.group as string,
            icon: <MessageOutlined />,
          }))}
          activeKey={activeConversationKey}
          onActiveChange={(k) => setActiveConversationKey(String(k))}
          groupable
          menu={(conv) => ({
            items: [
              { key: 'rename', label: 'Rename', icon: <EditOutlined /> },
              {
                key: 'delete',
                label: 'Delete',
                icon: <DeleteOutlined />,
                danger: true,
                disabled: conversations.length <= 1,
              },
            ],
            onClick: ({ key }) => {
              if (key === 'rename') setRenaming({ key: conv.key, label: String(conv.label ?? '') });
              if (key === 'delete') void removeChat(conv.key);
            },
          })}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12, padding: 8 }}>
          Signed in as {ROLE_LABEL[actor.role]} — answers only use documents your role can read.
        </Typography.Text>
      </Card>

      <Card
        style={{ flex: 1, minWidth: 0 }}
        styles={{
          body: { height: '100%', display: 'flex', flexDirection: 'column', gap: 12, padding: 16 },
        }}
      >
        {empty ? (
          <Flex
            vertical
            gap={24}
            style={{ flex: 1, overflowY: 'auto', padding: '24px 8px' }}
            align="center"
          >
            <Welcome
              variant="borderless"
              icon={<Avatar src={LOGO_URL} shape="square" size={56} />}
              title="Ask LineMate"
              description="Answers come from your kitchen’s SOPs, recipes and onboarding docs, with citations and a warning when a source is past its review date."
              style={{ maxWidth: 760 }}
            />
            <Prompts
              title="Try one of these"
              items={prompts}
              wrap
              styles={{ item: { flex: '1 1 300px', maxWidth: 370 } }}
              style={{ maxWidth: 760 }}
              onItemClick={({ data }) => submit(String(data.label))}
            />
          </Flex>
        ) : compare && isAdmin ? (
          <Flex gap={16} style={{ flex: 1, minHeight: 0 }}>
            <ChatColumn
              messages={primary.messages}
              onRetry={retry(primary, current)}
              compact
              label={current}
            />
            <ChatColumn
              messages={secondary.messages}
              onRetry={retry(secondary, compareModel)}
              compact
              label={compareModel}
            />
          </Flex>
        ) : (
          <ChatColumn messages={primary.messages} onRetry={retry(primary, current)} />
        )}

        <div ref={senderRef}>
          <Suggestion
            items={[
              {
                label: '/triage — run shift triage',
                value: '/triage',
                icon: <ThunderboltOutlined />,
              },
              {
                label: '/stale — open the stale-document audit',
                value: '/audits/stale',
                icon: <SafetyCertificateOutlined />,
              },
              {
                label: '/workload — station workload analytics',
                value: '/analytics',
                icon: <AppstoreAddOutlined />,
              },
            ]}
            onSelect={(v) => {
              setValue('');
              navigate(v);
            }}
          >
            {({ onTrigger, onKeyDown }) => (
              <Sender
                value={value}
                onChange={(v) => {
                  if (v === '/') onTrigger();
                  else if (!v.startsWith('/')) onTrigger(false);
                  setValue(v);
                }}
                onKeyDown={onKeyDown}
                onSubmit={(v) => submit(v)}
                onCancel={() => {
                  primary.abort();
                  secondary.abort();
                }}
                loading={busy}
                header={senderHeader}
                footer={footer}
                onPasteFile={(list) => {
                  if (!mine.data?.vision) return;
                  const f = list[0];
                  if (f?.type.startsWith('image/')) {
                    void uploadPhoto(f, `paste-${Date.now()}`);
                    setAttachOpen(true);
                  }
                }}
                placeholder={
                  files.length
                    ? 'What would you like to know about this photo?'
                    : 'Ask about a procedure, a recipe or a ticket…'
                }
                autoSize={{ minRows: 1, maxRows: 6 }}
              />
            )}
          </Suggestion>
        </div>
      </Card>
      <Modal
        open={!!renaming}
        title="Rename chat"
        okText="Save"
        onOk={() => void renameChat()}
        onCancel={() => setRenaming(null)}
        okButtonProps={{ disabled: !renaming?.label.trim() }}
        destroyOnHidden
      >
        <Input
          autoFocus
          maxLength={80}
          value={renaming?.label}
          onChange={(e) => setRenaming((r) => (r ? { ...r, label: e.target.value } : r))}
          onPressEnter={() => void renameChat()}
          aria-label="Chat name"
        />
      </Modal>
    </Flex>
  );
}
