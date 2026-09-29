import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Flex,
  Input,
  InputNumber,
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
import {
  XCard,
  registerCatalog,
  type ActionPayload,
  type XAgentCommand_v0_9,
} from '@ant-design/x-card';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import type {
  ChatInterrupt,
  CreateTicketPayload,
  EscalationPayload,
  ReassignPayload,
  SupplyOrderPayload,
} from '@/api/types';
import { PRIORITY } from '@/theme/tokens';
import type { ApprovalView } from '@/api/views';
import { crewById, stationName } from '@/api/lookups';

const CATALOG_ID = 'local://linemate/approvals/v1';

registerCatalog({
  catalogId: CATALOG_ID,
  title: 'LineMate human-approval cards',
  components: {
    ApprovalCard: {
      type: 'object',
      properties: {
        approvalId: { type: 'string' },
        kind: {
          type: 'string',
          enum: ['draft_supply_order', 'escalate_incident', 'reassign_ticket', 'create_ticket'],
        },
        title: { type: 'string' },
        summary: { type: 'string' },
        payload: { type: 'object' },
        canApprove: { type: 'boolean' },
      },
      required: ['approvalId', 'kind', 'title'],
    },
  },
});

interface CardProps extends ChatInterrupt {
  onAction?: (name: string, context: Record<string, unknown>) => void;
}

/** Rendered by XCard from the `ApprovalCard` component in the A2UI command stream. */
function ApprovalCardView({
  approvalId,
  kind,
  title,
  summary,
  payload,
  canApprove,
  onAction,
}: CardProps) {
  const navigate = useNavigate();
  const approvals = useQuery({
    queryKey: ['approvals'],
    queryFn: () => api<ApprovalView[]>('/api/approvals'),
  });
  const current = approvals.data?.find((a) => a.id === approvalId);
  const supply = kind === 'draft_supply_order' ? (payload as SupplyOrderPayload) : null;
  const esc = kind === 'escalate_incident' ? (payload as EscalationPayload) : null;
  const reassign = kind === 'reassign_ticket' ? (payload as ReassignPayload) : null;
  const create = kind === 'create_ticket' ? (payload as CreateTicketPayload) : null;
  const result = current?.result as
    { orderRef?: string; ticketId?: string; total?: number } | null | undefined;
  const [qty, setQty] = useState<number>(supply?.quantity ?? 1);
  const [reason, setReason] = useState('');
  const status = current?.status ?? 'pending';
  const pending = status === 'pending';

  return (
    <Card
      size="small"
      style={{
        marginTop: 12,
        borderColor: pending ? '#FAAD14' : status === 'approved' ? '#52C41A' : '#FF4D4F',
      }}
      title={
        <Flex gap={8} align="center">
          {supply ? (
            <ShoppingCartOutlined />
          ) : create ? (
            <FileAddOutlined />
          ) : reassign ? (
            <SwapOutlined />
          ) : (
            <SafetyOutlined />
          )}
          <span>{title}</span>
        </Flex>
      }
      extra={
        <Tag color={pending ? 'warning' : status === 'approved' ? 'success' : 'error'}>
          {pending ? 'Waiting for approval' : status === 'approved' ? 'Approved' : 'Rejected'}
        </Tag>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        {summary}
      </Typography.Paragraph>
      {supply && (
        <Descriptions
          size="small"
          column={2}
          items={[
            { key: 'item', label: 'Item', children: supply.item },
            { key: 'supplier', label: 'Supplier', children: supply.supplier },
            {
              key: 'qty',
              label: 'Quantity',
              children:
                canApprove && pending ? (
                  <InputNumber
                    size="small"
                    min={1}
                    max={20}
                    value={qty}
                    onChange={(v) => v && setQty(v)}
                    suffix={supply.unit}
                    style={{ width: 110 }}
                  />
                ) : (
                  `${current ? (current.payload as SupplyOrderPayload).quantity : supply.quantity} ${supply.unit}`
                ),
            },
            { key: 'total', label: 'Total', children: `$${(qty * supply.unitPrice).toFixed(2)}` },
            { key: 'needed', label: 'Needed by', children: supply.neededBy },
            {
              key: 'ticket',
              label: 'Ticket',
              children: (
                <Typography.Link onClick={() => navigate(`/tickets/${supply.ticketId}`)}>
                  {supply.ticketId}
                </Typography.Link>
              ),
            },
          ]}
        />
      )}
      {esc && (
        <Descriptions
          size="small"
          column={1}
          items={[
            {
              key: 'sev',
              label: 'Severity',
              children: <Tag color="red">{esc.severity.replace('_', ' ')}</Tag>,
            },
            {
              key: 'notify',
              label: 'Notify',
              children: esc.notify.map((n) => crewById(n)?.name ?? n).join(', '),
            },
            {
              key: 'hold',
              label: 'Hold product',
              children: esc.holdProduct ? 'Yes — label DO NOT USE' : 'No',
            },
            {
              key: 'ticket',
              label: 'Ticket',
              children: (
                <Typography.Link onClick={() => navigate(`/tickets/${esc.ticketId}`)}>
                  {esc.ticketId}
                </Typography.Link>
              ),
            },
          ]}
        />
      )}
      {reassign && (
        <Descriptions
          size="small"
          column={1}
          items={[
            {
              key: 'from',
              label: 'From',
              children: crewById(reassign.fromAssigneeId)?.name ?? 'Unassigned',
            },
            {
              key: 'to',
              label: 'To',
              children: crewById(reassign.toAssigneeId)?.name ?? reassign.toAssigneeId,
            },
            {
              key: 'ticket',
              label: 'Ticket',
              children: (
                <Typography.Link onClick={() => navigate(`/tickets/${reassign.ticketId}`)}>
                  {reassign.ticketId}
                </Typography.Link>
              ),
            },
          ]}
        />
      )}
      {create && (
        <Descriptions
          size="small"
          column={1}
          items={[
            { key: 'title', label: 'Title', children: create.title },
            {
              key: 'priority',
              label: 'Priority',
              children: (
                <Tag color={PRIORITY[create.priority]?.color}>
                  {PRIORITY[create.priority]?.label ?? create.priority}
                </Tag>
              ),
            },
            { key: 'station', label: 'Station', children: stationName(create.station) },
          ]}
        />
      )}
      {status === 'approved' && result && (result.orderRef || result.ticketId) && (
        <Alert
          style={{ marginTop: 8 }}
          type="success"
          showIcon
          title={
            result.orderRef
              ? `Order ${result.orderRef} sent${result.total ? ` · $${result.total.toFixed(2)}` : ''}`
              : `Ticket ${result.ticketId} updated`
          }
          description={
            result.ticketId ? (
              <Typography.Link onClick={() => navigate(`/tickets/${result.ticketId}`)}>
                Open {result.ticketId}
              </Typography.Link>
            ) : undefined
          }
        />
      )}
      {pending && canApprove && (
        <Flex vertical gap={8} style={{ marginTop: 8 }}>
          <Input.TextArea
            autoSize={{ minRows: 1, maxRows: 3 }}
            placeholder="Reason (required to reject)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <Flex gap={8} justify="flex-end">
            <Button
              danger
              icon={<CloseOutlined />}
              disabled={reason.trim().length < 3}
              onClick={() =>
                onAction?.('decide', { approvalId, decision: 'rejected', reason, quantity: qty })
              }
            >
              Reject
            </Button>
            <Button
              type="primary"
              icon={<CheckOutlined />}
              onClick={() =>
                onAction?.('decide', { approvalId, decision: 'approved', reason, quantity: qty })
              }
            >
              Approve{supply ? ' & send order' : ''}
            </Button>
          </Flex>
        </Flex>
      )}
      {pending && !canApprove && (
        <Alert
          style={{ marginTop: 8 }}
          type="info"
          showIcon
          title="Only the Kitchen Manager can approve this."
          description={
            <Typography.Link onClick={() => navigate('/approvals')}>
              Open the Approvals queue
            </Typography.Link>
          }
        />
      )}
      {!pending && current?.decidedByName && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {status === 'approved' ? 'Approved' : 'Rejected'} by {current.decidedByName}
          {current.reason ? ` — “${current.reason}”` : ''}
        </Typography.Text>
      )}
    </Card>
  );
}

