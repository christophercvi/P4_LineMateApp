import { useState } from 'react';
import { Alert, App, Button, Form, Input, InputNumber, Modal, Select, Typography } from 'antd';
import { KeyOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '@/api/client';
import type { ServiceToken } from '@/api/types';

type CreatedToken = ServiceToken & { token: string };
type FormValues = {
  name: string;
  client: string;
  scopes: ('mcp:read' | 'mcp:write')[];
  days: number;
};

/** Admin form that issues an MCP service token. The plain token is returned once and only shown here. */
export function IssueTokenButton() {
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<CreatedToken | null>(null);
  const [form] = Form.useForm<FormValues>();

  const issue = useMutation({
    mutationFn: (v: FormValues) => api<CreatedToken>('/api/mcp/tokens', { body: v }),
    onSuccess: (t) => {
      setCreated(t);
      qc.invalidateQueries({ queryKey: ['mcp'] });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Could not issue the token'),
  });

  const close = () => {
    setOpen(false);
    setCreated(null);
    form.resetFields();
  };

  return (
    <>
      <Button type="primary" icon={<KeyOutlined />} onClick={() => setOpen(true)}>
        Issue token
      </Button>
      <Modal
        open={open}
        title="Issue an MCP service token"
        onCancel={close}
        okText={created ? 'Done' : 'Issue token'}
        onOk={() => (created ? close() : form.submit())}
        confirmLoading={issue.isPending}
        cancelButtonProps={{ style: created ? { display: 'none' } : undefined }}
        destroyOnHidden
      >
        {created ? (
          <>
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              title="Copy this token now"
              description="Only an Argon2 hash is stored, so the token cannot be shown again. Send it as “Authorization: Bearer …” to the /mcp endpoint."
            />
            <Typography.Paragraph
              copyable={{ text: created.token }}
              code
              style={{ wordBreak: 'break-all' }}
            >
              {created.token}
            </Typography.Paragraph>
            <Typography.Text type="secondary">
              {created.name} · {created.scopes.join(', ')} · expires{' '}
              {new Date(created.expiresAt).toLocaleDateString()}
            </Typography.Text>
          </>
        ) : (
          <Form
            form={form}
            layout="vertical"
            initialValues={{ scopes: ['mcp:read'], days: 90 }}
            onFinish={(v) => issue.mutate(v)}
            requiredMark={false}
          >
            <Form.Item
              name="name"
              label="Token name"
              extra="Lowercase letters, numbers and dashes, e.g. pos-integration"
              rules={[
                {
                  required: true,
                  pattern: /^[a-z0-9][a-z0-9-]{2,59}$/,
                  message: 'Use 3–60 lowercase letters, numbers or dashes',
                },
              ]}
            >
              <Input placeholder="pos-integration" />
            </Form.Item>
            <Form.Item
              name="client"
              label="Client"
              rules={[
                { required: true, min: 2, message: 'Name the client that will use this token' },
              ]}
            >
              <Input placeholder="Toast POS sync job" />
            </Form.Item>
            <Form.Item
              name="scopes"
              label="Scopes"
              rules={[{ required: true, message: 'Pick at least one scope' }]}
            >
              <Select
                mode="multiple"
                options={[
                  {
                    value: 'mcp:read',
                    label: 'mcp:read — search documents, list tickets, analytics',
                  },
                  { value: 'mcp:write', label: 'mcp:write — create tickets and request approvals' },
                ]}
              />
            </Form.Item>
            <Form.Item name="days" label="Valid for (days)" rules={[{ required: true }]}>
              <InputNumber min={1} max={365} style={{ width: 140 }} />
            </Form.Item>
          </Form>
        )}
      </Modal>
    </>
  );
}
