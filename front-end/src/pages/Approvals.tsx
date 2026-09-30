import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Skeleton,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  CheckOutlined,
  CloseOutlined,
  FileAddOutlined,
  SafetyOutlined,
  ShoppingCartOutlined,
  SwapOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import { api, ApiError } from '@/api/client';
import type {
  ApprovalKind,
  CreateTicketPayload,
  EscalationPayload,
  ReassignPayload,
  SupplyOrderPayload,
} from '@/api/types';
import type { ApprovalView } from '@/api/views';
import { can } from '@/auth/permissions';
import { useActor } from '@/auth/useAuth';
import { PageHeader } from '@/components/PageHeader';
import { crewById, stationName } from '@/api/lookups';
import { PRIORITY } from '@/theme/tokens';

dayjs.extend(relativeTime);

const KIND: Record<ApprovalKind, { label: string; icon: React.ReactNode; tool: string }> = {
  draft_supply_order: {
    label: 'Supply order',
    icon: <ShoppingCartOutlined />,
    tool: 'draft_supply_order',
  },
  escalate_incident: { label: 'Escalation', icon: <SafetyOutlined />, tool: 'escalate_incident' },
  reassign_ticket: { label: 'Reassign', icon: <SwapOutlined />, tool: 'reassign_ticket' },
  create_ticket: { label: 'New ticket', icon: <FileAddOutlined />, tool: 'create_ticket' },
};

function PayloadPreview({ a }: { a: ApprovalView }) {
  if (a.kind === 'draft_supply_order') {
    const p = a.payload as SupplyOrderPayload;
    return (
      <Descriptions
        size="small"
        column={2}
        items={[
          { key: 'i', label: 'Item', children: `${p.item} (${p.sku})` },
          { key: 's', label: 'Supplier', children: p.supplier },
          { key: 'q', label: 'Quantity', children: `${p.quantity} ${p.unit}` },
          { key: 'p', label: 'Total', children: `$${(p.quantity * p.unitPrice).toFixed(2)}` },
          { key: 'n', label: 'Needed by', children: p.neededBy },
          {
            key: 't',
            label: 'Ticket',
            children: <Link to={`/tickets/${p.ticketId}`}>{p.ticketId}</Link>,
          },
        ]}
      />
    );
  }
  if (a.kind === 'escalate_incident') {
    const p = a.payload as EscalationPayload;
    return (
      <Descriptions
        size="small"
        column={2}
        items={[
          {
            key: 's',
            label: 'Severity',
            children: <Tag color="red">{p.severity.replace('_', ' ')}</Tag>,
          },
          { key: 'h', label: 'Hold product', children: p.holdProduct ? 'Yes' : 'No' },
          {
            key: 'n',
            label: 'Notify',
            span: 2,
            children: p.notify.map((n) => crewById(n)?.name ?? n).join(', '),
          },
          { key: 'o', label: 'Note', span: 2, children: p.note },
          {
            key: 't',
            label: 'Ticket',
            children: <Link to={`/tickets/${p.ticketId}`}>{p.ticketId}</Link>,
          },
        ]}
      />
    );
  }
  if (a.kind === 'create_ticket') {
    const p = a.payload as CreateTicketPayload;
    return (
      <Descriptions
        size="small"
        column={2}
        items={[
          { key: 'ti', label: 'Title', span: 2, children: p.title },
          {
            key: 'pr',
            label: 'Priority',
            children: (
              <Tag color={PRIORITY[p.priority]?.color}>
                {PRIORITY[p.priority]?.label ?? p.priority}
              </Tag>
            ),
          },
          { key: 'st', label: 'Station', children: stationName(p.station) },
          ...(p.description
            ? [{ key: 'de', label: 'Description', span: 2, children: p.description }]
            : []),
        ]}
      />
    );
  }
  const p = a.payload as ReassignPayload;
  return (
    <Descriptions
      size="small"
      column={2}
      items={[
        { key: 'f', label: 'From', children: crewById(p.fromAssigneeId)?.name ?? 'Unassigned' },
        { key: 'to', label: 'To', children: crewById(p.toAssigneeId)?.name ?? p.toAssigneeId },
        { key: 'r', label: 'Reason', span: 2, children: p.reason },
        {
          key: 't',
          label: 'Ticket',
          children: <Link to={`/tickets/${p.ticketId}`}>{p.ticketId}</Link>,
        },
      ]}
    />
  );
}