/** Streams the interrupt as A2UI v0.9 commands into an XCard surface. */
export function ApprovalSurface({ interrupt }: { interrupt: ChatInterrupt }) {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const surfaceId = `approval-${interrupt.approvalId}`;
  const commands = useMemo<XAgentCommand_v0_9[]>(
    () => [
      { version: 'v0.9', createSurface: { surfaceId, catalogId: CATALOG_ID } },
      {
        version: 'v0.9',
        updateComponents: {
          surfaceId,
          components: [{ id: 'root', component: 'ApprovalCard', ...interrupt }],
        },
      },
    ],
    [surfaceId, interrupt],
  );
  const decide = useMutation({
    mutationFn: (ctx: {
      approvalId: string;
      decision: string;
      reason?: string;
      quantity?: number;
    }) =>
      api(`/api/approvals/${ctx.approvalId}/decision`, {
        body: { decision: ctx.decision, reason: ctx.reason, quantity: ctx.quantity },
      }),
    onSuccess: (_d, ctx) => {
      message.success(
        ctx.decision === 'approved'
          ? 'Approved — the graph resumes and the tool call runs'
          : 'Rejected — the graph ends without the write',
      );
      qc.invalidateQueries({ queryKey: ['approvals'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
      qc.invalidateQueries({ queryKey: ['tickets'] });
      qc.invalidateQueries({ queryKey: ['ticket'] });
      qc.invalidateQueries({ queryKey: ['runs'] });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Decision failed'),
  });
  const onAction = (p: ActionPayload) => {
    if (p.name === 'decide')
      decide.mutate(
        p.context as { approvalId: string; decision: string; reason?: string; quantity?: number },
      );
  };
  return (
    <XCard.Box
      commands={commands}
      components={{ ApprovalCard: ApprovalCardView }}
      onAction={onAction}
    >
      <XCard.Card id={surfaceId} />
    </XCard.Box>
  );
}
