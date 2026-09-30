import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Card,
  Col,
  DatePicker,
  Flex,
  Progress,
  Row,
  Select,
  Skeleton,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { Column, Heatmap, Pie } from '@ant-design/charts';
import type { Dayjs } from 'dayjs';
import { api } from '@/api/client';
import type { Priority, TicketStatus, WorkloadRow } from '@/api/types';
import type { WorkloadView } from '@/api/views';
import { useUi } from '@/auth/store';
import { PageHeader } from '@/components/PageHeader';
import { StationTag } from '@/components/tags';
import { PRIORITIES, PRIORITY, STATUS, STATUSES } from '@/theme/tokens';
import { stations, stationName } from '@/api/lookups';

export default function Analytics() {
  const mode = useUi((s) => s.mode);
  const theme = mode === 'dark' ? 'classicDark' : 'classic';
  const [statuses, setStatuses] = useState<TicketStatus[]>(['open', 'in_progress', 'blocked']);
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const q = useQuery({
    queryKey: ['workload', statuses, range?.[0]?.toISOString(), range?.[1]?.toISOString()],
    queryFn: () =>
      api<WorkloadView>('/api/analytics/workload', {
        query: {
          statuses,
          from: range?.[0]?.startOf('day').toISOString(),
          to: range?.[1]?.endOf('day').toISOString(),
        },
      }),
  });

  if (!q.data) return <Skeleton active paragraph={{ rows: 14 }} />;
  const v = q.data;
  const names = stations().map((s) => s.name);
  const colors = stations().map((s) => s.color);
  const stacked = v.rows.flatMap((r) =>
    PRIORITIES.map((p) => ({
      station: stationName(r.station),
      priority: PRIORITY[p].label,
      count: r[p],
    })),
  );
  const pie = v.rows.map((r) => ({ station: stationName(r.station), open: r.open }));
  const flagged = v.rows.filter((r) => r.flagged);

  return (
    <>
      <PageHeader
        title="Workload analytics"
        subtitle={`${v.scope} · ${v.readOnly ? 'read-only for Admins · ' : ''}load index = station open tickets ÷ mean across stations (${v.mean})`}
        extra={
          <>
            <DatePicker.RangePicker
              value={range}
              onChange={(r) => setRange(r)}
              allowEmpty={[true, true]}
              placeholder={['Opened from', 'to']}
            />
            <Select
              mode="multiple"
              style={{ minWidth: 260 }}
              value={statuses}
              onChange={setStatuses}
              maxTagCount="responsive"
              options={STATUSES.map((s) => ({ value: s, label: STATUS[s].label }))}
              placeholder="Statuses"
            />
          </>
        }
      />
      {flagged.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          title={`${flagged.map((r) => stationName(r.station)).join(', ')} ${flagged.length > 1 ? 'are' : 'is'} carrying more than 1.5× the average load`}
          description={flagged
            .map(
              (r) =>
                `${stationName(r.station)}: ${r.open} open (${r.share}% of all), ${r.critical} Critical and ${r.high} High — load index ${r.loadIndex.toFixed(2)}`,
            )
            .join(' · ')}
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Card title="Open tickets by station and priority">
            <Column
              data={stacked}
              xField="station"
              yField="count"
              colorField="priority"
              stack
              height={320}
              theme={theme}
              scale={{
                color: {
                  domain: PRIORITIES.map((p) => PRIORITY[p].label),
                  range: PRIORITIES.map((p) => PRIORITY[p].hex),
                },
              }}
              axis={{ x: { title: false }, y: { title: 'tickets' } }}
              legend={{ color: { position: 'top' } }}
              style={{ maxWidth: 64 }}
            />
          </Card>
        </Col>
        <Col xs={24} xl={10}>
          <Card title="Share of open tickets">
            <Pie
              data={pie}
              angleField="open"
              colorField="station"
              innerRadius={0.6}
              height={320}
              theme={theme}
              scale={{ color: { domain: names, range: colors } }}
              label={{
                text: (d: { station: string; open: number }) => (d.open ? `${d.open}` : ''),
                position: 'outside',
              }}
              legend={{ color: { position: 'right' } }}
              annotations={[
                {
                  type: 'text',
                  style: {
                    text: `${pie.reduce((s, p) => s + p.open, 0)}\nopen`,
                    x: '50%',
                    y: '50%',
                    textAlign: 'center',
                    fontSize: 22,
                    fontWeight: 600,
                  },
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={14}>
          <Card title="Tickets opened per day · last 30 days">
            <Column
              data={v.perDay}
              xField="date"
              yField="count"
              colorField="station"
              stack
              height={280}
              theme={theme}
              scale={{ color: { domain: names, range: colors } }}
              axis={{
                x: {
                  title: false,
                  labelFormatter: (d: string) => d.slice(5),
                  labelFilter: (_: unknown, i: number) => i % 5 === 0,
                },
                y: { title: false, tickCount: 4 },
              }}
              style={{ radiusTopLeft: 2, radiusTopRight: 2 }}
              legend={{ color: { position: 'top' } }}
            />
          </Card>
        </Col>
        <Col xs={24} xl={10}>
          <Card title="Station × priority heat map">
            <Heatmap
              data={stacked}
              xField="priority"
              yField="station"
              colorField="count"
              mark="cell"
              height={300}
              theme={theme}
              scale={{
                x: { domain: PRIORITIES.map((p: Priority) => PRIORITY[p].label) },
                y: { domain: names },
                color: {
                  range:
                    mode === 'dark'
                      ? ['#2b1d16', '#C2410C', '#FF7A45']
                      : ['#FFF4EC', '#F59E0B', '#C2410C'],
                },
              }}
              style={{ inset: 1 }}
              label={{
                text: 'count',
                position: 'inside',
                style: { fill: mode === 'dark' ? '#fff' : '#1f1f1f', fontWeight: 600 },
              }}
              tooltip={{
                title: (d: { station: string }) => d.station,
                items: [
                  { field: 'priority', name: 'Priority' },
                  { field: 'count', name: 'Open tickets' },
                ],
              }}
              axis={{ x: { title: false }, y: { title: false } }}
              legend={false}
            />
          </Card>
        </Col>
        <Col span={24}>
          <Card title="Load index" styles={{ body: { padding: 0 } }}>
            <Table<WorkloadRow>
              rowKey="station"
              pagination={false}
              dataSource={[...v.rows].sort((a, b) => b.loadIndex - a.loadIndex)}
              scroll={{ x: 800 }}
              columns={[
                { title: 'Station', dataIndex: 'station', render: (s) => <StationTag value={s} /> },
                { title: 'Open', dataIndex: 'open', align: 'right' },
                {
                  title: 'Critical',
                  dataIndex: 'critical',
                  align: 'right',
                  render: (n) => (n ? <Tag color="red">{n}</Tag> : 0),
                },
                {
                  title: 'High',
                  dataIndex: 'high',
                  align: 'right',
                  render: (n) => (n ? <Tag color="orange">{n}</Tag> : 0),
                },
                {
                  title: 'Weighted',
                  dataIndex: 'weighted',
                  align: 'right',
                  render: (n) => (
                    <Tooltip title="Critical ×4, High ×3, Medium ×2, Low ×1">{n}</Tooltip>
                  ),
                },
                {
                  title: 'Share',
                  dataIndex: 'share',
                  render: (s: number) => (
                    <Progress
                      percent={s}
                      format={(p) => `${p}%`}
                      size="small"
                      style={{ width: 160 }}
                    />
                  ),
                },
                {
                  title: 'Load index',
                  dataIndex: 'loadIndex',
                  render: (n: number, r) => (
                    <Flex gap={8} align="center">
                      <Typography.Text strong className="lm-num">
                        {n.toFixed(2)}×
                      </Typography.Text>
                      {r.flagged ? (
                        <Tag color="error">Over 1.5× mean</Tag>
                      ) : (
                        <Tag color="success">OK</Tag>
                      )}
                    </Flex>
                  ),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </>
  );
}
