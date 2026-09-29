import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Avatar,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Flex,
  Input,
  Result,
  Row,
  Select,
  Skeleton,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  Upload,
} from 'antd';
import {
  MessageOutlined,
  PaperClipOutlined,
  SendOutlined,
  SwapOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { FileCard } from '@ant-design/x';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import { api, ApiError, fileHref } from '@/api/client';
import type { Attachment, Priority, TicketComment, TicketStatus } from '@/api/types';
import type { TicketDetailView } from '@/api/views';
import { useActor } from '@/auth/useAuth';
import { can, denyReason } from '@/auth/permissions';
import { PageHeader } from '@/components/PageHeader';
import {
  CategoryTag,
  FreshnessTag,
  Person,
  PriorityTag,
  StationTag,
  StatusTag,
} from '@/components/tags';
import { FILE_ICON, PRIORITIES, PRIORITY, STATUS, STATUSES } from '@/theme/tokens';
import { crew, stations, crewById } from '@/api/lookups';

dayjs.extend(relativeTime);

function CommentItem({
  c,
  replies,
  onReply,
  canReply,
}: {
  c: TicketComment;
  replies: TicketComment[];
  onReply: (parentId: string, body: string) => void;
  canReply: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const author = crewById(c.authorId);
  const station = stations().find((s) => s.id === author?.station);
  return (
    <Flex gap={12} align="flex-start">
      <Avatar style={{ background: station?.color ?? '#8c8c8c', flex: 'none' }}>
        {author?.initials ?? '?'}
      </Avatar>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Flex gap={8} align="baseline" wrap>
          <Typography.Text strong>{author?.name ?? c.authorId}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {author?.title}
          </Typography.Text>
          <Tooltip title={dayjs(c.createdAt).format('D MMM YYYY HH:mm')}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {dayjs(c.createdAt).fromNow()}
            </Typography.Text>
          </Tooltip>
        </Flex>
        <Typography.Paragraph style={{ margin: '2px 0 4px', whiteSpace: 'pre-wrap' }}>
          {c.body}
        </Typography.Paragraph>
        {canReply && (
          <Button
            type="link"
            size="small"
            style={{ padding: 0 }}
            onClick={() => setOpen((v) => !v)}
          >
            Reply
          </Button>
        )}
        {open && (
          <Flex gap={8} style={{ marginTop: 6 }}>
            <Input.TextArea
              autoSize={{ minRows: 1, maxRows: 4 }}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={`Reply to ${author?.name.split(' ')[0] ?? 'comment'}…`}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              disabled={!text.trim()}
              onClick={() => {
                onReply(c.id, text.trim());
                setText('');
                setOpen(false);
              }}
              aria-label="Send reply"
            />
          </Flex>
        )}
        {replies.length > 0 && (
          <Flex
            vertical
            gap={12}
            style={{
              marginTop: 12,
              paddingInlineStart: 12,
              borderInlineStart: '2px solid rgba(128,128,128,0.18)',
            }}
          >
            {replies.map((r) => (
              <CommentItem key={r.id} c={r} replies={[]} onReply={onReply} canReply={false} />
            ))}
          </Flex>
        )}
      </div>
    </Flex>
  );
}

export default function TicketDetail() {
  const { id = '' } = useParams();
  const actor = useActor();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const [comment, setComment] = useState('');
  const key = ['ticket', id];
  const q = useQuery({ queryKey: key, queryFn: () => api<TicketDetailView>(`/api/tickets/${id}`) });

  const patch = useMutation({
    mutationFn: (
      body: Partial<{ status: TicketStatus; priority: Priority; assigneeId: string | null }>,
    ) => api(`/api/tickets/${id}`, { method: 'PATCH', body }),
    onMutate: async (body) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<TicketDetailView>(key);
      if (prev)
        qc.setQueryData<TicketDetailView>(key, { ...prev, ticket: { ...prev.ticket, ...body } });
      return { prev };
    },
    onError: (e, _b, ctx) => {
      if (ctx?.prev) qc.setQueryData(key, ctx.prev);
      message.error(e instanceof ApiError ? e.message : 'Update failed');
    },
    onSuccess: () => message.success('Ticket updated'),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: key });
      qc.invalidateQueries({ queryKey: ['tickets'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });

  const addComment = useMutation({
    mutationFn: (v: { body: string; parentId?: string }) =>
      api<TicketComment>(`/api/tickets/${id}/comments`, { body: v }),
    onMutate: async (v) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<TicketDetailView>(key);
      const optimistic: TicketComment = {
        id: `tmp-${Date.now()}`,
        ticketId: id,
        authorId: actor.crewMemberId ?? actor.userId,
        body: v.body,
        createdAt: new Date().toISOString(),
        parentId: v.parentId ?? null,
      };
      if (prev)
        qc.setQueryData<TicketDetailView>(key, {
          ...prev,
          comments: [...prev.comments, optimistic],
        });
      return { prev };
    },
    onError: (e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(key, ctx.prev);
      message.error(e instanceof ApiError ? e.message : 'Comment failed');
    },
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });

  const attach = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append('files', file, file.name);
      return api<Attachment[]>(`/api/tickets/${id}/attachments`, { form });
    },
    onSuccess: (list) => {
      message.success(`${list.map((a) => a.name).join(', ')} attached`);
      qc.invalidateQueries({ queryKey: key });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Upload failed'),
  });

  if (q.isLoading) return <Skeleton active paragraph={{ rows: 14 }} />;
  if (q.error || !q.data)
    return (
      <Result
        status="404"
        title="Ticket not found"
        subTitle={q.error?.message}
        extra={<Button onClick={() => navigate('/tickets')}>Back to tickets</Button>}
      />
    );

  const {
    ticket: t,
    comments,
    attachments,
    relatedDoc,
    mismatch,
    activity,
    permissions: p,
  } = q.data;
  const top = comments.filter((c) => !c.parentId);
  const repliesOf = (cid: string) => comments.filter((c) => c.parentId === cid);
  const isClosing = (s: TicketStatus) => s === 'resolved' || s === 'closed';

  return (
    <>
      <PageHeader
        crumbs={[{ title: 'Tickets', to: '/tickets' }, { title: t.id }]}
        title={t.title}
        tags={
          <>
            <PriorityTag value={t.priority} />
            <StatusTag value={t.status} />
            <StationTag value={t.station} />
          </>
        }
        subtitle={`${t.id} · opened ${dayjs(t.createdAt).fromNow()} by ${crewById(t.reporterId)?.name ?? t.reporterId} · updated ${dayjs(t.updatedAt).fromNow()}`}
        extra={
          can.ask(actor) && (
            <Button
              icon={<MessageOutlined />}
              onClick={() =>
                navigate(
                  `/ask?q=${encodeURIComponent(`Help me with ${t.id}: ${t.title}`)}&ticket=${t.id}`,
                )
              }
            >
              Ask LineMate about this ticket
            </Button>
          )
        }
      />
      {mismatch && (
        <Alert
          type="warning"
          showIcon
          icon={<SwapOutlined />}
          style={{ marginBottom: 16 }}
          title="Ownership mismatch"
          description={`${crewById(mismatch.assigneeId)?.name ?? 'The assignee'} works on ${stations().find((s) => s.id === mismatch.assigneeStation)?.name ?? 'another station'}, but ${mismatch.docId} is owned by ${stations().find((s) => s.id === mismatch.docStation)?.name}.${mismatch.suggestedAssigneeId ? ` Suggested owner: ${crewById(mismatch.suggestedAssigneeId)?.name}.` : ''}`}
          action={
            p.assign && mismatch.suggestedAssigneeId ? (
              <Button
                size="small"
                onClick={() => patch.mutate({ assigneeId: mismatch.suggestedAssigneeId })}
              >
                Reassign
              </Button>
            ) : undefined
          }
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Flex vertical gap={16}>
            <Card title="Description">
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 8 }}>
                {t.description}
              </Typography.Paragraph>
              <Flex gap={4} wrap>
                {t.tags.map((tag) => (
                  <Tag key={tag}>{tag}</Tag>
                ))}
              </Flex>
            </Card>
            <Card
              title={
                <Flex gap={8} align="center">
                  <PaperClipOutlined />
                  Attachments ({attachments.length})
                </Flex>
              }
              extra={
                <Tooltip
                  title={
                    p.attach
                      ? 'Photos, PDFs, Word · max 20 MB'
                      : 'Admins cannot attach files to kitchen tickets.'
                  }
                >
                  <Upload
                    showUploadList={false}
                    accept=".png,.jpg,.jpeg,.pdf,.docx,.xlsx,.txt"
                    disabled={!p.attach}
                    beforeUpload={(f) => {
                      attach.mutate(f);
                      return false;
                    }}
                  >
                    <Button
                      size="small"
                      icon={<PaperClipOutlined />}
                      disabled={!p.attach}
                      loading={attach.isPending}
                    >
                      Attach file
                    </Button>
                  </Upload>
                </Tooltip>
              }
            >
              {attachments.length ? (
                <FileCard.List
                  overflow="wrap"
                  items={attachments.map((a) => ({
                    key: a.id,
                    name: a.name,
                    byte: a.bytes,
                    type:
                      a.url && (a.kind === 'jpg' || a.kind === 'png')
                        ? ('image' as const)
                        : ('file' as const),
                    src: fileHref(a.url),
                    icon: FILE_ICON[a.kind],
                    description: `${crewById(a.uploadedBy)?.name ?? a.uploadedBy} · ${dayjs(a.uploadedAt).fromNow()}`,
                    imageProps: { height: 120, style: { objectFit: 'cover' } },
                  }))}
                />
              ) : (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="No attachments. Photos of equipment or labels help the next shift."
                />
              )}
            </Card>
            <Card title={`Comments (${comments.length})`}>
              <Flex vertical gap={20}>
                {top.length === 0 && (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No comments yet" />
                )}
                {top.map((c) => (
                  <CommentItem
                    key={c.id}
                    c={c}
                    replies={repliesOf(c.id)}
                    canReply={p.comment}
                    onReply={(parentId, body) => addComment.mutate({ body, parentId })}
                  />
                ))}
              </Flex>
              {p.comment ? (
                <Flex gap={8} style={{ marginTop: 20 }} align="flex-end">
                  <Input.TextArea
                    autoSize={{ minRows: 2, maxRows: 6 }}
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    placeholder="Add an update for the next shift…"
                  />
                  <Button
                    type="primary"
                    icon={<SendOutlined />}
                    disabled={!comment.trim()}
                    onClick={() => {
                      addComment.mutate({ body: comment.trim() });
                      setComment('');
                    }}
                  >
                    Post
                  </Button>
                </Flex>
              ) : (
                <Alert
                  style={{ marginTop: 16 }}
                  type="info"
                  showIcon
                  title="Admins can read tickets but not comment on them."
                />
              )}
            </Card>
          </Flex>
        </Col>
        <Col xs={24} xl={8}>
          <Flex vertical gap={16}>
            <Card title="Manage">
              <Flex vertical gap={12}>
                <div>
                  <Typography.Text type="secondary">Status</Typography.Text>
                  <Tooltip
                    title={p.changeStatus ? '' : denyReason('status', actor)}
                    placement="left"
                  >
                    <Select
                      style={{ width: '100%', marginTop: 4 }}
                      value={t.status}
                      disabled={!p.changeStatus}
                      onChange={(v) => patch.mutate({ status: v })}
                      options={STATUSES.map((s) => ({
                        value: s,
                        label: STATUS[s].label,
                        disabled:
                          isClosing(s) &&
                          !p.lowerOrClose &&
                          !(actor.role === 'line_cook' && s === 'resolved'),
                      }))}
                    />
                  </Tooltip>
                </div>
                <div>
                  <Typography.Text type="secondary">Priority</Typography.Text>
                  <Tooltip
                    title={
                      p.lowerOrClose
                        ? ''
                        : 'You can escalate. Lowering is for the station Sous Chef or the Kitchen Manager.'
                    }
                    placement="left"
                  >
                    <Select
                      style={{ width: '100%', marginTop: 4 }}
                      value={t.priority}
                      disabled={!p.raise}
                      onChange={(v) => patch.mutate({ priority: v })}
                      options={PRIORITIES.map((pr) => ({
                        value: pr,
                        label: PRIORITY[pr].label,
                        disabled: PRIORITY[pr].rank < PRIORITY[t.priority].rank && !p.lowerOrClose,
                      }))}
                    />
                  </Tooltip>
                </div>
                <div>
                  <Typography.Text type="secondary">Assignee</Typography.Text>
                  <Tooltip title={p.assign ? '' : denyReason('assign', actor)} placement="left">
                    <Select
                      style={{ width: '100%', marginTop: 4 }}
                      value={t.assigneeId ?? undefined}
                      placeholder="Unassigned"
                      disabled={!p.assign}
                      showSearch={{ optionFilterProp: 'label' }}
                      onChange={(v) => patch.mutate({ assigneeId: v ?? null })}
                      options={crew().map((c) => ({
                        value: c.id,
                        label: `${c.name} · ${stations().find((s) => s.id === c.station)?.name ?? 'All'}`,
                      }))}
                    />
                  </Tooltip>
                </div>
              </Flex>
              <Descriptions
                style={{ marginTop: 16 }}
                column={1}
                size="small"
                items={[
                  {
                    key: 'assignee',
                    label: 'Assignee',
                    children: <Person id={t.assigneeId} showTitle />,
                  },
                  { key: 'reporter', label: 'Reporter', children: <Person id={t.reporterId} /> },
                  {
                    key: 'created',
                    label: 'Opened',
                    children: dayjs(t.createdAt).format('D MMM YYYY HH:mm'),
                  },
                ]}
              />
            </Card>
            <Card title="Related document">
              {relatedDoc ? (
                <Flex vertical gap={8}>
                  <Typography.Link strong onClick={() => navigate(`/documents/${relatedDoc.id}`)}>
                    {relatedDoc.id} · {relatedDoc.title}
                  </Typography.Link>
                  <Flex gap={6} wrap>
                    <CategoryTag value={relatedDoc.category} />
                    <StationTag value={relatedDoc.station} />
                    {relatedDoc.category !== 'incident' && (
                      <FreshnessTag days={relatedDoc.daysSinceReview} />
                    )}
                  </Flex>
                  {relatedDoc.stale && (
                    <Alert
                      type="error"
                      showIcon
                      icon={<WarningOutlined />}
                      title="This SOP is past its review date"
                      description="Double-check the steps with your Sous Chef before relying on it."
                    />
                  )}
                </Flex>
              ) : (
                <Typography.Text type="secondary">
                  {t.relatedDocId
                    ? 'The linked document is restricted for your role.'
                    : 'No document linked.'}
                </Typography.Text>
              )}
            </Card>
            <Card title="Activity">
              <Timeline
                items={activity.map((e) => ({
                  color: e.action.includes('escalated')
                    ? 'red'
                    : e.action.includes('moved')
                      ? 'blue'
                      : 'gray',
                  title: (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {dayjs(e.at).fromNow()}
                    </Typography.Text>
                  ),
                  content: (
                    <span>
                      <Typography.Text strong>{e.actorName}</Typography.Text> {e.action}
                      {e.action === 'commented on' ? '' : e.detail ? ` — ${e.detail}` : ''}
                    </span>
                  ),
                }))}
              />
            </Card>
          </Flex>
        </Col>
      </Row>
    </>
  );
}
