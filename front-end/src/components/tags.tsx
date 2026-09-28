import { Avatar, Badge, Flex, Tag, Tooltip, Typography } from 'antd';
import { ClockCircleOutlined, CheckCircleOutlined, WarningOutlined } from '@ant-design/icons';
import type { DocCategory, Priority, StationId, TicketStatus } from '@/api/types';
import { CATEGORY, PRIORITY, STALE_DAYS, STATUS } from '@/theme/tokens';
import { stations, crewById } from '@/api/lookups';

export const PriorityTag = ({ value }: { value: Priority }) => (
  <Tag color={PRIORITY[value].color} style={{ fontWeight: 500 }}>
    {PRIORITY[value].label}
  </Tag>
);

export const StatusTag = ({ value }: { value: TicketStatus }) => (
  <Tag color={STATUS[value].color} variant="outlined">
    {STATUS[value].label}
  </Tag>
);

export const CategoryTag = ({ value }: { value: DocCategory }) => (
  <Tag color={CATEGORY[value].color}>{CATEGORY[value].label}</Tag>
);

export function StationTag({ value }: { value: StationId | null | undefined }) {
  const s = stations().find((x) => x.id === value);
  if (!s) return <Tag>All stations</Tag>;
  return (
    <Tag variant="outlined">
      <Badge color={s.color} text={s.name} styles={{ indicator: { marginInlineEnd: 2 } }} />
    </Tag>
  );
}

export function FreshnessTag({ days, compact = false }: { days: number; compact?: boolean }) {
  if (days > STALE_DAYS)
    return (
      <Tooltip title={`Last reviewed ${days} days ago — over the ${STALE_DAYS}-day review policy`}>
        <Tag color="error" icon={<WarningOutlined />}>
          {compact ? `${days} d` : `Stale · ${days} d`}
        </Tag>
      </Tooltip>
    );
  if (days > STALE_DAYS - 30)
    return (
      <Tooltip title={`Due for review in ${STALE_DAYS - days + 1} days`}>
        <Tag color="warning" icon={<ClockCircleOutlined />}>
          {compact ? `${days} d` : `Due soon · ${days} d`}
        </Tag>
      </Tooltip>
    );
  return (
    <Tag color="success" icon={<CheckCircleOutlined />}>
      {compact ? `${days} d` : `Fresh · ${days} d`}
    </Tag>
  );
}

export const SourceTag = ({ value }: { value: 'seed' | 'upload' }) =>
  value === 'seed' ? (
    <Tag variant="filled">Seed</Tag>
  ) : (
    <Tag color="processing" variant="filled">
      Uploaded
    </Tag>
  );

export function Person({
  id,
  size = 'small',
  showTitle = false,
}: {
  id: string | null | undefined;
  size?: 'small' | 'default';
  showTitle?: boolean;
}) {
  const c = crewById(id);
  if (!c) return <Typography.Text type="secondary">{id ? id : 'Unassigned'}</Typography.Text>;
  const station = stations().find((s) => s.id === c.station);
  return (
    <Flex align="center" gap={8}>
      <Avatar
        size={size}
        style={{ background: station?.color ?? '#8C8C8C', flex: 'none', fontSize: 11 }}
      >
        {c.initials}
      </Avatar>
      <Flex vertical style={{ lineHeight: 1.25, minWidth: 0 }}>
        <Typography.Text ellipsis>{c.name}</Typography.Text>
        {showTitle && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {c.title}
          </Typography.Text>
        )}
      </Flex>
    </Flex>
  );
}