export default function Approvals() {
  const actor = useActor();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const canApprove = can.approve(actor);
  const q = useQuery({
    queryKey: ['approvals'],
    queryFn: () => api<ApprovalView[]>('/api/approvals'),
  });
  const [deciding, setDeciding] = useState<{
    a: ApprovalView;
    decision: 'approved' | 'rejected';
  } | null>(null);
  const [form] = Form.useForm<{ reason: string; quantity?: number }>();

  const decide = useMutation({
    mutationFn: (v: { id: string; decision: string; reason: string; quantity?: number }) =>
      api<ApprovalView>(`/api/approvals/${v.id}/decision`, { body: v }),
    onSuccess: (_d, v) => {
      message.success(
        v.decision === 'approved'
          ? 'Approved — the paused run resumes and the tool call executes'
          : 'Rejected — nothing was written',
      );
      setDeciding(null);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['approvals'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
      qc.invalidateQueries({ queryKey: ['runs'] });
      qc.invalidateQueries({ queryKey: ['tickets'] });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Decision failed'),
  });

  const pending = (q.data ?? []).filter((a) => a.status === 'pending');
  const decided = (q.data ?? []).filter((a) => a.status !== 'pending');

  return (
    <>
      <PageHeader
        title={
          <Flex gap={8} align="center">
            Approvals <Badge count={pending.length} />
          </Flex>
        }
        subtitle="Write actions proposed by the agent pause at a LangGraph interrupt until the Kitchen Manager decides"
      />
      {!canApprove && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          title="View only"
          description="You can see and request approvals. Only the Kitchen Manager can approve or reject."
        />
      )}
      {q.isLoading ? (
        <Skeleton active />
      ) : (
        <>
          <Typography.Title level={5}>Pending ({pending.length})</Typography.Title>
          {pending.length === 0 ? (
            <Card>
              <Empty description="Nothing waiting. Run triage to generate proposals." />
            </Card>
          ) : (
            <Row gutter={[16, 16]}>
              {pending.map((a) => (
                <Col xs={24} xl={12} key={a.id}>
                  <Card
                    size="small"
                    title={
                      <Flex gap={8} align="center">
                        {KIND[a.kind].icon}
                        {a.title}
                      </Flex>
                    }
                    extra={<Tag color="warning">pending</Tag>}
                    actions={
                      canApprove
                        ? [
                            <Button
                              key="r"
                              type="text"
                              danger
                              icon={<CloseOutlined />}
                              onClick={() => setDeciding({ a, decision: 'rejected' })}
                            >
                              Reject
                            </Button>,
                            <Button
                              key="a"
                              type="text"
                              icon={<CheckOutlined />}
                              style={{ color: '#389E0D' }}
                              onClick={() => {
                                setDeciding({ a, decision: 'approved' });
                                form.setFieldsValue({
                                  quantity:
                                    a.kind === 'draft_supply_order'
                                      ? (a.payload as SupplyOrderPayload).quantity
                                      : undefined,
                                });
                              }}
                            >
                              Approve
                            </Button>,
                          ]
                        : undefined
                    }
                  >
                    <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
                      {a.summary}
                    </Typography.Paragraph>
                    <PayloadPreview a={a} />
                    <Flex gap={8} wrap style={{ marginTop: 8 }}>
                      <Tag>tool: {KIND[a.kind].tool}</Tag>
                      <Tag>requested by {a.requestedByName}</Tag>
                      <Tag>{dayjs(a.requestedAt).fromNow()}</Tag>
                      <Link to="/agent/runs">
                        <Tag color="blue">run {a.runId}</Tag>
                      </Link>
                    </Flex>
                  </Card>
                </Col>
              ))}
            </Row>
          )}
          <Typography.Title level={5} style={{ marginTop: 24 }}>
            Decision history
          </Typography.Title>
          <Table<ApprovalView>
            rowKey="id"
            size="small"
            dataSource={decided}
            pagination={{ pageSize: 8 }}
            columns={[
              { title: 'ID', dataIndex: 'id', width: 100 },
              {
                title: 'Type',
                dataIndex: 'kind',
                render: (k: ApprovalKind) => <Tag icon={KIND[k].icon}>{KIND[k].label}</Tag>,
                filters: Object.entries(KIND).map(([value, v]) => ({ text: v.label, value })),
                onFilter: (v, r) => r.kind === v,
              },
              { title: 'Request', dataIndex: 'title', ellipsis: true },
              {
                title: 'Decision',
                dataIndex: 'status',
                render: (s: string) => (
                  <Tag color={s === 'approved' ? 'success' : 'error'}>{s}</Tag>
                ),
              },
              { title: 'By', dataIndex: 'decidedByName' },
              { title: 'Reason', dataIndex: 'reason', ellipsis: true },
              {
                title: 'When',
                dataIndex: 'decidedAt',
                render: (d: string) => (d ? dayjs(d).format('MMM D, HH:mm') : '—'),
                sorter: (a, b) => String(a.decidedAt).localeCompare(String(b.decidedAt)),
                defaultSortOrder: 'descend',
              },
            ]}
          />
        </>
      )}
      <Modal
        open={!!deciding}
        title={
          deciding
            ? `${deciding.decision === 'approved' ? 'Approve' : 'Reject'}: ${deciding.a.title}`
            : ''
        }
        okText={deciding?.decision === 'approved' ? 'Approve' : 'Reject'}
        okButtonProps={{ danger: deciding?.decision === 'rejected', loading: decide.isPending }}
        onCancel={() => setDeciding(null)}
        onOk={() =>
          form.validateFields().then(
            (v) =>
              deciding &&
              decide.mutate({
                id: deciding.a.id,
                decision: deciding.decision,
                reason: v.reason ?? '',
                quantity: v.quantity,
              }),
          )
        }
        destroyOnHidden
      >
        <Form form={form} layout="vertical" preserve={false}>
          {deciding?.decision === 'approved' && deciding.a.kind === 'draft_supply_order' && (
            <Form.Item
              name="quantity"
              label="Quantity (you can edit before approving)"
              rules={[{ required: true }]}
            >
              <InputNumber
                min={1}
                max={20}
                suffix={(deciding.a.payload as SupplyOrderPayload).unit}
              />
            </Form.Item>
          )}
          <Form.Item
            name="reason"
            label="Reason"
            rules={
              deciding?.decision === 'rejected'
                ? [{ required: true, min: 3, message: 'A reason is required to reject' }]
                : []
            }
          >
            <Input.TextArea
              autoSize={{ minRows: 2, maxRows: 5 }}
              placeholder={
                deciding?.decision === 'approved' ? 'Optional note' : 'Why is this rejected?'
              }
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
