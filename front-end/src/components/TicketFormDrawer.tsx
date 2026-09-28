import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Drawer,
  Flex,
  Form,
  Input,
  Radio,
  Select,
  Typography,
  Upload,
  type UploadFile,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { api, ApiError } from '@/api/client';
import type { Priority, StationId } from '@/api/types';
import type { DocListItem, TicketListItem } from '@/api/views';
import { useActor } from '@/auth/useAuth';
import { PRIORITIES, PRIORITY } from '@/theme/tokens';
import { crew, stations } from '@/api/lookups';

interface Values {
  title: string;
  description: string;
  priority: Priority;
  station: StationId;
  assigneeId?: string;
  relatedDocId?: string;
  tags?: string[];
}

export function TicketFormDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const actor = useActor();
  const [form] = Form.useForm<Values>();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [files, setFiles] = useState<UploadFile[]>([]);
  const station = Form.useWatch('station', form);
  const priority = Form.useWatch('priority', form);
  const docs = useQuery({
    queryKey: ['documents', 'all-for-select'],
    queryFn: () => api<{ items: DocListItem[] }>('/api/documents'),
    enabled: open,
  });

  const create = useMutation({
    mutationFn: async (v: Values) => {
      const ticket = await api<TicketListItem>('/api/tickets', {
        body: { ...v, title: v.title.trim(), tags: v.tags ?? [] },
      });
      const raws = files.map((f) => f.originFileObj).filter((f): f is NonNullable<typeof f> => !!f);
      if (raws.length) {
        const form = new FormData();
        raws.forEach((r) => form.append('files', r, r.name));
        try {
          await api(`/api/tickets/${ticket.id}/attachments`, { form });
        } catch (e) {
          message.warning(
            `${ticket.id} opened, but the attachments were rejected: ${e instanceof ApiError ? e.message : 'upload failed'}`,
          );
        }
      }
      return ticket;
    },
    onSuccess: (t) => {
      message.success(`${t.id} opened`);
      qc.invalidateQueries({ queryKey: ['tickets'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
      form.resetFields();
      setFiles([]);
      onClose();
      navigate(`/tickets/${t.id}`);
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Could not create ticket'),
  });

  return (
    <Drawer
      title="New ticket"
      open={open}
      onClose={onClose}
      size={560}
      destroyOnHidden
      extra={
        <Button type="primary" loading={create.isPending} onClick={() => form.submit()}>
          Create ticket
        </Button>
      }
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => create.mutate(v)}
        initialValues={{
          priority: 'medium',
          station: actor.station ?? 'prep',
          assigneeId: undefined,
        }}
        requiredMark="optional"
      >
        <Form.Item
          name="title"
          label="Title"
          rules={[{ required: true, min: 5, message: 'At least 5 characters' }]}
        >
          <Input
            placeholder="e.g. Walk-in door gasket torn — not sealing"
            maxLength={120}
            showCount
          />
        </Form.Item>
        <Form.Item
          name="description"
          label="What happened?"
          rules={[{ required: true, message: 'Describe the issue' }]}
        >
          <Input.TextArea
            rows={4}
            placeholder="When did you notice it, what did you check, is any product at risk?"
          />
        </Form.Item>
        <Form.Item
          name="priority"
          label="Priority"
          extra="Anyone can raise Critical. Only a Sous Chef or the Kitchen Manager can lower it later."
        >
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            options={PRIORITIES.map((p) => ({ value: p, label: PRIORITY[p].label }))}
          />
        </Form.Item>
        {priority === 'critical' && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            title="Critical: the station Sous Chef and the Kitchen Manager are notified straight away."
            description="If product may be unsafe, hold it and label it “DO NOT USE” now — don’t wait for a reply."
          />
        )}
        <Flex gap={12}>
          <Form.Item
            name="station"
            label="Station"
            style={{ flex: 1 }}
            rules={[{ required: true }]}
          >
            <Select options={stations().map((s) => ({ value: s.id, label: s.name }))} />
          </Form.Item>
          <Form.Item name="assigneeId" label="Assignee" style={{ flex: 1 }}>
            <Select
              allowClear
              placeholder="Unassigned"
              showSearch={{ optionFilterProp: 'label' }}
              options={crew()
                .filter((c) => !station || c.station === station || c.station === null)
                .map((c) => ({ value: c.id, label: `${c.name} · ${c.title}` }))}
            />
          </Form.Item>
        </Flex>
        <Form.Item
          name="relatedDocId"
          label="Related SOP or document"
          extra="Linking the owning document powers the ownership audit and the triage risk checks."
        >
          <Select
            allowClear
            showSearch={{ optionFilterProp: 'label' }}
            placeholder="Search documents"
            loading={docs.isLoading}
            options={(docs.data?.items ?? []).map((d) => ({
              value: d.id,
              label: `${d.id} · ${d.title}`,
            }))}
          />
        </Form.Item>
        <Form.Item name="tags" label="Tags">
          <Select mode="tags" placeholder="food-safety, equipment, supply…" />
        </Form.Item>
        <Form.Item
          label="Attachments"
          extra="Photos, PDFs or Word files · up to 20 MB each. Attachments stay on the ticket; they are not added to the knowledge base."
        >
          <Upload
            listType="picture-card"
            accept=".png,.jpg,.jpeg,.pdf,.docx,.xlsx,.txt"
            fileList={files}
            beforeUpload={(f) => {
              if (f.size > 20 * 1024 * 1024) {
                message.error(`${f.name} is larger than 20 MB`);
                return Upload.LIST_IGNORE;
              }
              const url = f.type.startsWith('image/') ? URL.createObjectURL(f) : undefined;
              setFiles((prev) => [
                ...prev,
                {
                  uid: f.uid,
                  name: f.name,
                  size: f.size,
                  type: f.type,
                  status: 'done',
                  thumbUrl: url,
                  url,
                  originFileObj: f,
                },
              ]);
              return false;
            }}
            onChange={({ fileList }) =>
              setFiles((prev) => prev.filter((p) => fileList.some((f) => f.uid === p.uid)))
            }
            onRemove={(f) => setFiles((prev) => prev.filter((x) => x.uid !== f.uid))}
          >
            <button type="button" style={{ border: 0, background: 'none', cursor: 'pointer' }}>
              <PlusOutlined />
              <div style={{ marginTop: 8 }}>Add file</div>
            </button>
          </Upload>
        </Form.Item>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Tickets are visible to everyone in the kitchen.
        </Typography.Text>
      </Form>
    </Drawer>
  );
}
